import pytest
from ag_ui.core import EventType, RunAgentInput
from ag_ui.core.events import CustomEvent, RunFinishedEvent, RunStartedEvent
from httpx import AsyncClient

from app.assistant.nodes import STREAM_TEXT_EVENT
from tests.conftest import MockAssistantService, basic_stream_events


@pytest.fixture(autouse=True)
def _reset_mock_stream():
    """Restore the mock's class-level streaming state after each test.

    `MockAssistantService` is REQUEST-scoped, so its observable state lives on class attrs
    shared across the process; reset them so a test that overrides the scenario does not leak
    into the next test."""
    yield
    MockAssistantService.stream_events = basic_stream_events()
    MockAssistantService.last_user_email = None


def _run_agent_input() -> dict:
    """A minimal valid RunAgentInput payload (JSON, camelCase aliases)."""
    return RunAgentInput(
        thread_id="s-stream",
        run_id="r1",
        state={},
        messages=[],
        tools=[],
        context=[],
        forwarded_props={},
    ).model_dump(mode="json", by_alias=True)


async def test_chat_json_route_unchanged(authenticated_client: AsyncClient):
    """The JSON transport still returns a materialized ChatResponse (mock service)."""
    resp = await authenticated_client.post(
        "/assistant/chat", json={"message": "hi", "session_id": "s1"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "canned answer"
    assert body["sessionId"] == "s1"
    # message_id must be present so a JSON-transport client can round-trip it on POST /feedback.
    assert body["messageId"] is not None


async def test_chat_stream_route_emits_sse_events(authenticated_client: AsyncClient):
    resp = await authenticated_client.post(
        "/assistant/chat/stream", json=_run_agent_input()
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "RUN_STARTED" in body
    assert "TEXT_MESSAGE_CONTENT" in body
    assert "RUN_FINISHED" in body
    # The trusted forwarded email reached the run (audit), not the client payload: the service
    # (not the route) now builds the agent config, so we assert what it received.
    assert MockAssistantService.last_user_email == "john.doe@example.com"


async def test_chat_stream_route_expands_pre_computed_answer(
    authenticated_client: AsyncClient,
):
    """The stream_answer signal is expanded into typewriter text deltas, not forwarded raw."""
    # Chit-chat / fallback scenario: the graph dispatches the `stream_answer` custom event
    # instead of a real chat-model stream; the route must expand it into typewriter deltas.
    MockAssistantService.stream_events = [
        RunStartedEvent(type=EventType.RUN_STARTED, thread_id="s-stream", run_id="r1"),
        CustomEvent(
            type=EventType.CUSTOM,
            name=STREAM_TEXT_EVENT,
            value={"message_id": "m1", "message": "hi there friend"},
        ),
        RunFinishedEvent(
            type=EventType.RUN_FINISHED, thread_id="s-stream", run_id="r1"
        ),
    ]

    resp = await authenticated_client.post(
        "/assistant/chat/stream", json=_run_agent_input()
    )

    assert resp.status_code == 200
    body = resp.text
    # One text message (start/end once) split into several content deltas, all id "m1".
    assert body.count("TEXT_MESSAGE_START") == 1
    assert body.count("TEXT_MESSAGE_END") == 1
    assert body.count("TEXT_MESSAGE_CONTENT") > 1
    assert "m1" in body
    # The raw CUSTOM signal must not leak to the client.
    assert STREAM_TEXT_EVENT not in body


async def test_chat_stream_route_requires_auth(client: AsyncClient):
    resp = await client.post("/assistant/chat/stream", json=_run_agent_input())

    assert resp.status_code == 401
