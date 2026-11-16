"""add_unique_index_to_user_feedback

Revision ID: 70091f94d712
Revises: d11b1c1192fa
Create Date: 2026-07-23 16:08:20.637022

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "70091f94d712"
down_revision: str | Sequence[str] | None = "d11b1c1192fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "user_feedback_session_message_uniq",
        "user_feedback",
        ["session_id", "message_id"],
        unique=True,
        postgresql_where=sa.text("message_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "user_feedback_session_message_uniq",
        table_name="user_feedback",
        postgresql_where=sa.text("message_id IS NOT NULL"),
    )
