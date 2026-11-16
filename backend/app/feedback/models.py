from sqlalchemy import Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import BaseTable


class UserFeedback(BaseTable):
    """One row per user feedback submission.

    Append-only table (managed by Alembic autogenerate). `session_id` is indexed as
    the main filter key. `comment` is only allowed for negative feedback (enforced by
    the schema layer; the frontend never sends it for positive feedback).

    A partial unique index on (session_id, message_id) WHERE message_id IS NOT NULL
    prevents duplicate feedback for the same turn; the repository uses ON CONFLICT DO
    NOTHING so retries and double-taps are silently ignored (idempotent 204).
    Rows with NULL message_id are not covered — NULL means the turn could not be
    identified, so deduplication is not guaranteed for that edge case.

    PoC limitation: message_id is a free-text column with no FK to
    oneknowledgedoor_log.message_id; there is no DB-level check that the referenced
    turn actually exists. Accepted for the PoC (no FKs elsewhere in the schema either).
    """

    __tablename__ = "user_feedback"
    __table_args__ = (
        Index(
            "user_feedback_session_message_uniq",
            "session_id",
            "message_id",
            unique=True,
            postgresql_where=text("message_id IS NOT NULL"),
        ),
    )

    session_id: Mapped[str] = mapped_column(Text, index=True)
    # AG-UI run_id on the streaming transport; backend-generated uuid4 on the JSON transport.
    message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "positive" or "negative" — enforced by the Pydantic schema layer.
    feedback: Mapped[str] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
