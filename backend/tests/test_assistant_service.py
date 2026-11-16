import logging

from ag_ui.core import EventType, RunAgentInput
from ag_ui.core.events import RunFinishedEvent, RunStartedEvent
from langchain_core.messages import AIMessage, HumanMessage

from app.assistant.nodes import AssistantNodes
from app.assistant.schemas import ChatRequest
from app.assistant.service import AssistantService

_LOGGER = "app.assistant.service"


class _FakeSnapshot:
    def __init__(self, values: dict):
        self.values = values


class _FakeLogRepository:
    """Records the persisted log entries instead of writing to a DB."""

    def __init__(self):
        self.saved = []

    async def save(self, entry) -> None:
        self.saved.append(entry)


class _FakeGraph:
    """Minimal CompiledStateGraph stand-in: `ainvoke` returns/raises, `aget_state` replays."""

    def __init__(self, *, invoke_result=None, invoke_error=None, state_values=None):
        self._invoke_result = invoke_result
        self._invoke_error = invoke_error
        self._state_values = state_values or {}

    async def ainvoke(self, _input, config=None):
        if self._invoke_error is not None:
            raise self._invoke_error
        return self._invoke_result

    async def aget_state(self, _config):
        return _FakeSnapshot(self._state_values)


def _success_state() -> dict:
    return {
        "messages": [HumanMessage("q"), AIMessage("a")],
        "answer": "the answer",
        "references": [],
        "rephrased_question": "standalone q",
        "relevant_documents": [],
        "route": "knowledge_query",
        "started_at": 0.0,
    }


def _run_agent_input() -> RunAgentInput:
    return RunAgentInput(
        thread_id="s-stream",
        run_id="r1",
        state={},
        messages=[],
        tools=[],
        context=[],
        forwarded_props={},
    )


# --- chat (JSON) -----------------------------------------------------------------------------


async def test_chat_success_maps_response_and_logs_info(caplog):
    log_repository = _FakeLogRepository()
    service = AssistantService(
        _FakeGraph(invoke_result=_success_state()), log_repository
    )

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        resp = await service.chat(ChatRequest(message="q", session_id="s1"), "u@x")

    assert resp.answer == "the answer"
    assert resp.rephrased_question == "standalone q"
    assert resp.session_id == "s1"
    # message_id is present on the response so the client can round-trip it on POST /feedback.
    assert resp.message_id is not None
    # Single per-request log, success path: error field reads "none".
    assert "[ASSISTANT]" in caplog.text
    assert ">> error                  : none" in caplog.text
    # The same turn is persisted once, with no error and the session id carried through.
    assert len(log_repository.saved) == 1
    assert log_repository.saved[0].session_id == "s1"
    assert log_repository.saved[0].error is None
    # JSON transport has no RunAgentInput: the service generates a uuid4 as message_id.
    assert log_repository.saved[0].message_id is not None
    # The same id is returned in the response and stored in the log.
    assert resp.message_id == log_repository.saved[0].message_id


async def test_chat_fatal_error_returns_generic_and_logs_error(caplog):
    # Partial state read back from the checkpointer after the node aborted the turn.
    partial = {
        "messages": [HumanMessage("q")],
        "started_at": 0.0,
        "question": "q",
        "route": "knowledge_query",
    }
    log_repository = _FakeLogRepository()
    service = AssistantService(
        _FakeGraph(invoke_error=RuntimeError("boom in retrieve"), state_values=partial),
        log_repository,
    )

    with caplog.at_level(logging.ERROR, logger=_LOGGER):
        resp = await service.chat(ChatRequest(message="q", session_id="s1"), "u@x")

    # User gets the generic message; no citations on the error path.
    assert resp.answer == AssistantNodes._ERROR_MESSAGE
    assert resp.references == []
    # message_id is still returned on the error path so the client can correlate feedback.
    assert resp.message_id is not None
    # The single log fired at ERROR with the traceback in the `error` field (incl. the type
    # and message — which identifies where it failed).
    assert "[ASSISTANT]" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "boom in retrieve" in caplog.text
    assert ">> error                  : none" not in caplog.text
    # The failed turn is still persisted, with the traceback captured in `error`.
    assert len(log_repository.saved) == 1
    assert "boom in retrieve" in log_repository.saved[0].error


# --- stream (SSE) ----------------------------------------------------------------------------


class _FakeAgentOK:
    def __init__(self, *, name, graph, config):
        pass

    async def run(self, _input):
        yield RunStartedEvent(
            type=EventType.RUN_STARTED, thread_id="s-stream", run_id="r1"
        )
        yield RunFinishedEvent(
            type=EventType.RUN_FINISHED, thread_id="s-stream", run_id="r1"
        )


class _FakeAgentError:
    def __init__(self, *, name, graph, config):
        pass

    async def run(self, _input):
        yield RunStartedEvent(
            type=EventType.RUN_STARTED, thread_id="s-stream", run_id="r1"
        )
        raise RuntimeError("boom in chat")


async def test_stream_success_forwards_events_and_logs_info(monkeypatch, caplog):
    monkeypatch.setattr("app.assistant.service.LangGraphAgent", _FakeAgentOK)
    service = AssistantService(
        _FakeGraph(state_values=_success_state()), _FakeLogRepository()
    )

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        events = [ev async for ev in service.stream(_run_agent_input(), "u@x")]

    types = [e.type for e in events]
    assert EventType.RUN_STARTED in types
    assert EventType.RUN_FINISHED in types
    assert EventType.RUN_ERROR not in types
    assert ">> error                  : none" in caplog.text
    # Streaming transport passes the AG-UI run_id as message_id (= "r1" from _run_agent_input).
    log_repo = service._log_repository
    assert len(log_repo.saved) == 1
    assert log_repo.saved[0].message_id == "r1"


async def test_stream_fatal_error_emits_run_error_and_logs_error(monkeypatch, caplog):
    monkeypatch.setattr("app.assistant.service.LangGraphAgent", _FakeAgentError)
    service = AssistantService(
        _FakeGraph(state_values={"messages": [HumanMessage("q")], "started_at": 0.0}),
        _FakeLogRepository(),
    )

    with caplog.at_level(logging.ERROR, logger=_LOGGER):
        events = [ev async for ev in service.stream(_run_agent_input(), "u@x")]

    run_error = [e for e in events if e.type == EventType.RUN_ERROR]
    assert len(run_error) == 1
    assert run_error[0].message == AssistantNodes._ERROR_MESSAGE
    # Same single log point, ERROR level, traceback captured.
    assert "[ASSISTANT]" in caplog.text
    assert "boom in chat" in caplog.text
