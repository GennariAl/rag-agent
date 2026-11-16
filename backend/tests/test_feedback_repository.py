from uuid import uuid4

from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.feedback.models import UserFeedback
from app.feedback.repository import FeedbackRepository
from app.feedback.schemas import FeedbackRequest


async def test_repository_persists_feedback_row(
    container: AsyncContainer,
    session_factory: async_sessionmaker[AsyncSession],
):
    repository = await container.get(FeedbackRepository)
    session_id = f"test-{uuid4()}"
    request = FeedbackRequest(
        session_id=session_id,
        message_id="run-abc123",
        feedback="negative",
        comment="answer was incomplete",
    )

    await repository.save(request)

    async with session_factory() as session:
        row = (
            await session.execute(
                select(UserFeedback).where(UserFeedback.session_id == session_id)
            )
        ).scalar_one()
        try:
            assert row.session_id == session_id
            assert row.message_id == "run-abc123"
            assert row.feedback == "negative"
            assert row.comment == "answer was incomplete"
            assert row.created_at is not None
        finally:
            await session.delete(row)
            await session.commit()


async def test_repository_persists_positive_feedback_with_null_optionals(
    container: AsyncContainer,
    session_factory: async_sessionmaker[AsyncSession],
):
    """message_id and comment are nullable — both should be NULL in the DB when omitted."""
    repository = await container.get(FeedbackRepository)
    session_id = f"test-{uuid4()}"
    request = FeedbackRequest(session_id=session_id, feedback="positive")

    await repository.save(request)

    async with session_factory() as session:
        row = (
            await session.execute(
                select(UserFeedback).where(UserFeedback.session_id == session_id)
            )
        ).scalar_one()
        try:
            assert row.feedback == "positive"
            assert row.message_id is None
            assert row.comment is None
        finally:
            await session.delete(row)
            await session.commit()


async def test_duplicate_submission_is_ignored(
    container: AsyncContainer,
    session_factory: async_sessionmaker[AsyncSession],
):
    """A second save with the same (session_id, message_id) produces exactly one row."""
    repository = await container.get(FeedbackRepository)
    session_id = f"test-{uuid4()}"
    request = FeedbackRequest(
        session_id=session_id,
        message_id="run-dup",
        feedback="positive",
    )

    await repository.save(request)
    await repository.save(request)

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(UserFeedback).where(UserFeedback.session_id == session_id)
                )
            )
            .scalars()
            .all()
        )
        try:
            assert len(rows) == 1
        finally:
            for row in rows:
                await session.delete(row)
            await session.commit()
