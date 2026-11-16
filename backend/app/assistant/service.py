import logging
import time
import traceback
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from ag_ui.core import BaseEvent, EventType, RunAgentInput, RunErrorEvent
from ag_ui_langgraph import LangGraphAgent
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from app.assistant.nodes import AssistantNodes, _render_history
from app.assistant.repository import AssistantLogRepository
from app.assistant.schemas import AssistantLogEntry, ChatRequest, ChatResponse
from app.knowledge_base.schemas import SearchResult

logger = logging.getLogger(__name__)

# Per-node duration/token labels reused by both the response mapping and the log formatter.
_NODE_KEYS = ("orchestrator", "rephrase", "retrieve", "judge", "chat")


def _format_log(
    session_id: str,
    state: dict[str, Any],
    request_duration: float,
    user_email: str,
    error: str,
) -> str:
    """Build the single per-request `[ASSISTANT]` log block from the final graph state.

    Scalar fields come first (with `error` right under `user`, so a failure jumps out); the
    (long) chunk dump is last so the rest stays readable. Token counts are one line each, with a
    unique label per value, so coarse CloudWatch queries can isolate a single metric (e.g. all
    `chat_input_tokens`). `state` may be partial (on a failed turn it is read back from the
    checkpointer up to the last completed node), so every field is read defensively.
    """
    relevant: list[SearchResult] = state.get("relevant_documents", [])
    chunks = "\n".join(
        f"<doc {i}>\nDocument title: {doc.document_title}\n{doc.content}\n</doc {i}>"
        for i, doc in enumerate(relevant, start=1)
    )
    messages = state.get("messages", [])
    # On success, messages ends with current_human + current_ai — drop both to get prior turns.
    # On error, only current_human was appended (no AI reply) — drop just that one.
    history = (
        messages[:-2]
        if (not error and len(messages) >= 2)
        else messages[:-1]
        if messages
        else []
    )
    return (
        "\n══════════════════ [ASSISTANT] ══════════════════\n"
        f">> session_id             : {session_id}\n"
        f">> user                   : {user_email}\n"
        f">> error                  : {error or 'none'}\n"
        f">> request_duration       : {request_duration:.2f}s\n"
        f">> orchestrator_duration  : {state.get('orchestrator_duration', 0.0):.2f}s\n"
        f">> rephrase_duration      : {state.get('rephrase_duration', 0.0):.2f}s\n"
        f">> retrieve_duration      : {state.get('retrieve_duration', 0.0):.2f}s\n"
        f">> judge_duration         : {state.get('judge_duration', 0.0):.2f}s\n"
        f">> chat_duration          : {state.get('chat_duration', 0.0):.2f}s\n"
        f">> route                  : {state.get('route')}\n"
        f">> orch_input_tokens      : {state.get('orchestrator_input_tokens', 0)}\n"
        f">> orch_output_tokens     : {state.get('orchestrator_output_tokens', 0)}\n"
        f">> rephrase_input_tokens  : {state.get('rephrase_input_tokens', 0)}\n"
        f">> rephrase_output_tokens : {state.get('rephrase_output_tokens', 0)}\n"
        f">> judge_input_tokens     : {state.get('judge_input_tokens', 0)}\n"
        f">> judge_output_tokens    : {state.get('judge_output_tokens', 0)}\n"
        f">> chat_input_tokens      : {state.get('chat_input_tokens', 0)}\n"
        f">> chat_output_tokens     : {state.get('chat_output_tokens', 0)}\n"
        f">> relevant_chunks        : {len(relevant)}\n"
        f">> question               : {state.get('question')}\n"
        f">> rephrased_question     : {state.get('rephrased_question')}\n"
        f">> answer                 : {state.get('answer')}\n"
        f">> chat_history           : {_render_history(history)}\n"
        f"\n{chunks}\n"
        "═════════════════════════════════════════════════════"
    )


def _format_judge_debug(state: dict[str, Any]) -> str:
    """Build the DEBUG-only block with the judge's per-chunk verdict and reasoning.

    One line per retrieved chunk (kept or discarded), so it is clear *why* the judge dropped a
    chunk. Only emitted when DEBUG is enabled (hidden in production).
    """
    verdicts: list[dict] = state.get("judge_verdicts", [])
    lines = "\n".join(
        f"  [{i}] {'RELEVANT ' if v['is_relevant'] else 'DISCARDED'} | "
        f"{v.get('document_title')} (p.{v.get('page_number')})\n"
        f"      reasoning: {v.get('reasoning')}"
        for i, v in enumerate(verdicts, start=1)
    )
    return (
        "\n──────── [ASSISTANT JUDGE] ────────\n"
        f"{lines}\n"
        "───────────────────────────────────"
    )


class AssistantService:
    """Business logic for the conversational assistant — the runner for both transports.

    Both transports run the SAME compiled graph (keyed by the session id; the checkpointer holds
    the in-memory conversation history). `chat` runs it materialized (`graph.ainvoke`) and maps
    the final state to the response DTO for the JSON transport; `stream` wraps the same graph in
    the AG-UI adapter and yields its events for the SSE transport. This service is also reused
    in-process by the evaluation feature (which uses `chat`).

    **Logging + errors.** The single per-request `[ASSISTANT]` log lives here (`_log`), not in the
    graph: it is the one point both transports share, and — unlike the `finalize` node — it is
    reachable even when a node raises and aborts the turn before `finalize` runs. Each transport
    wraps its run in `try/except/finally`; the OK and KO paths converge on the same `_log`, the
    only difference being the `error` field (empty on success, the traceback on failure — which
    already names the node that failed). On a fatal error the user gets a generic message
    (`AssistantNodes._ERROR_MESSAGE`). The document-reference extraction stays in the graph's
    `finalize` node because the streaming transport ships `references` inside the STATE_SNAPSHOT.
    """

    def __init__(
        self, graph: CompiledStateGraph, log_repository: AssistantLogRepository
    ):
        self.graph = graph
        self._log_repository = log_repository

    def _compute_duration(self, state: dict[str, Any]) -> float:
        return time.perf_counter() - state.get("started_at", time.perf_counter())

    async def _log(
        self,
        session_id: str,
        user_email: str,
        state: dict[str, Any],
        error: str,
        duration: float,
        message_id: str | None = None,
    ) -> None:
        """Emit the single per-request `[ASSISTANT]` log block (both transports, OK and KO).

        `logger.error` when the turn failed (so alerting can key off the level), `logger.info`
        otherwise — the only content difference between the two is the `error` field. The verbose
        judge reasoning goes to DEBUG (gated so the string is built only when enabled). The same
        data is also persisted to `oneknowledgedoor_log` (best-effort, see `_persist`).
        """
        block = _format_log(session_id, state, duration, user_email, error)
        if error:
            logger.error(block)
        else:
            logger.info(block)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(_format_judge_debug(state))
        await self._persist(session_id, user_email, state, duration, error, message_id)

    async def _persist(
        self,
        session_id: str,
        user_email: str,
        state: dict[str, Any],
        duration: float,
        error: str,  # used to determine how many messages to strip for chat_history
        message_id: str | None = None,
    ) -> None:
        """Persist the log row to `oneknowledgedoor_log` — best-effort.

        A failure here (DB down, aborted transaction, malformed row) must never break the request
        nor mask the original error, so it is swallowed with a WARNING. The state→row mapping is
        the DTO's `_from_state` validator; `chat_history` is rendered here (reusing the shared
        `_render_history`) to keep the schema free of a `nodes` import.
        """
        try:
            messages = state.get("messages", [])
            history = (
                messages[:-2]
                if (not error and len(messages) >= 2)
                else messages[:-1]
                if messages
                else []
            )
            entry = AssistantLogEntry.model_validate(
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "user_email": user_email,
                    "error": error,
                    "request_duration": duration,
                    "chat_history": _render_history(history),
                    "state": state,
                }
            )
            await self._log_repository.save(entry)
        except Exception:
            logger.warning("Persisting the assistant log failed", exc_info=True)

    async def chat(self, request: ChatRequest, user_email: str) -> ChatResponse:
        # `thread_id` keys the checkpointer (conversation history); `user_email` is logged for
        # audit. Only the new message is seeded — the `reset` node captures `question` and clears
        # the per-turn derived fields, and `finalize` fills `references`. On a fatal error the
        # state is read back from the checkpointer (partial, up to the last completed node) so the
        # log still carries the fields produced before the failure.
        config = {
            "configurable": {"thread_id": request.session_id, "user_email": user_email}
        }
        message_id = str(uuid4())
        error = ""
        state: dict[str, Any] = {}
        try:
            state = await self.graph.ainvoke(
                {"messages": [HumanMessage(request.message)]}, config=config
            )
        except Exception:  # noqa: BLE001
            error = traceback.format_exc()
            state = (await self.graph.aget_state(config)).values
        finally:
            duration = self._compute_duration(state)
            await self._log(
                request.session_id, user_email, state, error, duration, message_id
            )
        if error:
            return ChatResponse(
                answer=AssistantNodes._ERROR_MESSAGE,
                references=[],
                relevant_chunks=0,
                rephrased_question=None,
                durations={key: 0.0 for key in _NODE_KEYS},
                request_duration=0.0,
                session_id=request.session_id,
                message_id=message_id,
            )
        return ChatResponse(
            answer=state["answer"],
            references=state["references"],
            relevant_chunks=len(state["relevant_documents"]),
            rephrased_question=state.get("rephrased_question"),
            durations={key: state.get(f"{key}_duration", 0.0) for key in _NODE_KEYS},
            request_duration=duration,
            session_id=request.session_id,
            message_id=message_id,
        )

    async def stream(
        self, input_data: RunAgentInput, user_email: str
    ) -> AsyncGenerator[BaseEvent]:
        # Streaming transport: wrap the SAME compiled graph in the AG-UI adapter and yield its
        # events. Built per call (this service is REQUEST-scoped). The trusted forwarded email
        # goes into the run config here (never the client payload), so `_log` records it for
        # audit — identical to the JSON path. `thread_id` is set explicitly (same value the
        # adapter would take from `RunAgentInput`) because `_log` reads the final state back from
        # the checkpointer with it — the generator has no return value. A fatal error is closed
        # protocol-conformantly with a RUN_ERROR carrying the generic message.
        config = {
            "configurable": {
                "thread_id": input_data.thread_id,
                "user_email": user_email,
            }
        }
        agent = LangGraphAgent(name="okd-assistant", graph=self.graph, config=config)
        error = ""
        try:
            async for event in agent.run(input_data):
                yield event
        except Exception:  # noqa: BLE001
            error = traceback.format_exc()
            yield RunErrorEvent(
                type=EventType.RUN_ERROR, message=AssistantNodes._ERROR_MESSAGE
            )
        finally:
            state = (await self.graph.aget_state(config)).values
            duration = self._compute_duration(state)
            await self._log(
                input_data.thread_id,
                user_email,
                state,
                error,
                duration,
                input_data.run_id,
            )
