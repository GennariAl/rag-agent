from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.feedback.models import UserFeedback
from app.feedback.schemas import FeedbackRequest


class FeedbackRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def save(self, request: FeedbackRequest) -> None:
        async with self._session_factory() as session:
            stmt = (
                pg_insert(UserFeedback)
                .values(
                    session_id=request.session_id,
                    message_id=request.message_id,
                    feedback=request.feedback,
                    comment=request.comment,
                )
                .on_conflict_do_nothing()
            )
            await session.execute(stmt)
            await session.commit()
