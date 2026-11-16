from uuid import uuid4

from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.assistant.models import OneKnowledgeDoorLog
from app.assistant.repository import AssistantLogRepository
from app.assistant.schemas import AssistantLogEntry
from app.knowledge_base.schemas import SearchResult


def _state() -> dict:
    """A representative final graph state for a knowledge-query turn (one kept chunk)."""
    return {
        "route": "knowledge_query",
        "question": "[censored]",
        "rephrased_question": "[censored]",
        "answer": "[censored]",
        "orchestrator_duration": 0.1,
        "rephrase_duration": 0.2,
        "retrieve_duration": 0.3,
        "judge_duration": 0.4,
        "chat_duration": 0.5,
        "orchestrator_input_tokens": 10,
        "orchestrator_output_tokens": 1,
        "judge_input_tokens": 20,
        "chat_output_tokens": 30,
        "relevant_documents": [
            SearchResult(content="chunk one", document_title="Doc A", page_number=2),
            SearchResult(content="chunk two", document_title="Doc B", page_number=5),
        ],
        "messages": [],
    }


def test_log_entry_maps_state_to_row_fields():
    entry = AssistantLogEntry.model_validate(
        {
            "session_id": "test-map",
            "user_email": "john.doe@example.com",
            "error": "",  # empty string on success → NULL
            "request_duration": 1.5,
            "chat_history": "(no prior conversation)",
            "state": _state(),
        }
    )

    assert entry.session_id == "test-map"
    assert entry.message_id is None  # not provided → NULL
    assert entry.user_email == "john.doe@example.com"
    assert entry.error is None
    assert entry.route == "knowledge_query"
    assert entry.request_duration == 1.5
    assert entry.orchestrator_duration == 0.1
    assert entry.chat_duration == 0.5
    assert entry.orchestrator_input_tokens == 10
    assert entry.judge_input_tokens == 20
    assert entry.chat_output_tokens == 30
    assert entry.question == "[censored]"
    assert entry.chat_history == "(no prior conversation)"
    assert entry.relevant_chunks == 2
    assert [c["content"] for c in entry.chunks] == ["chunk one", "chunk two"]
    assert entry.chunks[0]["document_title"] == "Doc A"


def test_log_entry_carries_message_id_when_provided():
    """message_id (= AG-UI run_id) is forwarded from the service envelope to the row."""
    entry = AssistantLogEntry.model_validate(
        {
            "session_id": "test-mid",
            "message_id": "run-abc123",
            "user_email": "john.doe@example.com",
            "error": "",
            "request_duration": 0.5,
            "chat_history": "(no prior conversation)",
            "state": _state(),
        }
    )

    assert entry.message_id == "run-abc123"


def test_log_entry_keeps_error_traceback():
    entry = AssistantLogEntry.model_validate(
        {
            "session_id": "test-err",
            "user_email": "john.doe@example.com",
            "error": "Traceback (most recent call last): boom",
            "request_duration": 0.0,
            "chat_history": "(no prior conversation)",
            "state": {"messages": []},
        }
    )

    assert entry.error == "Traceback (most recent call last): boom"
    assert entry.relevant_chunks == 0
    assert entry.chunks == []


async def test_repository_persists_log_row(
    container: AsyncContainer,
    session_factory: async_sessionmaker[AsyncSession],
):
    repository = await container.get(AssistantLogRepository)
    session_id = f"test-{uuid4()}"  # speaking prefix: test rows are filterable out
    entry = AssistantLogEntry.model_validate(
        {
            "session_id": session_id,
            "user_email": "john.doe@example.com",
            "error": "",
            "request_duration": 2.0,
            "chat_history": "(no prior conversation)",
            "state": _state(),
        }
    )

    await repository.save(entry)

    async with session_factory() as session:
        row = (
            await session.execute(
                select(OneKnowledgeDoorLog).where(
                    OneKnowledgeDoorLog.session_id == session_id
                )
            )
        ).scalar_one()
        try:
            assert row.user_email == "john.doe@example.com"
            assert row.message_id is None  # not provided in this path
            assert row.error is None
            assert row.route == "knowledge_query"
            assert row.relevant_chunks == 2
            assert row.chunks[1]["content"] == "chunk two"
            assert row.created_at is not None
        finally:
            # Independent-session commit escapes test isolation, so clean up explicitly.
            await session.delete(row)
            await session.commit()
