import uuid
from datetime import UTC, datetime

from sqlalchemy import UUID, Boolean, DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IDMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
        server_onupdate=func.now(),
    )


class NotUpdatableOkdEntity(Base):
    __abstract__ = True

    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )


class AuditMixin:
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    updated_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_on: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=lambda: datetime.now(UTC),
        server_onupdate=func.now(),
        nullable=True,
    )


class OkdEntity(Base, AuditMixin):
    __abstract__ = True

    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class BaseTable(Base, IDMixin, TimestampMixin):
    __abstract__ = True
