from typing import Annotated, Any, TypedDict

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

from app.assistant.nodes import AssistantNodes
from app.assistant.schemas import DocumentReference
from app.knowledge_base.repository import KnowledgeBaseRepository
from app.knowledge_base.schemas import SearchResult


class AssistantState(TypedDict):
    """Internal LangGraph state (not a DTO).

    `messages` is reduced with `add_messages`, so each turn's human/AI messages accumulate
    into the conversation history held by the checkpointer (keyed by thread_id).
    """

    messages: Annotated[list[BaseMessage], add_messages]
    question: str
    # Orchestrate decision: "chit_chat" (answered directly, graph ends) or "knowledge_query"
    # (proceeds through the retrieval flow).
    route: str
    rephrased_question: str
    documents: list[SearchResult]
    # Chunks the judge node kept (subset of `documents`); the chat node and the citations are
    # built from these, not from the full retrieved set.
    relevant_documents: list[SearchResult]
    # Per-chunk judge verdicts (incl. discarded), surfaced only in the DEBUG log.
    judge_verdicts: list[dict[str, Any]]
    answer: str
    # Document-level citations, extracted programmatically by the finalize node from
    # `relevant_documents`. Carried in the state so it is a single source of truth: the JSON
    # response reads it, and the streaming transport ships it in the STATE_SNAPSHOT.
    references: list[DocumentReference]
    # perf_counter set by the reset node at the start of every turn; the finalize node uses it
    # to compute the request duration for the log (works for both transports).
    started_at: float
    # Token usage per LLM node, kept separate (orchestrate, rephrase, judge and chat run on
    # different models). The service reads them for the single per-request log; CloudWatch
    # queries can isolate any one label.
    orchestrator_input_tokens: int
    orchestrator_output_tokens: int
    rephrase_input_tokens: int
    rephrase_output_tokens: int
    judge_input_tokens: int
    judge_output_tokens: int
    chat_input_tokens: int
    chat_output_tokens: int
    # Wall-clock duration per work node (seconds). Like the token counters, these are
    # last-write-wins and are reset to 0.0 at the head of every turn by the reset node, so a
    # chit-chat turn does not inherit stale timings.
    orchestrator_duration: float
    rephrase_duration: float
    retrieve_duration: float
    judge_duration: float
    chat_duration: float


def _route_after_orchestrator(state: AssistantState) -> str:
    """Route after orchestrate: end immediately on chit-chat (reply already produced),
    otherwise continue into the retrieval flow via rephrase."""
    return "chit_chat" if state["route"] == "chit_chat" else "knowledge_query"


def _route_after_judge(state: AssistantState) -> str:
    """Route after the judge: chat if any chunk survived, fallback if none did."""
    return "chat" if state["relevant_documents"] else "fallback"


def build_graph(
    orchestrator_llm: ChatBedrockConverse,
    rephrase_llm: ChatBedrockConverse,
    judge_llm: ChatBedrockConverse,
    chat_llm: ChatBedrockConverse,
    kb_repository: KnowledgeBaseRepository,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    """Assemble and compile the assistant graph.

    Topology:
        reset -> orchestrate -> {chit_chat -> stream_answer -> finalize
                                 | knowledge_query -> rephrase -> retrieve -> judge
                                     -> {chat -> finalize
                                         | fallback -> stream_answer -> finalize}} -> END

    `reset` runs first on every turn and clears the per-turn derived fields (the checkpointer
    persists the whole state and only `messages` has a reducer). `orchestrate` classifies the
    conversation: on chit-chat it produced the reply and skips retrieval; otherwise it flows
    into retrieval. The judge filters the retrieved chunks with one LLM call each (run in
    parallel); if it discards them all the question is out of scope and `fallback` sets a fixed
    message. The two paths that produce an answer without a chat-model stream (chit-chat and
    fallback) converge on `stream_answer`, which writes the `AIMessage` into the history and
    re-emits it as streamed text for the streaming transport (see `AssistantNodes.stream_answer`);
    the `chat` path already streams natively and goes straight to `finalize`. Every terminal
    path converges on `finalize`, which extracts the document references. The single per-request
    `[ASSISTANT]` log is emitted by `AssistantService` (see there), not by `finalize`.

    The models are plain `ChatBedrockConverse` here — the distinct gateway tiers are resolved
    at the Dishka boundary, so this module stays decoupled from `providers/`.
    """
    nodes = AssistantNodes(
        orchestrator_llm, rephrase_llm, judge_llm, chat_llm, kb_repository
    )

    builder = StateGraph(AssistantState)
    builder.add_node("reset", nodes.reset)
    builder.add_node("orchestrate", nodes.orchestrate)
    builder.add_node("rephrase", nodes.rephrase)
    builder.add_node("retrieve", nodes.retrieve)
    builder.add_node("judge", nodes.judge)
    builder.add_node("chat", nodes.chat)
    builder.add_node("fallback", nodes.fallback)
    builder.add_node("stream_answer", nodes.stream_answer)
    builder.add_node("finalize", nodes.finalize)

    builder.add_edge(START, "reset")
    builder.add_edge("reset", "orchestrate")
    builder.add_conditional_edges(
        "orchestrate",
        _route_after_orchestrator,
        {"chit_chat": "stream_answer", "knowledge_query": "rephrase"},
    )
    builder.add_edge("rephrase", "retrieve")
    builder.add_edge("retrieve", "judge")
    builder.add_conditional_edges(
        "judge", _route_after_judge, {"chat": "chat", "fallback": "fallback"}
    )
    builder.add_edge("chat", "finalize")
    builder.add_edge("fallback", "stream_answer")
    builder.add_edge("stream_answer", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
