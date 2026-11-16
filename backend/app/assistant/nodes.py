from __future__ import annotations

import asyncio
import functools
import logging
import time
from importlib import resources
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import uuid4

import yaml
from langchain_aws import ChatBedrockConverse
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.assistant.glossary import expand_query
from app.assistant.schemas import (
    DocumentReference,
    RelevanceVerdict,
    RephrasedQuery,
    RouteDecision,
)
from app.knowledge_base.repository import KnowledgeBaseRepository
from app.knowledge_base.schemas import SearchQuery, SearchResult

if TYPE_CHECKING:
    # Imported only for typing to avoid a circular import with graph.py (which imports this
    # module to build the graph). AssistantState lives in graph.py.
    from app.assistant.graph import AssistantState

logger = logging.getLogger(__name__)

# Custom-event name used by the `stream_answer` node to signal a pre-computed answer to the
# streaming transport. Module-level because it is the contract shared with `routes.py`, which
# imports it to expand the signal into AG-UI text deltas (a genuinely cross-module value).
STREAM_TEXT_EVENT = "okd_stream_text"


def _timed(field: str):
    """Decorate an async node method to record its wall-clock duration into the state update.

    Measures `time.perf_counter()` around the wrapped call and injects `{field}_duration`
    (seconds) into the returned partial-state dict, covering every return path (e.g. the
    orchestrate early-return) without duplicating timing code in each branch.
    """

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(self, state, *args, **kwargs):
            start = time.perf_counter()
            update = await fn(self, state, *args, **kwargs)
            update[f"{field}_duration"] = time.perf_counter() - start
            return update

        return wrapper

    return decorator


class AssistantNodes:
    """LangGraph nodes for the conversational assistant.

    Each node captures the injected chat models and knowledge-base repository and returns a
    partial state update. The `reset` node clears the per-turn derived fields at the head of
    every turn; the `finalize` node closes every path, extracting the document references. The
    single per-request `[ASSISTANT]` log is emitted by `AssistantService` (one point for both
    transports, reachable even when a node aborts before `finalize`), not by a node here. The
    adjacent-chunk window is still to be added in a later step.
    """

    @staticmethod
    def _load_prompts() -> dict[str, str]:
        _required = {
            "orchestrator_system",
            "conversation_user",
            "rephrase_system",
            "judge_system",
            "judge_user",
            "chat_system",
            "out_of_scope_message",
            "error_message",
        }
        raw = yaml.safe_load(
            resources.files(__package__).joinpath("prompts.yaml").read_text("utf-8")
        )
        missing = _required - raw.keys()
        if missing:
            raise ValueError(f"prompts.yaml: missing required keys: {missing}")
        return {key: str(value).rstrip("\n") for key, value in raw.items()}

    _P: ClassVar[dict[str, str]] = _load_prompts.__func__()  # type: ignore[attr-defined]
    _ORCHESTRATOR_SYSTEM: ClassVar[str] = _P["orchestrator_system"]
    _CONVERSATION_USER: ClassVar[str] = _P["conversation_user"]
    _REPHRASE_SYSTEM: ClassVar[str] = _P["rephrase_system"]
    _JUDGE_SYSTEM: ClassVar[str] = _P["judge_system"]
    _JUDGE_USER: ClassVar[str] = _P["judge_user"]
    _CHAT_SYSTEM: ClassVar[str] = _P["chat_system"]
    _OUT_OF_SCOPE_MESSAGE: ClassVar[str] = _P["out_of_scope_message"]
    # User-facing reply shown by the service when a turn fails fatally (see AssistantService).
    _ERROR_MESSAGE: ClassVar[str] = _P["error_message"]

    def __init__(
        self,
        orchestrator_llm: ChatBedrockConverse,
        rephrase_llm: ChatBedrockConverse,
        judge_llm: ChatBedrockConverse,
        chat_llm: ChatBedrockConverse,
        kb_repository: KnowledgeBaseRepository,
    ):
        self.chat_llm = chat_llm
        self.kb_repository = kb_repository
        self.orchestrator_llm = orchestrator_llm.with_structured_output(
            RouteDecision, include_raw=True
        )
        self.rephrase_llm = rephrase_llm.with_structured_output(
            RephrasedQuery, include_raw=True
        )
        self.judge_llm = judge_llm.with_structured_output(
            RelevanceVerdict, include_raw=True
        )

    async def reset(self, state: AssistantState) -> dict[str, Any]:
        """Clear the per-turn derived state at the head of every turn.

        The checkpointer persists the WHOLE state across turns and only `messages` has a
        reducer — every other field is last-write-wins. A chit-chat turn ends without visiting
        rephrase/retrieve/judge/chat, so without this reset it would inherit the previous
        knowledge-query turn's derived fields (stale citations, token counters), leaking them
        into the response and the log. Runs first (before orchestrate) so downstream nodes and
        the finalize log see a clean slate. Does not touch `messages` (the input, with its
        reducer). Also captures the latest user message as `question` (so the finalize log is
        correct for both transports) and stamps `started_at` for the finalize duration.
        """
        return {
            "question": state["messages"][-1].text(),
            "route": "",
            "rephrased_question": "",
            "documents": [],
            "relevant_documents": [],
            "judge_verdicts": [],
            "references": [],
            "answer": "",
            "orchestrator_input_tokens": 0,
            "orchestrator_output_tokens": 0,
            "rephrase_input_tokens": 0,
            "rephrase_output_tokens": 0,
            "judge_input_tokens": 0,
            "judge_output_tokens": 0,
            "chat_input_tokens": 0,
            "chat_output_tokens": 0,
            "orchestrator_duration": 0.0,
            "rephrase_duration": 0.0,
            "retrieve_duration": 0.0,
            "judge_duration": 0.0,
            "chat_duration": 0.0,
            "started_at": time.perf_counter(),
        }

    @_timed("orchestrator")
    async def orchestrate(self, state: AssistantState) -> dict[str, Any]:
        """Classify the conversation and, for chit-chat, produce the reply in one LLM call.

        Runs first on every turn. It classifies the WHOLE conversation (not just the latest
        message) as chit-chat or a real knowledge query — the latest turn can be a short reply
        to a clarification the assistant asked, meaningful only in context. On chit-chat it also
        writes the courteous answer to `answer` (for the log) but does NOT touch `messages`:
        the terminal `stream_answer` node owns writing the `AIMessage` into the history (with a
        shared id) and re-emitting it as streamed text, so chit-chat goes orchestrate ->
        stream_answer, skipping retrieve/judge/chat. On a knowledge query the flow continues to
        rephrase.

        Fail-safe: on any orchestrator failure, route to `knowledge_query` (safer than replying
        with chit-chat to a real question) — the controlled counterpart of the judge's fail-open.
        Two distinct failure paths converge on it:
        - the HTTP call itself raising (no tokens spent) — caught by the `except`;
        - malformed structured output — because `include_raw=True`, a schema violation (e.g. the
          `RouteDecision` model_validator rejecting a `chit_chat` route with no reply) does NOT
          raise: it is captured as `result["parsing_error"]` with `result["parsed"] is None`.
          That case is handled explicitly (not by the `except`) and logged at ERROR — a silent
          blank turn or a canned reply would hide the malformed output. The request WAS billed
          here, so the token usage from `result["raw"]` is still recorded.
        """
        *history, latest = state["messages"]
        messages = [
            SystemMessage(self._ORCHESTRATOR_SYSTEM),
            HumanMessage(
                self._CONVERSATION_USER.format(
                    history=_render_history(history), question=latest.text()
                )
            ),
        ]
        try:
            result = await self.orchestrator_llm.ainvoke(messages)
        except Exception:
            logger.exception("orchestrator call failed; routing to knowledge_query")
            return {"route": "knowledge_query"}
        raw = result.get("raw")
        usage = (raw.usage_metadata if raw is not None else None) or {}
        tokens = {
            "orchestrator_input_tokens": usage.get("input_tokens", 0),
            "orchestrator_output_tokens": usage.get("output_tokens", 0),
        }
        decision: RouteDecision | None = result["parsed"]
        if decision is None:
            # Malformed structured output: with `include_raw=True` a schema violation (e.g. the
            # `RouteDecision` model_validator rejecting a chit-chat route with no reply) is
            # captured here as `parsed is None` rather than raised. Fail safe to knowledge_query
            # but log loudly — never stream a blank/canned turn over a hidden bad output.
            logger.error(
                "orchestrator returned malformed structured output; routing to "
                "knowledge_query: %s",
                result.get("parsing_error"),
            )
            return {"route": "knowledge_query", **tokens}
        update: dict[str, Any] = {"route": decision.route, **tokens}
        if decision.route == "chit_chat":
            # Only record the answer (for the log); `stream_answer` writes it into `messages`.
            # The model_validator guarantees `chit_chat_response` is non-empty on this route.
            update["answer"] = decision.chit_chat_response
        return update

    @_timed("rephrase")
    async def rephrase(self, state: AssistantState) -> dict[str, Any]:
        """Rewrite the latest user message into a standalone, glossary-expanded question.

        The prior conversation is passed as *rendered text* inside a single instruction
        message (not as a live chat transcript): feeding the accumulated messages — which at
        turn 2+ end with assistant answers followed by a user question — primes the model to
        continue as the assistant and answer instead of rewriting. Here the history is context
        and the latest turn is explicitly isolated as the thing to rewrite, mirroring the judge
        node's single-formatted-message pattern.

        The rephrased question is used only by the retriever (it is not added to `messages`
        nor passed to the chat node), so it is safe to enrich it deterministically: domain
        acronyms get their expanded form appended to bridge the lexical gap with the KB.
        """
        *history, latest = state["messages"]
        messages = [
            SystemMessage(self._REPHRASE_SYSTEM),
            HumanMessage(
                self._CONVERSATION_USER.format(
                    history=_render_history(history), question=latest.text()
                )
            ),
        ]
        result = await self.rephrase_llm.ainvoke(messages)
        raw = result.get("raw")
        usage = (raw.usage_metadata if raw is not None else None) or {}
        verdict: RephrasedQuery | None = result["parsed"]
        if verdict is None:
            # Malformed structured output: with `include_raw=True` a schema violation is
            # captured here as `parsed is None` rather than raised (mirrors `orchestrate`).
            # Fall back to the raw model text so the retriever still gets a usable query — a
            # recognisable, un-rewritten string — but log loudly so the failure is not silent.
            fallback = raw.text() if raw is not None else ""
            logger.error(
                "rephrase returned malformed structured output; falling back to raw text: %s",
                result.get("parsing_error"),
            )
            question = fallback
        else:
            question = verdict.standalone_question
        return {
            "rephrased_question": expand_query(question),
            "rephrase_input_tokens": usage.get("input_tokens", 0),
            "rephrase_output_tokens": usage.get("output_tokens", 0),
        }

    @_timed("retrieve")
    async def retrieve(self, state: AssistantState) -> dict[str, Any]:
        """Hybrid-search the knowledge base with the rephrased question."""
        documents = await self.kb_repository.hybrid_search(
            SearchQuery(query=state["rephrased_question"])
        )
        return {"documents": documents}

    @_timed("judge")
    async def judge(self, state: AssistantState) -> dict[str, Any]:
        """Filter the retrieved chunks by relevance, one LLM call per chunk in parallel.

        The hybrid search can surface false positives over the highly technical KB, so each
        chunk is classified relevant / not relevant against the question; only the relevant
        ones reach the chat node. The per-chunk calls run concurrently via `asyncio.gather`.
        """
        # NOTE DEV switch: uncomment to disable the judge and keep every retrieved chunk.
        # return {"relevant_documents": state["documents"]}
        question = state["rephrased_question"]
        documents = state["documents"]
        results = await asyncio.gather(
            *(self._judge_chunk(question, doc) for doc in documents)
        )
        relevant = [doc for doc, keep, _, _ in results if keep]
        input_tokens = sum(usage.get("input_tokens", 0) for _, _, _, usage in results)
        output_tokens = sum(usage.get("output_tokens", 0) for _, _, _, usage in results)
        # Per-chunk verdicts (incl. discarded), carried in the state for the DEBUG-only log.
        verdicts = [
            {
                "document_title": doc.document_title,
                "page_number": doc.page_number,
                "is_relevant": keep,
                "reasoning": reasoning,
            }
            for doc, keep, reasoning, _ in results
        ]
        return {
            "relevant_documents": relevant,
            "judge_verdicts": verdicts,
            "judge_input_tokens": input_tokens,
            "judge_output_tokens": output_tokens,
        }

    async def _judge_chunk(
        self, question: str, doc: SearchResult
    ) -> tuple[SearchResult, bool, str, dict[str, Any]]:
        """Classify a single chunk. Fail-open: keep the chunk if the call errors out.

        Returns the chunk, the relevance flag, the model's reasoning (kept for the DEBUG log)
        and the raw token usage.
        """
        messages = [
            SystemMessage(self._JUDGE_SYSTEM),
            HumanMessage(
                self._JUDGE_USER.format(
                    question=question,
                    document_title=doc.document_title,
                    document=doc.content,
                )
            ),
        ]
        try:
            result = await self.judge_llm.ainvoke(messages)
        except Exception:  # noqa: BLE001
            return doc, True, "(judge call failed — kept by fail-open)", {}
        verdict: RelevanceVerdict | None = result["parsed"]
        if verdict is None:
            # Malformed structured output (mirrors orchestrate / rephrase): fail-open and keep
            # the chunk so a single bad verdict does not take down the whole asyncio.gather.
            logger.error(
                "judge returned malformed structured output for chunk '%s'; keeping by fail-open: %s",
                doc.document_title,
                result.get("parsing_error"),
            )
            raw = result.get("raw")
            usage = (raw.usage_metadata if raw is not None else None) or {}
            return (
                doc,
                True,
                "(judge returned malformed output — kept by fail-open)",
                usage,
            )
        usage = result["raw"].usage_metadata or {}
        return doc, verdict.is_relevant, verdict.reasoning, usage

    async def fallback(self, state: AssistantState) -> dict[str, Any]:
        """Out-of-scope reply when the judge discarded every chunk.

        Records the predefined message as `answer` only; the terminal `stream_answer` node
        owns writing it into `messages` (with a shared id) and re-emitting it as streamed
        text, exactly like the chit-chat path.
        """
        return {"answer": self._OUT_OF_SCOPE_MESSAGE}

    async def stream_answer(self, state: AssistantState) -> dict[str, Any]:
        """Re-emit a pre-computed answer (chit-chat or out-of-scope) as a streamed text message.

        Both producers bypass the chat-model stream — `orchestrate` emits structured output
        (tool-call chunks) and `fallback` is static text — so neither reaches the AG-UI text
        channel and, on the streaming transport, would never be shown. This node dispatches a
        single 'signal' custom event that the streaming route expands into typewriter text
        deltas, and writes the `AIMessage` into `messages` with the SAME id as the streamed
        message so the final MESSAGES_SNAPSHOT reconciles instead of duplicating. Deterministic,
        no LLM call; on the JSON transport the custom event is simply ignored.
        """
        answer = state["answer"]
        mid = str(uuid4())
        await adispatch_custom_event(
            STREAM_TEXT_EVENT, {"message_id": mid, "message": answer}
        )
        return {"messages": [AIMessage(answer, id=mid)]}

    @_timed("chat")
    async def chat(self, state: AssistantState) -> dict[str, Any]:
        """Generate the grounded answer from the conversation and the relevant chunks."""
        context = "\n\n".join(
            f"<doc {i}>\nDocument title: {doc.document_title}\n{doc.content}\n</doc {i}>"
            for i, doc in enumerate(state["relevant_documents"], start=1)
        )
        messages = [
            SystemMessage(self._CHAT_SYSTEM.format(context=context)),
            *state["messages"],
        ]
        response = await self.chat_llm.ainvoke(messages)
        answer = response.text
        usage = response.usage_metadata or {}
        # The AIMessage is appended to `messages` (add_messages reducer) so it is persisted
        # in the checkpointer and available as history on the next turn.
        return {
            "answer": answer,
            "messages": [AIMessage(answer)],
            "chat_input_tokens": usage.get("input_tokens", 0),
            "chat_output_tokens": usage.get("output_tokens", 0),
        }

    async def finalize(self, state: AssistantState) -> dict[str, Any]:
        """Terminal node (every path converges here): extract the document references.

        References are extracted programmatically (no LLM) from the relevant chunks and written
        to the state, so both transports read one source of truth: the JSON response reads
        `references`, the streaming transport ships it in the STATE_SNAPSHOT. This node MUST stay
        in the graph precisely for that reason — the streaming citations travel inside the graph
        state, so a node has to write them before the snapshot is emitted. The single per-request
        `[ASSISTANT]` log lives in `AssistantService` (one point for both transports, and reachable
        even when a node aborts before this one runs), not here.
        """
        return {"references": self._references(state.get("relevant_documents", []))}

    @staticmethod
    def _references(documents: list[SearchResult]) -> list[DocumentReference]:
        """Collapse the retrieved chunks to document-level citations, order-preserving.

        Chunks coming from the same document are merged into a single reference whose
        `page_number` is the ordered list of every page that contributed to the answer
        (deduplicated) — so no page is lost when a document supplies more than one chunk.
        """
        seen: dict[str | None, DocumentReference] = {}
        for doc in documents:
            key = doc.permalink or doc.document_title
            reference = seen.get(key)
            if reference is None:
                reference = DocumentReference.model_validate(
                    {**doc.model_dump(), "page_number": []}
                )
                seen[key] = reference
            if (
                doc.page_number is not None
                and doc.page_number not in reference.page_number
            ):
                reference.page_number.append(doc.page_number)
        return list(seen.values())


def _render_history(history: list[Any]) -> str:
    """Render the prior turns as labelled plain text.

    Only human/assistant turns carry an antecedent worth resolving; anything else is
    skipped. Returns a placeholder when there is no prior conversation (turn 1). Module-level
    because it is shared: the `rephrase` node uses it to build the prompt, and the service's
    `[ASSISTANT]` log formatter uses it to render `chat_history`.
    """
    lines = []
    for message in history:
        role = "User" if isinstance(message, HumanMessage) else "Assistant"
        lines.append(f"{role}: {message.text()}")
    return "\n".join(lines) or "(no prior conversation)"
