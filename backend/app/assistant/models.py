from typing import Any

from sqlalchemy import Float, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import BaseTable


class OneKnowledgeDoorLog(BaseTable):
    """One row per assistant request: the persisted counterpart of the `[ASSISTANT]` stdout log.

    Append-only audit/observability table (managed by Alembic autogenerate). Inherits `BaseTable`
    (id + timestamps): `created_at` is the request timestamp; `updated_at` is unused but harmless.
    The scalar fields mirror what `AssistantService._format_log` prints, one column each; the
    variable-length data goes into `chat_history` (rendered text) and `chunks` (JSONB list of the
    relevant chunks). `session_id` is indexed because it is the main query/filter key (e.g. to
    separate test traffic, which uses a "speaking" session_id prefix, from real traffic).
    """

    __tablename__ = "oneknowledgedoor_log"

    session_id: Mapped[str] = mapped_column(Text, index=True)
    # AG-UI run_id on the streaming transport; backend-generated uuid4 on the JSON transport.
    message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_email: Mapped[str] = mapped_column(Text)
    # NULL on success; the traceback on a fatal turn (mapped from the log's empty string).
    error: Mapped[str | None] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(Text)

    request_duration: Mapped[float] = mapped_column(Float)
    orchestrator_duration: Mapped[float] = mapped_column(Float)
    rephrase_duration: Mapped[float] = mapped_column(Float)
    retrieve_duration: Mapped[float] = mapped_column(Float)
    judge_duration: Mapped[float] = mapped_column(Float)
    chat_duration: Mapped[float] = mapped_column(Float)

    orchestrator_input_tokens: Mapped[int] = mapped_column(Integer)
    orchestrator_output_tokens: Mapped[int] = mapped_column(Integer)
    rephrase_input_tokens: Mapped[int] = mapped_column(Integer)
    rephrase_output_tokens: Mapped[int] = mapped_column(Integer)
    judge_input_tokens: Mapped[int] = mapped_column(Integer)
    judge_output_tokens: Mapped[int] = mapped_column(Integer)
    chat_input_tokens: Mapped[int] = mapped_column(Integer)
    chat_output_tokens: Mapped[int] = mapped_column(Integer)

    question: Mapped[str | None] = mapped_column(Text)
    rephrased_question: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    chat_history: Mapped[str | None] = mapped_column(Text)

    relevant_chunks: Mapped[int] = mapped_column(Integer)
    chunks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
