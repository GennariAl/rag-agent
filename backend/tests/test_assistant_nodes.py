from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from app.assistant.nodes import STREAM_TEXT_EVENT, AssistantNodes
from app.assistant.schemas import RouteDecision
from app.knowledge_base.schemas import SearchResult


def _nodes() -> AssistantNodes:
    """AssistantNodes with mocked models/repository (reset & finalize use neither)."""
    return AssistantNodes(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )


class _CaptureCustomEvents(AsyncCallbackHandler):
    """Records custom events dispatched via `adispatch_custom_event` during a runnable run."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def on_custom_event(self, name, data, **kwargs):
        self.events.append((name, data))


def _structured_result(parsed, input_tokens=0, output_tokens=0, parsing_error=None):
    """Mimic `with_structured_output(..., include_raw=True)`'s return dict."""
    raw = MagicMock()
    raw.usage_metadata = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    return {"parsed": parsed, "raw": raw, "parsing_error": parsing_error}


async def test_reset_clears_derived_fields_and_captures_question():
    nodes = _nodes()
    state = {
        "messages": [HumanMessage("first"), AIMessage("answer"), HumanMessage("latest")]
    }

    out = await nodes.reset(state)

    # Question is the latest user message, not the whole history.
    assert out["question"] == "latest"
    # Derived fields are cleared.
    assert out["rephrased_question"] == ""
    assert out["documents"] == []
    assert out["relevant_documents"] == []
    assert out["judge_verdicts"] == []
    assert out["references"] == []
    # Token counters are zeroed (fixes stale counters on a chit-chat turn).
    for key in (
        "orchestrator_input_tokens",
        "rephrase_output_tokens",
        "judge_input_tokens",
        "chat_output_tokens",
    ):
        assert out[key] == 0
    assert isinstance(out["started_at"], float)
    # Never touches messages (the input, reduced by add_messages).
    assert "messages" not in out


async def test_finalize_extracts_deduped_references_into_state():
    nodes = _nodes()
    state = {
        "messages": [HumanMessage("question")],
        "relevant_documents": [
            SearchResult(
                content="a", document_title="Doc A", permalink="/a", page_number=1
            ),
            SearchResult(
                content="b", document_title="Doc A", permalink="/a", page_number=2
            ),
            SearchResult(
                content="c", document_title="Doc B", permalink="/b", page_number=1
            ),
        ],
        "started_at": 0.0,
    }

    out = await nodes.finalize(state)

    refs = out["references"]
    # Deduped by permalink: two documents, order preserved.
    assert [r.permalink for r in refs] == ["/a", "/b"]
    assert [r.document_title for r in refs] == ["Doc A", "Doc B"]
    # Core merge behaviour: both pages from the two Doc A chunks are collected.
    assert refs[0].page_number == [1, 2]
    assert refs[1].page_number == [1]


async def test_finalize_handles_empty_relevant_documents():
    nodes = _nodes()

    out = await nodes.finalize(
        {
            "messages": [HumanMessage("question")],
            "relevant_documents": [],
            "started_at": 0.0,
        }
    )

    assert out["references"] == []


async def test_fallback_sets_answer_without_touching_messages():
    nodes = _nodes()

    out = await nodes.fallback({})

    # fallback only records the answer; stream_answer owns writing it into `messages`.
    assert out["answer"] == AssistantNodes._OUT_OF_SCOPE_MESSAGE
    assert "messages" not in out


async def test_stream_answer_signals_and_writes_message_with_shared_id():
    nodes = _nodes()
    handler = _CaptureCustomEvents()

    # Run the node inside a RunnableLambda carrying the handler, so the real
    # `adispatch_custom_event` path fires (it reads the run config from the contextvar) and the
    # handler captures the event — no monkeypatch of the framework function.
    out = await RunnableLambda(nodes.stream_answer).ainvoke(
        {"answer": "ciao"}, config={"callbacks": [handler]}
    )

    # (a) the signal custom event carries the answer text.
    name, data = handler.events[0]
    assert name == STREAM_TEXT_EVENT
    assert data["message"] == "ciao"
    # (b) the AIMessage written into history matches the streamed message id and text.
    message = out["messages"][0]
    assert isinstance(message, AIMessage)
    assert message.text() == "ciao"
    assert message.id == data["message_id"]


def test_route_decision_rejects_chit_chat_without_response():
    # The model_validator turns a chit-chat route with no reply into a schema violation.
    with pytest.raises(ValidationError):
        RouteDecision(reasoning="x", route="chit_chat", chit_chat_response=None)
    with pytest.raises(ValidationError):
        RouteDecision(reasoning="x", route="chit_chat", chit_chat_response="   ")


def test_route_decision_allows_knowledge_query_without_response():
    decision = RouteDecision(reasoning="x", route="knowledge_query")
    assert decision.chit_chat_response is None


async def test_orchestrate_chit_chat_records_answer_and_tokens():
    nodes = _nodes()
    decision = RouteDecision(
        reasoning="greeting", route="chit_chat", chit_chat_response="Hello!"
    )
    nodes.orchestrator_llm.ainvoke = AsyncMock(
        return_value=_structured_result(decision, input_tokens=10, output_tokens=5)
    )

    out = await nodes.orchestrate({"messages": [HumanMessage("hi")]})

    assert out["route"] == "chit_chat"
    assert out["answer"] == "Hello!"
    assert out["orchestrator_input_tokens"] == 10
    assert out["orchestrator_output_tokens"] == 5


async def test_orchestrate_malformed_output_reroutes_and_keeps_tokens():
    nodes = _nodes()
    # include_raw=True captures a schema violation as parsed=None (it does not raise).
    nodes.orchestrator_llm.ainvoke = AsyncMock(
        return_value=_structured_result(
            None, input_tokens=10, output_tokens=5, parsing_error=ValueError("bad")
        )
    )

    out = await nodes.orchestrate({"messages": [HumanMessage("hi")]})

    # Fail safe to knowledge_query, no blank/canned answer, tokens still counted (billed).
    assert out["route"] == "knowledge_query"
    assert "answer" not in out
    assert out["orchestrator_input_tokens"] == 10
    assert out["orchestrator_output_tokens"] == 5


async def test_orchestrate_call_error_reroutes_to_knowledge_query():
    nodes = _nodes()
    nodes.orchestrator_llm.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))

    out = await nodes.orchestrate({"messages": [HumanMessage("hi")]})

    assert out["route"] == "knowledge_query"
    assert "answer" not in out


def test_references_dedup_falls_back_to_title_without_permalink():
    docs = [
        SearchResult(content="a", document_title="Doc A", permalink=None),
        SearchResult(content="b", document_title="Doc A", permalink=None),
    ]

    refs = AssistantNodes._references(docs)

    assert len(refs) == 1
    assert refs[0].document_title == "Doc A"
