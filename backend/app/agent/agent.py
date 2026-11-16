from __future__ import annotations

import asyncio
import json
import logging
import traceback
from collections.abc import AsyncGenerator
from typing import Annotated, Literal
from uuid import uuid4

from ag_ui.core import BaseEvent, EventType, RunAgentInput, RunErrorEvent
from ag_ui_langgraph import LangGraphAgent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from app.assistant.glossary import expand_query
from app.assistant.schemas import ChatRequest, ChatResponse, RelevanceVerdict
from app.knowledge_base.repository import KnowledgeBaseRepository
from app.knowledge_base.schemas import SearchQuery

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 5
_TOOL_NODE = "tools"
_AGENT_NODE = "agent"
_EXIT_TOOL_NAME = "answer_without_search"

_ERROR_MESSAGE = "Sorry, something went wrong. Please try again."

# Directives returned by `answer_without_search`. The tool hands back an instruction,
# not the final text, so the model still writes the reply in its own voice.
_CHIT_CHAT_INSTRUCTION = (
    "No search was performed. Reply briefly and conversationally in English. "
    "Do not state any fact about [censored]"
)
_OUT_OF_SCOPE_INSTRUCTION = (
    "No search was performed. Tell the user this request is outside the [censored]"
)


class AgentState(TypedDict):
    # `add_messages` is the reducer: new messages are appended, not overwritten.
    # This replaces the `messages` list you'd grow manually in the raw Anthropic loop.
    messages: Annotated[list[BaseMessage], add_messages]


def _make_search_kb_tool(kb_repo: KnowledgeBaseRepository, judge_llm):
    # @tool reads the function signature, type hints and the Args: block in the docstring
    # and builds the JSON schema automatically — equivalent to writing a ToolParam by hand:
    #
    #   ToolParam(name="search_knowledge_base", description="...",
    #             input_schema={"type": "object", "properties": {"query": ..., "category": ...}})
    #
    # The factory pattern (closure) is needed to capture kb_repo and judge_llm, which are
    # injected dependencies — @tool cannot take constructor arguments.
    @tool
    async def search_knowledge_base(query: str, category: str | None = None) -> str:
        """Search internal [censored] documentation. Use this for any question requiring internal knowledge.

        Args:
            query: A standalone English search query (resolve all conversation references).
            category: Optional filter: [censored].
        """
        expanded = expand_query(query)
        results = await kb_repo.hybrid_search(
            SearchQuery(query=expanded, category=category)
        )

        async def _judge(chunk) -> tuple[bool, object]:
            try:
                out = await judge_llm.with_structured_output(
                    RelevanceVerdict, include_raw=True
                ).ainvoke(
                    [
                        {
                            "role": "user",
                            "content": (
                                f"Query: {query}\n\nChunk:\n{chunk.content}\n\n"
                                "Is this chunk relevant to the query? First reason briefly, then decide."
                            ),
                        }
                    ]
                )
                parsed = out.get("parsed") if isinstance(out, dict) else None
                return (parsed.is_relevant if parsed else True), chunk
            except Exception:  # noqa: BLE001
                return True, chunk  # fail-open

        verdicts = await asyncio.gather(*(_judge(c) for c in results))
        kept = [chunk for is_rel, chunk in verdicts if is_rel]

        if not kept:
            return '{"count": 0, "chunks": [], "hint": "No relevant content found. Try a broader query or a different category filter."}'

        return json.dumps(
            {
                "count": len(kept),
                "chunks": [
                    {
                        "document_title": c.document_title,
                        "category": c.category,
                        "page_number": c.page_number,
                        "content": c.content,
                        "permalink": c.permalink,
                    }
                    for c in kept
                ],
            }
        )

    return search_knowledge_base


# No injected dependency, so no closure factory is needed: a plain module-level @tool.
@tool
async def answer_without_search(reason: Literal["chit_chat", "out_of_scope"]) -> str:
    """Answer directly. Use when the question needs no internal [censored] documentation.

    Args:
        reason: "chit_chat" for greetings, small talk, farewells or questions about your
            own capabilities. "out_of_scope" for requests clearly [censored].
    """
    # This tool is the legitimate exit from the forced tool choice: with tool_choice="any"
    # the model MUST call something as its first action, so "do not search" has to be a
    # tool of its own. Compared to tool_choice="auto" the routing decision becomes an
    # explicit, typed, logged event instead of an implicit choice you can only infer.
    if reason == "out_of_scope":
        return _OUT_OF_SCOPE_INSTRUCTION
    return _CHIT_CHAT_INSTRUCTION


def _last_turn_index(messages: list[BaseMessage]) -> int:
    """Index of the last HumanMessage — where the current turn starts."""
    return next(
        (
            i
            for i, m in reversed(list(enumerate(messages)))
            if isinstance(m, HumanMessage)
        ),
        0,
    )


def _prune_history(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Strip tool traffic from *previous* turns; leave the current turn untouched.

    The checkpointer accumulates every message, ToolMessages included, so without this
    the model would still see the chunks retrieved in earlier turns and could answer a
    follow-up from stale context instead of searching again. Dropping the `tool_use`
    AIMessage and its `tool_result` together keeps the sequence valid for Bedrock.
    """
    start = _last_turn_index(messages)
    history = [
        m
        for m in messages[:start]
        if not isinstance(m, ToolMessage) and not getattr(m, "tool_calls", None)
    ]
    return [*history, *messages[start:]]


class AgentNodes:
    """Stateless node methods for the agent graph.

    `agent_step` and `tools_step` together implement one iteration of the Anthropic
    tool-use while loop. The class exists only to share the bound models and `_tool_map`
    between the two methods without globals.
    """

    _SYSTEM_PROMPT: str = (
        "[censored]"
    )

    def __init__(self, llm, tools: list, max_iterations: int = _MAX_ITERATIONS) -> None:
        # Three variants of the same model, picked per iteration by `agent_step`:
        #   _llm_any  → toolChoice={"any": {}}  — a tool call is mandatory
        #   _llm_auto → toolChoice={"auto": {}} — the model decides
        #   _llm      → no toolConfig at all    — a tool call is impossible
        # The raw model is the guardrail: Bedrock Converse has no toolChoice "none" and
        # rejects an empty `tools` list, so "stop calling tools" can only be expressed by
        # not sending the tool schema at all.
        self._llm = llm
        self._llm_any = llm.bind_tools(
            tools, tool_choice="auto"
        )  # any to force the tool_use
        self._llm_auto = llm.bind_tools(tools, tool_choice="auto")
        self._tool_map = {t.name: t for t in tools}
        self.max_iterations = max_iterations

    async def agent_step(self, state: AgentState) -> dict:
        """Call the LLM in the raw loop."""
        # Count ToolMessages since the last HumanMessage to enforce the per-turn cap.
        tool_calls_this_turn = 0
        for m in reversed(state["messages"]):
            if isinstance(m, HumanMessage):
                break
            if isinstance(m, ToolMessage):
                tool_calls_this_turn += 1

        if tool_calls_this_turn == 0:
            llm = self._llm_any
        elif tool_calls_this_turn < self.max_iterations:
            llm = self._llm_auto
        else:
            llm = self._llm

        messages = [
            {"role": "system", "content": self._SYSTEM_PROMPT},
            *_prune_history(state["messages"]),
        ]
        response = await llm.ainvoke(messages)
        return {"messages": [response]}

    async def tools_step(self, state: AgentState) -> dict:
        """Execute all tool calls from the last AIMessage`.
        Each result becomes a ToolMessage, which LangChain serialises to the `tool_result`
        content block that the Anthropic API expects on the next turn.
        """
        last = state["messages"][-1]
        tool_calls = list(last.tool_calls)

        # With tool_choice="any" the model may emit several tool calls at once. If it asks
        # for a real search AND the exit tool in the same response the search wins — but
        # Bedrock still demands a tool_result for every tool_use, so the discarded call
        # gets a stub answer instead of simply being dropped.
        names = {tc["name"] for tc in tool_calls}
        ignored = (
            {tc["id"] for tc in tool_calls if tc["name"] == _EXIT_TOOL_NAME}
            if _EXIT_TOOL_NAME in names and len(names) > 1
            else set()
        )

        async def _run(tc) -> ToolMessage:
            if tc["id"] in ignored:
                return ToolMessage(
                    content="Ignored: a knowledge base search was performed instead.",
                    tool_call_id=tc["id"],
                )
            fn = self._tool_map[tc["name"]]
            try:
                result = await fn.ainvoke(tc["args"])
            except Exception as exc:  # noqa: BLE001
                result = f"Tool error: {exc}"
            return ToolMessage(content=str(result), tool_call_id=tc["id"])

        results = await asyncio.gather(*(_run(tc) for tc in tool_calls))
        return {"messages": list(results)}


def _route_after_agent(state: AgentState) -> str:
    """Decide whether to loop or stop — equivalent to `if response.stop_reason != "tool_use": break`.
    If the last AIMessage has tool_calls the graph routes to tools_step (another loop
    iteration); otherwise it routes to END (the model produced a final text answer).
    """
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return _TOOL_NODE
    return END


def build_agent_graph(
    agent_llm,
    judge_llm,
    kb_repository: KnowledgeBaseRepository,
    checkpointer: InMemorySaver,
) -> CompiledStateGraph:
    """Assemble and compile the agent graph.

    The three key steps here mirror how you set up the raw Anthropic tool-use loop:

    1. `_make_search_kb_tool` — builds the tool function and auto-generates its JSON schema
       (equivalent to defining `ToolParam` by hand).
    2. `bind_tools(tools, tool_choice=...)` — "injects" the schema into the LLM so that
       every `llm.ainvoke(messages)` call automatically includes `tools=[...]` in the
       underlying Anthropic API request. It happens inside `AgentNodes.__init__`, once per
       tool_choice mode, because the mode changes with the loop iteration.
    3. Graph topology — declares the while loop structure declaratively:
         START → agent_step → (tool_calls?) → tools_step → agent_step → ...
                                           ↘ (no tool_calls) → END
       The `checkpointer` replaces the in-memory `messages` list: it persists state across
       HTTP calls so the conversation survives between turns, keyed by `thread_id`.
    """
    # Step 1 — build tool + auto-generate its ToolParam-equivalent schema from the signature.
    search_tool = _make_search_kb_tool(kb_repository, judge_llm)

    # Step 2 — hand the raw model and the tool set to the nodes; AgentNodes pre-binds the
    # schema once per tool_choice mode and selects one per iteration (see agent_step).
    nodes = AgentNodes(agent_llm, [search_tool, answer_without_search])

    # Step 3 — declare the loop as a graph.
    graph = StateGraph(AgentState)
    graph.add_node(_AGENT_NODE, nodes.agent_step)
    graph.add_node(_TOOL_NODE, nodes.tools_step)
    graph.add_edge(START, _AGENT_NODE)
    graph.add_conditional_edges(_AGENT_NODE, _route_after_agent)  # the while-condition
    graph.add_edge(_TOOL_NODE, _AGENT_NODE)  # loop back

    return graph.compile(checkpointer=checkpointer)


def _log_turn_messages(messages: list[BaseMessage], session_id: str) -> None:
    turn_messages = messages[_last_turn_index(messages) :]
    lines = [
        f"[AGENT] session={session_id} — turn trace ({len(turn_messages)} messages):"
    ]
    for i, msg in enumerate(turn_messages):
        prefix = f"  [{i}] {type(msg).__name__}"
        if isinstance(msg, HumanMessage):
            lines.append(f"{prefix}: {msg.content!r}")
        elif isinstance(msg, AIMessage):
            content_preview = repr(msg.content) if msg.content else "(no text)"
            lines.append(f"{prefix}: content={content_preview}")
            for tc in msg.tool_calls or []:
                lines.append(
                    f"       tool_call → name={tc['name']!r} args={tc['args']}"
                )
        elif isinstance(msg, ToolMessage):
            preview = msg.content[:300] + "…" if len(msg.content) > 300 else msg.content
            lines.append(
                f"{prefix}: tool_call_id={msg.tool_call_id!r} content={preview!r}"
            )
        else:
            lines.append(f"{prefix}: {str(msg)[:200]}")
    logger.info("\n".join(lines))


class AgentService:
    def __init__(self, graph: CompiledStateGraph) -> None:
        self.graph = graph

    async def chat(self, request: ChatRequest, user_email: str) -> ChatResponse:
        config = {"configurable": {"thread_id": request.session_id}}
        state = await self.graph.ainvoke(
            {"messages": [HumanMessage(request.message)]},
            config=config,
        )
        last_ai = next(
            (m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None
        )
        answer = last_ai.text() if last_ai else _ERROR_MESSAGE
        _log_turn_messages(state["messages"], request.session_id)
        logger.info(
            "[AGENT] session=%s user=%s answer_len=%d",
            request.session_id,
            user_email,
            len(answer),
        )
        return ChatResponse(
            answer=answer,
            rephrased_question="",
            session_id=request.session_id,
            message_id=str(uuid4()),
        )

    async def stream(
        self, input_data: RunAgentInput, user_email: str
    ) -> AsyncGenerator[BaseEvent]:
        config = {"configurable": {"thread_id": input_data.thread_id}}
        agent = LangGraphAgent(name="okd-agent", graph=self.graph, config=config)
        try:
            async for event in agent.run(input_data):
                yield event
            snapshot = await self.graph.aget_state(config)
            _log_turn_messages(
                snapshot.values.get("messages") or [], input_data.thread_id
            )
        except Exception:  # noqa: BLE001
            logger.error(
                "[AGENT] stream error session=%s user=%s\n%s",
                input_data.thread_id,
                user_email,
                traceback.format_exc(),
            )
            yield RunErrorEvent(type=EventType.RUN_ERROR, message=_ERROR_MESSAGE)
