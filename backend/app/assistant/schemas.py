from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from app.base_schema import BaseSchema


class ChatRequest(BaseSchema):
    message: Annotated[str, Field()]
    # Session identifier (= the LangGraph checkpointer thread_id). The client generates it
    # per chat and resends it on each turn to resume the in-memory conversation history.
    session_id: Annotated[str, Field()]


class DocumentReference(BaseSchema):
    """Document-level citation, mapped programmatically from a retrieved chunk."""

    document_title: Annotated[str | None, Field(default=None)]
    permalink: Annotated[str | None, Field(default=None)]
    category: Annotated[str | None, Field(default=None)]
    # All the pages of this document used to build the answer, order-preserving.
    # A document is cited once, but each contributing chunk keeps its own page.
    page_number: Annotated[list[int], Field(default_factory=list)]


class ChatResponse(BaseSchema):
    answer: Annotated[str, Field()]
    references: Annotated[list[DocumentReference], Field(default_factory=list)]
    # Exposed for debugging the rephrasing node; may be dropped in a later step.
    rephrased_question: Annotated[str | None, Field(default=None)]
    # Per-node wall-clock durations (seconds), for evaluation/observability. Optional and
    # additive; absent for older clients.
    durations: Annotated[dict[str, float], Field(default_factory=dict)]
    request_duration: Annotated[float, Field(default=0.0)]
    # Total wall-clock time for the whole request (seconds).
    relevant_chunks: Annotated[int, Field(default=0)]
    session_id: Annotated[str, Field()]
    # Per-turn identifier for feedback correlation. On the JSON transport this is a
    # backend-generated uuid4 returned here so the client can round-trip it on POST /feedback.
    message_id: Annotated[str, Field()]


class AssistantLogEntry(BaseSchema):
    """Persisted `[ASSISTANT]` log row (one per request), mapped from the final graph state.

    The state→row mapping (field renames, chunk serialization, counts) lives here in the
    before-validator, not hand-rolled in the service (convention 6). The caller passes the raw
    graph state under `state`, plus the few values the service owns and computes: `session_id`,
    `user_email`, `error`, the `request_duration`, and the pre-rendered `chat_history` (rendered
    by the service to avoid importing `nodes._render_history` here — that would be a cycle).
    """

    session_id: Annotated[str, Field()]
    # AG-UI run_id on the streaming transport; backend-generated uuid4 on the JSON transport.
    message_id: Annotated[str | None, Field(default=None)]
    user_email: Annotated[str, Field()]
    error: Annotated[str | None, Field(default=None)]
    route: Annotated[str | None, Field(default=None)]

    request_duration: Annotated[float, Field(default=0.0)]
    orchestrator_duration: Annotated[float, Field(default=0.0)]
    rephrase_duration: Annotated[float, Field(default=0.0)]
    retrieve_duration: Annotated[float, Field(default=0.0)]
    judge_duration: Annotated[float, Field(default=0.0)]
    chat_duration: Annotated[float, Field(default=0.0)]

    orchestrator_input_tokens: Annotated[int, Field(default=0)]
    orchestrator_output_tokens: Annotated[int, Field(default=0)]
    rephrase_input_tokens: Annotated[int, Field(default=0)]
    rephrase_output_tokens: Annotated[int, Field(default=0)]
    judge_input_tokens: Annotated[int, Field(default=0)]
    judge_output_tokens: Annotated[int, Field(default=0)]
    chat_input_tokens: Annotated[int, Field(default=0)]
    chat_output_tokens: Annotated[int, Field(default=0)]

    question: Annotated[str | None, Field(default=None)]
    rephrased_question: Annotated[str | None, Field(default=None)]
    answer: Annotated[str | None, Field(default=None)]
    chat_history: Annotated[str | None, Field(default=None)]

    relevant_chunks: Annotated[int, Field(default=0)]
    chunks: Annotated[list[dict[str, Any]], Field(default_factory=list)]

    @model_validator(mode="before")
    @classmethod
    def _from_state(cls, data: Any) -> Any:
        """Flatten the raw graph state (plus the service-owned values) into the row fields.

        Only fires when handed the `{state, session_id, ...}` envelope from the service; a plain
        dict of already-mapped fields (e.g. in tests) passes through unchanged. `error` is
        normalized from the log's empty string to NULL; `chunks` are the serialized relevant
        chunks and `relevant_chunks` their count.
        """
        if not isinstance(data, dict) or "state" not in data:
            return data
        state = data["state"]
        relevant = state.get("relevant_documents") or []
        return {
            "session_id": data.get("session_id"),
            "message_id": data.get("message_id"),
            "user_email": data.get("user_email"),
            "error": data.get("error") or None,
            "route": state.get("route"),
            "request_duration": data.get("request_duration", 0.0),
            "orchestrator_duration": state.get("orchestrator_duration", 0.0),
            "rephrase_duration": state.get("rephrase_duration", 0.0),
            "retrieve_duration": state.get("retrieve_duration", 0.0),
            "judge_duration": state.get("judge_duration", 0.0),
            "chat_duration": state.get("chat_duration", 0.0),
            "orchestrator_input_tokens": state.get("orchestrator_input_tokens", 0),
            "orchestrator_output_tokens": state.get("orchestrator_output_tokens", 0),
            "rephrase_input_tokens": state.get("rephrase_input_tokens", 0),
            "rephrase_output_tokens": state.get("rephrase_output_tokens", 0),
            "judge_input_tokens": state.get("judge_input_tokens", 0),
            "judge_output_tokens": state.get("judge_output_tokens", 0),
            "chat_input_tokens": state.get("chat_input_tokens", 0),
            "chat_output_tokens": state.get("chat_output_tokens", 0),
            "question": state.get("question"),
            "rephrased_question": state.get("rephrased_question"),
            "answer": state.get("answer"),
            "chat_history": data.get("chat_history"),
            "relevant_chunks": len(relevant),
            "chunks": [
                doc.model_dump(mode="json") if hasattr(doc, "model_dump") else doc
                for doc in relevant
            ],
        }


class RouteDecision(BaseSchema):
    """Internal LLM structured-output schema for the orchestrate node (not a request/response DTO).

    The orchestrate node runs once at the head of the graph and does two things in a single call:
    classifies the conversation as chit-chat or a real knowledge-base query, and — only for
    chit-chat — already produces the courteous reply (so the chit-chat path costs one LLM call and
    skips retrieve/judge/chat entirely).

    `reasoning` is declared before the decision on purpose (same rationale as `RelevanceVerdict`):
    it makes the model justify its call before committing to it.
    """

    reasoning: Annotated[str, Field(description="Brief justification of the decision.")]
    route: Annotated[
        Literal["chit_chat", "knowledge_query"],
        Field(
            description=(
                "'chit_chat' for anything that does NOT require [censored] — "
                "both legitimate social/meta talk (greetings, thanks, small talk, questions "
                "about the assistant) and out-of-scope or disallowed requests to refuse; "
                "'knowledge_query' for anything that requires [censored]. Judge "
                "the whole conversation, not just the last message. See the system prompt."
            )
        ),
    ]
    chit_chat_response: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The reply to the user, in English — a courteous answer for social/meta talk "
                "or a firm, polite refusal for out-of-scope/disallowed requests. Populate "
                "ONLY when route is 'chit_chat'; leave null for 'knowledge_query'."
            ),
        ),
    ]

    @model_validator(mode="after")
    def _require_chit_chat_response(self) -> Self:
        """Enforce the cross-field invariant that lives only in the prompt otherwise.

        A `chit_chat` route with an empty/absent `chit_chat_response` is structurally valid
        for the two fields taken alone, but it means the turn has no reply to stream. A smaller
        model tier can emit exactly that; making it a schema violation lets `orchestrate` catch
        it (via `include_raw=True`'s `parsing_error`) instead of silently streaming a blank
        turn. `knowledge_query` legitimately leaves the field null, so the check is conditional.
        """
        if self.route == "chit_chat" and not (self.chit_chat_response or "").strip():
            raise ValueError(
                "chit_chat_response must be non-empty when route is 'chit_chat'"
            )
        return self


class RephrasedQuery(BaseSchema):
    """Internal LLM structured-output schema for the rephrase node (not a request/response DTO).

    Forcing the output into a schema (instead of free-form text) is what keeps the rephrase
    model from drifting into *answering* the question at turn 2+, where the accumulated
    conversation history primes it to behave like the assistant. The field name itself
    (`standalone_question`) constrains the model to produce a rewritten question.
    """

    standalone_question: Annotated[
        str,
        Field(
            description=(
                "The latest user message rewritten as a single, self-contained question in "
                "English, with all references to the conversation resolved into explicit terms. "
                "Never an answer, a refusal, or a request for clarification."
            )
        ),
    ]


class RelevanceVerdict(BaseSchema):
    """Internal LLM structured-output schema for the judge node (not a request/response DTO).

    `reasoning` is declared before `is_relevant` on purpose: it makes the model justify its
    call before committing to the boolean, which improves the precision of the classification.
    """

    reasoning: Annotated[str, Field(description="Brief justification of the decision.")]
    is_relevant: Annotated[
        bool,
        Field(description="True if the document is relevant to the user's question."),
    ]
