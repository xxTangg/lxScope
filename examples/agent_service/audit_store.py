# -*- coding: utf-8 -*-
"""Application-owned tenant-scoped audit event repository.

The audit table is created by the persistence foundation migration.  This
adapter keeps the Redis audit list as a compatibility fallback while making
the database the queryable source for tenant and platform audit views.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID


def _uuid(value: Any) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def visible_audit_events(
    events: list[dict[str, Any]],
    *,
    tenant_id: str | None,
    platform_admin: bool = False,
) -> list[dict[str, Any]]:
    """Apply the audit visibility rule without trusting a client tenant id."""

    if platform_admin:
        return list(events)
    if tenant_id is None:
        return []
    normalized = str(tenant_id)
    return [
        event
        for event in events
        if str(event.get("tenant_id") or "") == normalized
    ]


class AuditEventStore:
    """Persist and query structured audit events in ``longxin_app``."""

    def __init__(self, database: Any) -> None:
        self._engine = getattr(database, "engine", database)
        self._table: Any | None = None
        self._metadata: Any | None = None

    def _table_definition(self) -> Any:
        if self._table is not None:
            return self._table
        from sqlalchemy import (
            BigInteger,
            Column,
            DateTime,
            Integer,
            JSON,
            MetaData,
            String,
            Table,
            Uuid,
        )

        schema = "longxin_app" if self._engine.dialect.name == "postgresql" else None
        metadata = MetaData(schema=schema)
        self._metadata = metadata
        self._table = Table(
            "audit_events",
            metadata,
            Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True),
            Column("tenant_id", Uuid(as_uuid=True), nullable=True),
            Column("actor_membership_id", Uuid(as_uuid=True), nullable=True),
            Column("actor_type", String(32), nullable=True),
            Column("action", String(128), nullable=False),
            Column("resource_type", String(64), nullable=True),
            Column("resource_id", String(128), nullable=True),
            Column("resource", JSON, nullable=False),
            Column("request_id", String(128), nullable=True),
            Column("result", String(32), nullable=True),
            Column("summary", String, nullable=True),
            Column("metadata", JSON, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        return self._table

    async def record(self, event: dict[str, Any]) -> None:
        table = self._table_definition()
        tenant_id = _uuid(event.get("tenant_id"))
        membership_id = _uuid(event.get("actor_membership_id"))
        metadata = {
            "event_id": event.get("event_id"),
            "target_user_id": event.get("target_user_id"),
            "target_user_name": event.get("target_user_name"),
            "reason": event.get("reason") or "",
            "resource": event.get("resource") or {},
            "actor_id": event.get("actor_id"),
            "actor_name": event.get("actor_name"),
            "status": event.get("status") or "completed",
            "result_summary": event.get("result_summary"),
        }
        values = {
            "tenant_id": tenant_id,
            # The FK in the foundation migration requires a real membership
            # UUID. System/local compatibility actors are intentionally null.
            "actor_membership_id": membership_id if tenant_id else None,
            "actor_type": event.get("actor_type"),
            "action": event.get("action") or "unknown",
            "resource_type": event.get("resource_type"),
            "resource_id": event.get("resource_id"),
            "resource": event.get("resource") or {},
            "request_id": event.get("request_id"),
            "result": event.get("status") or "completed",
            "summary": event.get("result_summary") or event.get("reason") or "",
            "metadata": metadata,
            "created_at": _parse_datetime(event.get("created_at")),
        }
        async with self._engine.begin() as connection:
            await connection.execute(table.insert().values(**values))

    async def list_events(
        self,
        *,
        limit: int = 200,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        table = self._table_definition()
        from sqlalchemy import select

        predicates = []
        if tenant_id is not None:
            parsed_tenant = _uuid(tenant_id) or tenant_id
            predicates.append(table.c.tenant_id == parsed_tenant)
        statement = (
            select(table)
            .where(*predicates)
            .order_by(table.c.created_at.desc(), table.c.id.desc())
            .limit(max(1, min(int(limit), 500)))
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [_view_from_row(dict(row)) for row in rows]


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _view_from_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
        resource = row.get("resource") or metadata.get("resource")
    if not isinstance(resource, dict):
        resource = {
            "type": row.get("resource_type"),
            "id": row.get("resource_id"),
        }
    return {
        "event_id": str(metadata.get("event_id") or row.get("id")),
        "tenant_id": str(row["tenant_id"]) if row.get("tenant_id") else None,
        "actor_membership_id": (
            str(row["actor_membership_id"])
            if row.get("actor_membership_id")
            else None
        ),
        "actor_type": row.get("actor_type") or "system",
        "actor_id": str(metadata.get("actor_id") or ""),
        "actor_name": str(metadata.get("actor_name") or ""),
        "target_user_id": metadata.get("target_user_id"),
        "target_user_name": metadata.get("target_user_name"),
        "action": row.get("action") or "unknown",
        "resource_type": row.get("resource_type"),
        "resource_id": row.get("resource_id"),
        "resource": resource,
        "reason": metadata.get("reason") or "",
        "request_id": row.get("request_id") or "",
        "status": metadata.get("status") or row.get("result") or "completed",
        "result_summary": metadata.get("result_summary") or row.get("summary"),
        "created_at": (
            row["created_at"].isoformat()
            if hasattr(row.get("created_at"), "isoformat")
            else str(row.get("created_at") or "")
        ),
    }


__all__ = ["AuditEventStore", "visible_audit_events"]
