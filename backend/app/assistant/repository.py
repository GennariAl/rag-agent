from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.assistant.models import OneKnowledgeDoorLog
from app.assistant.schemas import AssistantLogEntry


class AssistantLogRepository:
    """Persists one `oneknowledgedoor_log` row per assistant request.

    Takes the `async_sessionmaker` (the factory), NOT the request-scoped `AsyncSession`, and
    opens its own short session per write: the log is emitted from the service's `finally`, and
    when the turn failed on a database error the request session is in an aborted transaction
    ("current transaction is aborted") — a fresh session gets a clean transaction so the log is
    still written. Writing is best-effort (the caller wraps this and never re-raises).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def save(self, entry: AssistantLogEntry) -> None:
        async with self._session_factory() as session:
            session.add(OneKnowledgeDoorLog(**entry.model_dump()))
            await session.commit()
