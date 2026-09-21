# -*- coding: utf-8 -*-
"""Application-owned persistence for Skill observability events.

This module deliberately sits outside ``src/agentscope``.  AgentScope keeps
owning agent execution, Skill loading, and the generic Storage backends; the
agent-service application owns the optional PostgreSQL sink used by the
future Skill analysis page.

The sink is optional.  When ``SKILL_OBSERVABILITY_DATABASE_URL`` is absent,
the service keeps its existing stdout diagnostics and does not open a second
database connection.  The table stores bounded dimensions and counters only;
it never stores prompts, Skill Markdown, model output, or credentials.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Protocol
from uuid import UUID


logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return an aware UTC timestamp for PostgreSQL ``timestamptz``."""
    return datetime.now(timezone.utc)


def _failure_descriptor(
    *,
    event_name: str,
    result: str,
    error_code: str | None,
    skill_tool_available: bool | None,
) -> tuple[str, str] | None:
    """Map a bounded event to a safe failure stage and reason code."""
    if event_name == "skill.reconcile.completed" and result in {"failed", "partial"}:
        return "reconcile", error_code or f"reconcile_{result}"
    if event_name == "skill.exposed" and skill_tool_available is False:
        return "expose", error_code or "skill_tool_unavailable"
    if event_name == "skill.invoked" and result != "started":
        return "invoke", error_code or "invalid_skill_input"
    if event_name == "skill.completed" and result != "success":
        return "execute", error_code or f"skill_viewer_{result}"
    return None


@dataclass(frozen=True, slots=True)
class SkillObservationEvent:
    """A bounded, persistence-safe Skill observability event.

    The fields intentionally model the analysis dimensions directly instead
    of accepting an arbitrary payload.  This keeps the storage contract
    reviewable and prevents accidental persistence of prompts or Markdown.
    """

    event_name: str
    result: str
    user_id: str
    agent_id: str
    tenant_id: str | None = None
    membership_id: str | None = None
    session_id: str | None = None
    skill_name: str | None = None
    error_code: str | None = None
    occurred_at: datetime = field(default_factory=_utcnow)
    duration_seconds: float | None = None
    skill_count: int | None = None
    listed_skill_count: int | None = None
    skill_tool_available: bool | None = None
    before_count: int | None = None
    visible_count: int | None = None
    after_count: int | None = None
    removed_count: int | None = None
    restored_count: int | None = None
    installed_count: int | None = None
    failure_count: int | None = None

    def to_row(self) -> dict[str, Any]:
        """Return only the columns allowed by the persistence schema."""
        tenant_id = self.tenant_id
        membership_id = self.membership_id
        try:
            from identity.dependencies import (
                get_bound_membership_id,
                get_bound_tenant_id,
            )
        except ModuleNotFoundError:  # pragma: no cover - package import mode
            from examples.agent_service.identity.dependencies import (
                get_bound_membership_id,
                get_bound_tenant_id,
            )
        bound_tenant_id = get_bound_tenant_id()
        if bound_tenant_id is not None:
            tenant_id = bound_tenant_id
            membership_id = get_bound_membership_id()
        for value_name, value in (
            ("tenant_id", tenant_id),
            ("membership_id", membership_id),
        ):
            if value is None or isinstance(value, UUID):
                continue
            try:
                converted = UUID(str(value))
            except (TypeError, ValueError):
                continue
            if value_name == "tenant_id":
                tenant_id = converted
            else:
                membership_id = converted
        return {
            "event_name": self.event_name,
            "result": self.result,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "tenant_id": tenant_id,
            "membership_id": membership_id,
            "session_id": self.session_id,
            "skill_name": self.skill_name,
            "error_code": self.error_code,
            "occurred_at": self.occurred_at,
            "duration_seconds": self.duration_seconds,
            "skill_count": self.skill_count,
            "listed_skill_count": self.listed_skill_count,
            "skill_tool_available": self.skill_tool_available,
            "before_count": self.before_count,
            "visible_count": self.visible_count,
            "after_count": self.after_count,
            "removed_count": self.removed_count,
            "restored_count": self.restored_count,
            "installed_count": self.installed_count,
            "failure_count": self.failure_count,
        }


class SkillObservationSink(Protocol):
    """Minimal async contract consumed by the Skill instrumentation."""

    async def record(self, event: SkillObservationEvent) -> None:
        """Persist one bounded event."""


class NullSkillObservationSink:
    """No-op sink used when PostgreSQL persistence is not configured."""

    async def record(self, event: SkillObservationEvent) -> None:
        del event


class BestEffortSkillObservationSink:
    """Protect the Skill turn from observability storage failures."""

    def __init__(
        self,
        delegate: SkillObservationSink,
        *,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._delegate = delegate
        self._timeout_seconds = timeout_seconds

    async def record(self, event: SkillObservationEvent) -> None:
        """Try to persist without making a valid agent turn fail."""
        try:
            await asyncio.wait_for(
                self._delegate.record(event),
                timeout=self._timeout_seconds,
            )
        except Exception:
            logger.warning(
                "skill.observation.persist_failed event_name=%s "
                "result=%s user_id=%s agent_id=%s session_id=%s",
                event.event_name,
                event.result,
                event.user_id,
                event.agent_id,
                event.session_id,
                exc_info=True,
            )


class PostgresSkillObservationStore:
    """Small application-level PostgreSQL repository for Skill events.

    SQLAlchemy is imported lazily so the existing Redis-only service remains
    importable when the optional PostgreSQL extra is not installed.
    """

    def __init__(
        self,
        url: str,
        *,
        engine: Any | None = None,
    ) -> None:
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
            Boolean,
            DateTime,
            Float,
            Index,
            Integer,
            MetaData,
            String,
            Table,
            Column,
            Uuid,
        )
        from sqlalchemy.ext.asyncio import create_async_engine

        if self._engine is None:
            self._engine = create_async_engine(
                self._url,
                pool_pre_ping=True,
            )

        metadata = MetaData()
        # PostgreSQL uses BIGSERIAL; SQLite's rowid autoincrement requires
        # the exact INTEGER spelling, which keeps the repository easy to
        # exercise in the project's lightweight local test environment.
        event_id_type = BigInteger().with_variant(Integer, "sqlite")
        table = Table(
            "skill_observability_events",
            metadata,
            Column(
                "event_id",
                event_id_type,
                primary_key=True,
                autoincrement=True,
            ),
            Column("event_name", String(64), nullable=False),
            Column("result", String(32), nullable=False),
            Column("occurred_at", DateTime(timezone=True), nullable=False),
            Column("user_id", String(255), nullable=False),
            Column("agent_id", String(255), nullable=False),
            Column("tenant_id", Uuid(as_uuid=True), nullable=True),
            Column("membership_id", Uuid(as_uuid=True), nullable=True),
            Column("session_id", String(255), nullable=True),
            Column("skill_name", String(255), nullable=True),
            Column("error_code", String(128), nullable=True),
            Column("duration_seconds", Float, nullable=True),
            Column("skill_count", Integer, nullable=True),
            Column("listed_skill_count", Integer, nullable=True),
            Column("skill_tool_available", Boolean, nullable=True),
            Column("before_count", Integer, nullable=True),
            Column("visible_count", Integer, nullable=True),
            Column("after_count", Integer, nullable=True),
            Column("removed_count", Integer, nullable=True),
            Column("restored_count", Integer, nullable=True),
            Column("installed_count", Integer, nullable=True),
            Column("failure_count", Integer, nullable=True),
            Index(
                "ix_skill_obs_user_occurred_at",
                "user_id",
                "occurred_at",
            ),
            Index(
                "ix_skill_obs_tenant_time",
                "tenant_id",
                "occurred_at",
            ),
            Index(
                "ix_skill_obs_event_occurred_at",
                "event_name",
                "occurred_at",
            ),
            Index(
                "ix_skill_obs_skill_occurred_at",
                "skill_name",
                "occurred_at",
            ),
            Index(
                "ix_skill_obs_session_occurred_at",
                "session_id",
                "occurred_at",
            ),
        )
        self._metadata = metadata
        self._table = table

        async with self._engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
            if connection.dialect.name == "postgresql":
                await connection.exec_driver_sql(
                    "ALTER TABLE public.skill_observability_events "
                    "ADD COLUMN IF NOT EXISTS tenant_id UUID",
                )
                await connection.exec_driver_sql(
                    "ALTER TABLE public.skill_observability_events "
                    "ADD COLUMN IF NOT EXISTS membership_id UUID",
                )
                await connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_skill_obs_tenant_time "
                    "ON public.skill_observability_events "
                    "(tenant_id, occurred_at)",
                )

    async def record(self, event: SkillObservationEvent) -> None:
        """Insert one event into the append-only analysis table."""
        if self._engine is None or self._table is None:
            raise RuntimeError(
                "PostgresSkillObservationStore is not initialized; "
                "call initialize() first",
            )
        async with self._engine.begin() as connection:
            await connection.execute(self._table.insert().values(**event.to_row()))

    async def query_skill_analytics(
        self,
        *,
        start: datetime,
        end: datetime,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate bounded Skill events for the administrator dashboard.

        The repository owns the event-to-metric mapping so the API and the UI
        do not need to know the storage schema.  The first dashboard version
        intentionally reads only the dimensions needed by the current Skill
        contract; it does not expose prompts, Markdown, or arbitrary payloads.
        """
        if self._engine is None or self._table is None:
            raise RuntimeError(
                "PostgresSkillObservationStore is not initialized; "
                "call initialize() first",
            )
        if start >= end:
            raise ValueError("The analytics start time must be before the end time")

        from sqlalchemy import and_, select

        columns = (
            self._table.c.event_name,
            self._table.c.result,
            self._table.c.occurred_at,
            self._table.c.user_id,
            self._table.c.session_id,
            self._table.c.skill_name,
            self._table.c.error_code,
            self._table.c.duration_seconds,
            self._table.c.visible_count,
            self._table.c.after_count,
            self._table.c.skill_tool_available,
        )
        predicates = [
            self._table.c.occurred_at >= start,
            self._table.c.occurred_at < end,
        ]
        if tenant_id is not None:
            try:
                tenant_filter = UUID(str(tenant_id))
            except (TypeError, ValueError):
                tenant_filter = tenant_id
            predicates.append(self._table.c.tenant_id == tenant_filter)
        if user_id is not None:
            predicates.append(self._table.c.user_id == user_id)

        statement = (
            select(*columns)
            .where(and_(*predicates))
            .order_by(self._table.c.occurred_at.asc())
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()

        reconcile = {
            "success": 0,
            "partial": 0,
            "failed": 0,
            "skipped": 0,
            "started": 0,
        }
        lifecycle = {"exposed": 0, "invoked": 0, "completed": 0}
        completed = {"success": 0, "failed": 0, "other": 0}
        execution_failure_count = 0
        failure_by_stage: dict[str, int] = {}
        failure_by_error: dict[str, int] = {}
        recent_failures: list[dict[str, Any]] = []
        daily: dict[str, dict[str, int]] = {}
        top_skills: dict[str, int] = {}
        durations: list[float] = []
        latest_snapshot: dict[str, int] | None = None

        for row in rows:
            event_name = row["event_name"]
            result = row["result"]
            occurred_at = row["occurred_at"]
            day = occurred_at.date().isoformat()
            day_bucket = daily.setdefault(
                day,
                {"event_count": 0, "exposed": 0, "invoked": 0, "completed": 0},
            )
            day_bucket["event_count"] += 1

            failure = _failure_descriptor(
                event_name=event_name,
                result=result,
                error_code=row["error_code"],
                skill_tool_available=row["skill_tool_available"],
            )
            if failure is not None:
                stage, error_code = failure
                failure_by_stage[stage] = failure_by_stage.get(stage, 0) + 1
                failure_by_error[error_code] = failure_by_error.get(error_code, 0) + 1
                recent_failures.append(
                    {
                        "occurred_at": occurred_at,
                        "event_name": event_name,
                        "stage": stage,
                        "error_code": error_code,
                        "result": result,
                        "user_id": row["user_id"],
                        "session_id": row["session_id"],
                        "skill_name": row["skill_name"],
                        "duration_seconds": row["duration_seconds"],
                    },
                )

            if event_name == "skill.reconcile.started":
                reconcile["started"] += 1
            elif event_name == "skill.reconcile.completed":
                reconcile[result] = reconcile.get(result, 0) + 1
                if row["duration_seconds"] is not None:
                    durations.append(float(row["duration_seconds"]))
                latest_snapshot = {
                    "visible_count": int(row["visible_count"] or 0),
                    "after_count": int(row["after_count"] or 0),
                }
            elif event_name == "skill.exposed":
                lifecycle["exposed"] += 1
                day_bucket["exposed"] += 1
            elif event_name == "skill.invoked":
                lifecycle["invoked"] += 1
                day_bucket["invoked"] += 1
                skill_name = row["skill_name"]
                if skill_name:
                    top_skills[skill_name] = top_skills.get(skill_name, 0) + 1
            elif event_name == "skill.completed":
                lifecycle["completed"] += 1
                day_bucket["completed"] += 1
                if result == "success":
                    completed["success"] += 1
                elif result in {"failed", "error"}:
                    completed["failed"] += 1
                else:
                    completed["other"] += 1
                if result != "success":
                    execution_failure_count += 1

        exposed_count = lifecycle["exposed"]
        actual_usage_rate = (
            lifecycle["invoked"] / exposed_count if exposed_count else 0.0
        )
        daily_rows = [
            {"date": day, **values}
            for day, values in sorted(daily.items())
        ]
        top_skill_rows = [
            {"skill_name": skill_name, "invoked_count": count}
            for skill_name, count in sorted(
                top_skills.items(),
                key=lambda item: (-item[1], item[0]),
            )[:10]
        ]
        recent_failures = list(reversed(recent_failures[-20:]))
        failure_count = sum(failure_by_stage.values())

        return {
            "event_count": len(rows),
            "reconcile": reconcile,
            "lifecycle": lifecycle,
            "completed": completed,
            "failure_count": failure_count,
            "execution_failure_count": execution_failure_count,
            "execution_failure_rate": (
                execution_failure_count / lifecycle["completed"]
                if lifecycle["completed"]
                else 0.0
            ),
            "failure_by_stage": [
                {"key": key, "count": count}
                for key, count in sorted(
                    failure_by_stage.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ],
            "failure_by_error": [
                {"key": key, "count": count}
                for key, count in sorted(
                    failure_by_error.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ],
            "recent_failures": recent_failures,
            "actual_usage_rate": actual_usage_rate,
            "average_reconcile_duration_seconds": (
                sum(durations) / len(durations) if durations else None
            ),
            "latest_snapshot": latest_snapshot,
            "daily": daily_rows,
            "top_skills": top_skill_rows,
        }

    async def close(self) -> None:
        """Dispose only the engine owned by this repository."""
        if self._engine is not None and self._owns_engine:
            await self._engine.dispose()
        self._engine = None
        self._table = None
        self._metadata = None


__all__ = [
    "BestEffortSkillObservationSink",
    "NullSkillObservationSink",
    "PostgresSkillObservationStore",
    "SkillObservationEvent",
    "SkillObservationSink",
]
