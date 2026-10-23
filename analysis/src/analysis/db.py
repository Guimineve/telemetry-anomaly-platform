"""SQLAlchemy tables for rules and alert history."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class RuleRow(Base):
    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(32))
    metric: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device_pattern: Mapped[str] = mapped_column(String(64), default="*")
    params: Mapped[dict] = mapped_column(JsonType, default=dict)
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AlertRow(Base):
    """One alert *event*: a rule opening or resolving for a device."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_ts", "ts"),
        Index("ix_alerts_device_ts", "device_id", "ts"),
        Index("ix_alerts_rule_device_ts", "rule_id", "device_id", "ts"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(36))  # not a FK: history outlives rule deletion
    rule_name: Mapped[str] = mapped_column(String(120))
    rule_type: Mapped[str] = mapped_column(String(32))
    device_id: Mapped[str] = mapped_column(String(64))
    metric: Mapped[str | None] = mapped_column(String(32), nullable=True)
    severity: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(32), unique=True)  # stored lower-cased
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)  # bump to end all sessions
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditRow(Base):
    """One security-relevant event. Never holds passwords or tokens."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_ts", "ts"), Index("ix_audit_action_ts", "action", "ts"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # None: unknown user
    actor_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(48))
    target_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict] = mapped_column(JsonType, default=dict)


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def make_sessions(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
