# -*- coding: utf-8 -*-
"""PostgreSQL persistence for project-level observability events.

This repository belongs to the application observability module.  It is kept
separate from the Skill observation store and from AgentScope's storage
backends so the admin analytics contract remains project-oriented.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from observability_analytics import ObservabilityEvent
except ModuleNotFoundError:
    from examples.agent_service.observability_analytics import ObservabilityEvent


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PostgresProjectObservabilityStore:
    """Persist bounded project events without storing request payloads."""

    def __init__(self, url: str, *, engine: Any | None = None) -> None:
        if not url and engine is None:
            raise ValueError("A PostgreSQL URL or SQLAlchemy engine is required")
        self._url = url
        self._engine = engine
        self._owns_engine = engine is None
        self._table: Any | None = None
        self._metadata: Any | None = None

    async def initialize(self) -> None:
        """Open the engine and create the application-owned table/indexes."""
        from sqlalchemy import (
            BigInteger,
            Column,
            DateTime,
            Float,
            Index,
            Integer,
            MetaData,
            String,
            Table,
        )
        from sqlalchemy.ext.asyncio import create_async_engine

        if self._engine is None:
            self._engine = create_async_engine(
                self._url,
                pool_pre_ping=True,
            )

        metadata = MetaData()
        event_id_type = BigInteger().with_variant(Integer, "sqlite")
        table = Table(
            "project_observability_events",
            metadata,
            Column("event_id", event_id_type, primary_key=True, autoincrement=True),
            Column("occurred_at", DateTime(timezone=True), nullable=False),
            Column("event_name", String(64), nullable=False),
            Column("component", String(32), nullable=False),
            Column("result", String(32), nullable=False),
            Column("duration_seconds", Float, nullable=True),
            Column("request_id", String(255), nullable=True),
            Column("trace_id", String(255), nullable=True),
            Column("user_id", String(255), nullable=True),
            Column("session_id", String(255), nullable=True),
            Column("agent_name", String(255), nullable=True),
            Column("model", String(255), nullable=True),
            Column("tool", String(255), nullable=True),
            Column("tool_kind", String(64), nullable=True),
            Column("mcp_server", String(255), nullable=True),
            Column("route", String(255), nullable=True),
            Column("method", String(16), nullable=True),
            Column("status_code", Integer, nullable=True),
            Column("error_code", String(128), nullable=True),
            Column("input_tokens", Integer, nullable=False, default=0),
            Column("output_tokens", Integer, nullable=False, default=0),
            Index(
                "ix_project_obs_occurred_at",
                "occurred_at",
            ),
            Index(
                "ix_project_obs_component_occurred_at",
                "component",
                "occurred_at",
            ),
            Index(
                "ix_project_obs_trace_id",
                "trace_id",
            ),
            Index(
                "ix_project_obs_request_id",
                "request_id",
            ),
            Index(
                "ix_project_obs_user_occurred_at",
                "user_id",
                "occurred_at",
            ),
        )
        self._metadata = metadata
        self._table = table

        async with self._engine.begin() as connection:
            await connection.run_sync(metadata.create_all)

    async def record(self, event: ObservabilityEvent) -> None:
        """Insert one payload-free project event."""
        if self._engine is None or self._table is None:
            raise RuntimeError(
                "PostgresProjectObservabilityStore is not initialized; "
                "call initialize() first",
            )
        values = {
            "occurred_at": _aware_utc(event.occurred_at),
            "event_name": event.event_name,
            "component": event.component,
            "result": event.result,
            "duration_seconds": event.duration_seconds,
            "request_id": event.request_id,
            "trace_id": event.trace_id,
            "user_id": event.user_id,
            "session_id": event.session_id,
            "agent_name": event.agent_name,
            "model": event.model,
            "tool": event.tool,
            "tool_kind": event.tool_kind,
            "mcp_server": event.mcp_server,
            "route": event.route,
            "method": event.method,
            "status_code": event.status_code,
            "error_code": event.error_code,
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
        }
        async with self._engine.begin() as connection:
            await connection.execute(self._table.insert().values(**values))

    async def load_recent(self, *, limit: int = 50_000) -> list[ObservabilityEvent]:
        """Load the newest bounded window in chronological order."""
        if self._engine is None or self._table is None:
            raise RuntimeError(
                "PostgresProjectObservabilityStore is not initialized; "
                "call initialize() first",
            )

        from sqlalchemy import select

        columns = [
            self._table.c.occurred_at,
            self._table.c.event_name,
            self._table.c.component,
            self._table.c.result,
            self._table.c.duration_seconds,
            self._table.c.request_id,
            self._table.c.trace_id,
            self._table.c.user_id,
            self._table.c.session_id,
            self._table.c.agent_name,
            self._table.c.model,
            self._table.c.tool,
            self._table.c.tool_kind,
            self._table.c.mcp_server,
            self._table.c.route,
            self._table.c.method,
            self._table.c.status_code,
            self._table.c.error_code,
            self._table.c.input_tokens,
            self._table.c.output_tokens,
        ]
        statement = (
            select(*columns)
            .order_by(
                self._table.c.occurred_at.desc(),
                self._table.c.event_id.desc(),
            )
            .limit(max(1, min(limit, 100_000)))
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()

        events = [
            ObservabilityEvent(
                occurred_at=_aware_utc(row["occurred_at"]),
                event_name=row["event_name"],
                component=row["component"],
                result=row["result"],
                duration_seconds=row["duration_seconds"],
                request_id=row["request_id"],
                trace_id=row["trace_id"],
                user_id=row["user_id"],
                session_id=row["session_id"],
                agent_name=row["agent_name"],
                model=row["model"],
                tool=row["tool"],
                tool_kind=row["tool_kind"],
                mcp_server=row["mcp_server"],
                route=row["route"],
                method=row["method"],
                status_code=row["status_code"],
                error_code=row["error_code"],
                input_tokens=row["input_tokens"] or 0,
                output_tokens=row["output_tokens"] or 0,
            )
            for row in reversed(rows)
        ]
        return events

    async def close(self) -> None:
        """Dispose only the engine owned by this repository."""
        if self._engine is not None and self._owns_engine:
            await self._engine.dispose()
        self._engine = None
        self._table = None
        self._metadata = None


__all__ = ["PostgresProjectObservabilityStore"]
