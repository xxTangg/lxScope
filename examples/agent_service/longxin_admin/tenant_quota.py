# -*- coding: utf-8 -*-
"""Tenant-scoped quota and usage service.

This is an lxScope application-layer boundary.  AgentScope continues to
receive the membership id as ``user_id``; quota ownership is resolved from
the trusted TenantIdentity and persisted in the existing application tables.
"""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

try:
    from fastapi import HTTPException, status
except ModuleNotFoundError:  # pragma: no cover - lightweight unit tests
    class HTTPException(RuntimeError):
        def __init__(self, status_code: int, detail: Any) -> None:
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    class _Status:
        HTTP_403_FORBIDDEN = 403
        HTTP_404_NOT_FOUND = 404
        HTTP_409_CONFLICT = 409
        HTTP_503_SERVICE_UNAVAILABLE = 503

    status = _Status()

try:
    from identity.dependencies import (
        get_bound_membership_id,
        get_bound_tenant_id,
        get_bound_tenant_identity,
    )
except ModuleNotFoundError:  # pragma: no cover - package import mode
    try:
        from examples.agent_service.identity.dependencies import (
            get_bound_membership_id,
            get_bound_tenant_id,
            get_bound_tenant_identity,
        )
    except ModuleNotFoundError:  # pragma: no cover - pure policy tests
        def get_bound_membership_id() -> UUID | None:
            return None

        def get_bound_tenant_id() -> UUID | None:
            return None

        def get_bound_tenant_identity() -> Any | None:
            return None

try:
    from identity.permissions import PLATFORM_MANAGE, PLATFORM_OBSERVE, has_permission
except ModuleNotFoundError:  # pragma: no cover - pure policy tests
    PLATFORM_MANAGE = "platform:manage"
    PLATFORM_OBSERVE = "platform:observe"

    def has_permission(permissions: Any, required: str) -> bool:
        return required in set(permissions or ())

try:
    from agentscope.middleware import MiddlewareBase
except ModuleNotFoundError:  # pragma: no cover - pure policy tests
    class MiddlewareBase:  # type: ignore[no-redef]
        pass

# Keep this module importable for policy tests and lightweight tooling that do
# not install the optional FastAPI runtime. The plan catalog uses the same
# stable persisted code.
UNASSIGNED_PLAN_ID = "plan_none"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _usage(result: Any) -> tuple[int, int, int, int]:
    usage = getattr(result, "usage", None)
    if usage is None:
        return 0, 0, 0, 0
    return tuple(
        max(0, int(getattr(usage, name, 0) or 0))
        for name in (
            "input_tokens",
            "output_tokens",
            "cache_input_tokens",
            "cache_creation_input_tokens",
        )
    )


def model_cost(model: str | None, input_tokens: int, output_tokens: int) -> Decimal:
    """Calculate the recorded cost without putting pricing in quota logic.

    Pricing can be supplied as ``LXSCOPE_MODEL_PRICING_JSON`` with values in
    currency per million input/output tokens.  Unknown models are recorded
    with zero monetary cost while their token usage remains authoritative.
    """

    try:
        pricing = json.loads(os.getenv("LXSCOPE_MODEL_PRICING_JSON", "{}"))
    except json.JSONDecodeError:
        pricing = {}
    value = pricing.get(model or "", {}) if isinstance(pricing, dict) else {}
    if not isinstance(value, dict):
        value = {}
    input_rate = Decimal(str(value.get("input_per_million", 0) or 0))
    output_rate = Decimal(str(value.get("output_per_million", 0) or 0))
    return (
        Decimal(input_tokens) * input_rate
        + Decimal(output_tokens) * output_rate
    ) / Decimal(1_000_000)


def tenant_balance_after(current_balance: int, delta_tokens: int) -> int:
    """Apply one tenant-local balance delta and reject negative balances."""

    result = int(current_balance) + int(delta_tokens)
    if result < 0:
        raise _error(
            "quota_insufficient",
            "The tenant token balance is insufficient.",
            status.HTTP_409_CONFLICT,
        )
    return result


def _error(code: str, message: str, code_number: int) -> HTTPException:
    return HTTPException(
        status_code=code_number,
        detail={"code": code, "message": message},
    )


class TenantQuotaService:
    """Use existing tenant billing tables as the quota source of truth."""

    def __init__(self, database_provider: Callable[[], Any | None] | Any) -> None:
        self._database_provider = database_provider
        self._tables: dict[str, Any] | None = None

    def _database(self) -> Any | None:
        value = self._database_provider
        return value() if callable(value) else value

    def _engine(self) -> Any | None:
        database = self._database()
        if database is None:
            return None
        return getattr(database, "engine", database)

    @property
    def enabled(self) -> bool:
        return self._engine() is not None and get_bound_tenant_id() is not None

    def _context(
        self,
        *,
        tenant_id: Any | None = None,
        membership_id: Any | None = None,
    ) -> tuple[UUID, UUID | None]:
        tenant = _uuid(tenant_id if tenant_id is not None else get_bound_tenant_id())
        if tenant is None:
            raise _error(
                "tenant_context_required",
                "A verified tenant context is required for quota operations.",
                status.HTTP_403_FORBIDDEN,
            )
        membership = _uuid(
            membership_id
            if membership_id is not None
            else get_bound_membership_id()
        )
        return tenant, membership

    def _table_definitions(self) -> dict[str, Any]:
        if self._tables is not None:
            return self._tables
        engine = self._engine()
        if engine is None:
            raise _error(
                "application_database_unavailable",
                "The application database is not ready.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        from sqlalchemy import (
            BigInteger,
            Column,
            DateTime,
            Integer,
            JSON,
            MetaData,
            Numeric,
            String,
            Table,
            Uuid,
            text,
        )

        schema = "longxin_app" if engine.dialect.name == "postgresql" else None
        metadata = MetaData(schema=schema)
        uuid_type = Uuid(as_uuid=True)
        self._tables = {
            "accounts": Table(
                "tenant_accounts",
                metadata,
                Column("id", uuid_type, primary_key=True),
                Column("tenant_id", uuid_type, nullable=False),
                Column("plan_code", String(64)),
                Column("status", String(32), nullable=False),
                Column("token_balance", BigInteger, nullable=False),
                Column("version", BigInteger, nullable=False),
                Column("created_at", DateTime(timezone=True)),
                Column("updated_at", DateTime(timezone=True)),
            ),
            "plan_orders": Table(
                "plan_orders",
                metadata,
                Column("id", uuid_type, primary_key=True),
                Column("tenant_id", uuid_type, nullable=False),
                Column("plan_code", String(64), nullable=False),
                Column("order_type", String(32), nullable=False),
                Column("status", String(32), nullable=False),
                Column("requested_by_membership_id", uuid_type),
                Column("reviewed_by_membership_id", uuid_type),
                Column("note", String),
                Column("decision_reason", String),
                Column("allocated_tokens", BigInteger, nullable=False),
                Column("metadata", JSON, nullable=False),
                Column("created_at", DateTime(timezone=True)),
                Column("updated_at", DateTime(timezone=True)),
            ),
            "recharge_orders": Table(
                "recharge_orders",
                metadata,
                Column("id", uuid_type, primary_key=True),
                Column("tenant_id", uuid_type, nullable=False),
                Column("external_system", String(64)),
                Column("external_order_id", String(128)),
                Column("amount", Numeric(18, 2)),
                Column("credits", BigInteger),
                Column("status", String(32), nullable=False),
                Column("requested_by_membership_id", uuid_type),
                Column("reviewed_by_membership_id", uuid_type),
                Column("metadata", JSON, nullable=False),
                Column("created_at", DateTime(timezone=True)),
                Column("updated_at", DateTime(timezone=True)),
            ),
            "ledger": Table(
                "quota_ledger",
                metadata,
                Column("id", BigInteger, primary_key=True),
                Column("tenant_id", uuid_type, nullable=False),
                Column("membership_id", uuid_type),
                Column("entry_type", String(64), nullable=False),
                Column("delta_tokens", BigInteger, nullable=False),
                Column("balance_after", BigInteger, nullable=False),
                Column("source_type", String(64)),
                Column("source_id", String(128)),
                Column("idempotency_key", String(128)),
                Column("created_by_membership_id", uuid_type),
                Column("model", String(128)),
                Column("tokens", BigInteger),
                Column("cost", Numeric(20, 8)),
                Column("created_at", DateTime(timezone=True)),
            ),
        }
        return self._tables

    @staticmethod
    async def _ensure_account(connection: Any, accounts: Any, tenant_id: UUID) -> None:
        from sqlalchemy import select

        exists = await connection.execute(
            select(accounts.c.id).where(accounts.c.tenant_id == tenant_id).limit(1),
        )
        if exists.scalar_one_or_none() is not None:
            return
        values = {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "plan_code": UNASSIGNED_PLAN_ID,
            "status": "active",
            "token_balance": 0,
            "version": 0,
            "created_at": _now(),
            "updated_at": _now(),
        }
        if connection.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif connection.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            insert = None
        statement = (
            insert(accounts).values(**values).on_conflict_do_nothing(
                index_elements=[accounts.c.tenant_id],
            )
            if insert is not None
            else accounts.insert().values(**values)
        )
        await connection.execute(statement)

    async def snapshot(
        self,
        *,
        tenant_id: Any | None = None,
    ) -> dict[str, Any]:
        tenant, _ = self._context(tenant_id=tenant_id)
        tables = self._table_definitions()
        from sqlalchemy import func, select

        async with self._engine().begin() as connection:
            await self._ensure_account(connection, tables["accounts"], tenant)
            account = (
                await connection.execute(
                    select(tables["accounts"]).where(
                        tables["accounts"].c.tenant_id == tenant,
                    ),
                )
            ).mappings().one()
            usage = (
                await connection.execute(
                    select(func.coalesce(func.sum(tables["ledger"].c.tokens), 0)).where(
                        tables["ledger"].c.tenant_id == tenant,
                        tables["ledger"].c.entry_type == "model_usage",
                    ),
                )
            ).scalar_one()
            credits = (
                await connection.execute(
                    select(func.coalesce(func.sum(tables["recharge_orders"].c.credits), 0)).where(
                        tables["recharge_orders"].c.tenant_id == tenant,
                        tables["recharge_orders"].c.status.in_(("approved", "completed")),
                    ),
                )
            ).scalar_one()
            recharged = (
                await connection.execute(
                    select(func.coalesce(func.sum(tables["recharge_orders"].c.amount), 0)).where(
                        tables["recharge_orders"].c.tenant_id == tenant,
                        tables["recharge_orders"].c.status.in_(("approved", "completed")),
                    ),
                )
            ).scalar_one()
        return {
            "system_id": f"tenant-{tenant}",
            "pool_tokens": int(account["token_balance"] or 0),
            "total_recharged": str(recharged or "0.00"),
            "cumulative_consumed": int(usage or 0),
            "cumulative_credits": int(credits or 0),
            "test_default_tokens": 0,
            "updated_at": _iso(account.get("updated_at")) or _now().isoformat(),
        }

    async def account(self, *, tenant_id: Any | None = None) -> dict[str, Any]:
        """Return the current tenant account without exposing another tenant."""

        tenant, _ = self._context(tenant_id=tenant_id)
        tables = self._table_definitions()
        from sqlalchemy import select

        async with self._engine().begin() as connection:
            await self._ensure_account(connection, tables["accounts"], tenant)
            row = (
                await connection.execute(
                    select(tables["accounts"]).where(
                        tables["accounts"].c.tenant_id == tenant,
                    ),
                )
            ).mappings().one()
        return dict(row)

    @staticmethod
    def _order_id(value: Any) -> UUID:
        parsed = _uuid(value)
        if parsed is None:
            raise _error("order_not_found", "Plan order not found.", 404)
        return parsed

    async def create_plan_order(
        self,
        *,
        plan_code: str,
        order_type: str,
        note: str | None,
        requested_by_membership_id: Any | None,
        metadata: dict[str, Any],
        tenant_id: Any | None = None,
    ) -> dict[str, Any]:
        tenant, current_membership = self._context(
            tenant_id=tenant_id,
            membership_id=requested_by_membership_id,
        )
        tables = self._table_definitions()
        from sqlalchemy import select

        async with self._engine().begin() as connection:
            await self._ensure_account(connection, tables["accounts"], tenant)
            pending = (
                await connection.execute(
                    select(tables["plan_orders"].c.id).where(
                        tables["plan_orders"].c.tenant_id == tenant,
                        tables["plan_orders"].c.requested_by_membership_id
                        == (current_membership or _uuid(requested_by_membership_id)),
                        tables["plan_orders"].c.status == "pending",
                    ).limit(1),
                )
            ).scalar_one_or_none()
            if pending is not None:
                raise _error(
                    "pending_order_exists",
                    "A pending plan order already exists for this membership.",
                    status.HTTP_409_CONFLICT,
                )
            order_id = uuid4()
            await connection.execute(
                tables["plan_orders"].insert().values(
                    id=order_id,
                    tenant_id=tenant,
                    plan_code=plan_code,
                    order_type=order_type,
                    status="pending",
                    requested_by_membership_id=current_membership
                    or _uuid(requested_by_membership_id),
                    note=note,
                    decision_reason=None,
                    allocated_tokens=0,
                    metadata=metadata,
                    created_at=_now(),
                    updated_at=_now(),
                ),
            )
        return {
            "id": order_id,
            "tenant_id": tenant,
            "plan_code": plan_code,
            "order_type": order_type,
            "status": "pending",
            "requested_by_membership_id": current_membership
            or _uuid(requested_by_membership_id),
            "note": note,
            "decision_reason": None,
            "allocated_tokens": 0,
            "metadata": metadata,
            "created_at": _now(),
            "updated_at": _now(),
        }

    async def list_plan_orders(
        self,
        *,
        membership_id: Any | None = None,
        order_status: str | None = None,
        limit: int = 50,
        tenant_id: Any | None = None,
    ) -> list[dict[str, Any]]:
        tenant, current_membership = self._context(tenant_id=tenant_id)
        tables = self._table_definitions()
        from sqlalchemy import select

        clauses = [tables["plan_orders"].c.tenant_id == tenant]
        if membership_id is not None:
            clauses.append(
                tables["plan_orders"].c.requested_by_membership_id
                == (_uuid(membership_id) or current_membership),
            )
        if order_status:
            clauses.append(tables["plan_orders"].c.status == order_status)
        async with self._engine().connect() as connection:
            rows = (
                await connection.execute(
                    select(tables["plan_orders"])
                    .where(*clauses)
                    .order_by(tables["plan_orders"].c.created_at.desc())
                    .limit(limit),
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def get_plan_order(
        self,
        order_id: Any,
        *,
        tenant_id: Any | None = None,
    ) -> dict[str, Any]:
        tenant, _ = self._context(tenant_id=tenant_id)
        tables = self._table_definitions()
        from sqlalchemy import select

        async with self._engine().connect() as connection:
            row = (
                await connection.execute(
                    select(tables["plan_orders"]).where(
                        tables["plan_orders"].c.tenant_id == tenant,
                        tables["plan_orders"].c.id == self._order_id(order_id),
                    ),
                )
            ).mappings().first()
        if row is None:
            raise _error("order_not_found", "Plan order not found.", 404)
        return dict(row)

    async def approve_plan_order(
        self,
        order_id: Any,
        *,
        plan_code: str,
        monthly_quota: int,
        actor_membership_id: Any | None,
        plan_metadata: dict[str, Any],
        tenant_id: Any | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        tenant, actor_membership = self._context(
            tenant_id=tenant_id,
            membership_id=actor_membership_id,
        )
        tables = self._table_definitions()
        from sqlalchemy import select, update

        async with self._engine().begin() as connection:
            await self._ensure_account(connection, tables["accounts"], tenant)
            order = (
                await connection.execute(
                    select(tables["plan_orders"])
                    .where(
                        tables["plan_orders"].c.tenant_id == tenant,
                        tables["plan_orders"].c.id == self._order_id(order_id),
                    )
                    .with_for_update(),
                )
            ).mappings().one_or_none()
            if order is None:
                raise _error("order_not_found", "Plan order not found.", 404)
            if order["status"] != "pending":
                raise _error("order_not_pending", "Only pending orders can be approved.", 409)
            account = (
                await connection.execute(
                    select(tables["accounts"])
                    .where(tables["accounts"].c.tenant_id == tenant)
                    .with_for_update(),
                )
            ).mappings().one()
            old_plan = str(account["plan_code"] or UNASSIGNED_PLAN_ID)
            old_quota = int((order.get("metadata") or {}).get("current_monthly_quota", 0))
            if order["order_type"] in {"activation", "renewal"}:
                allocation = monthly_quota
            else:
                allocation = monthly_quota - old_quota
            balance_after = int(account["token_balance"] or 0) + allocation
            if balance_after < 0:
                raise _error(
                    "quota_insufficient",
                    "The tenant token balance cannot become negative.",
                    status.HTTP_409_CONFLICT,
                )
            await connection.execute(
                update(tables["accounts"])
                .where(tables["accounts"].c.tenant_id == tenant)
                .values(
                    plan_code=plan_code,
                    status="active",
                    token_balance=balance_after,
                    version=tables["accounts"].c.version + 1,
                    updated_at=_now(),
                ),
            )
            await connection.execute(
                tables["ledger"].insert().values(
                    tenant_id=tenant,
                    membership_id=order["requested_by_membership_id"],
                    entry_type="plan_allocation" if allocation >= 0 else "plan_refund",
                    delta_tokens=allocation,
                    balance_after=balance_after,
                    source_type="plan_order",
                    source_id=str(order["id"]),
                    idempotency_key=None,
                    created_by_membership_id=actor_membership,
                    model=None,
                    tokens=abs(allocation),
                    cost=Decimal("0"),
                    created_at=_now(),
                ),
            )
            metadata = dict(order.get("metadata") or {})
            metadata.update(plan_metadata)
            metadata["monthly_quota"] = monthly_quota
            metadata["plan_started_at"] = _now()
            await connection.execute(
                update(tables["plan_orders"])
                .where(tables["plan_orders"].c.id == order["id"])
                .values(
                    status="approved",
                    reviewed_by_membership_id=actor_membership,
                    decision_reason=reason,
                    allocated_tokens=allocation,
                    metadata=metadata,
                    updated_at=_now(),
                ),
            )
        order = dict(order)
        order.update(
            {
                "status": "approved",
                "reviewed_by_membership_id": actor_membership,
                "decision_reason": reason,
                "allocated_tokens": allocation,
                "metadata": metadata,
            },
        )
        return order

    async def reject_plan_order(
        self,
        order_id: Any,
        *,
        actor_membership_id: Any | None,
        reason: str,
        tenant_id: Any | None = None,
    ) -> dict[str, Any]:
        tenant, actor_membership = self._context(
            tenant_id=tenant_id,
            membership_id=actor_membership_id,
        )
        tables = self._table_definitions()
        from sqlalchemy import select, update

        async with self._engine().begin() as connection:
            row = (
                await connection.execute(
                    select(tables["plan_orders"])
                    .where(
                        tables["plan_orders"].c.tenant_id == tenant,
                        tables["plan_orders"].c.id == self._order_id(order_id),
                    )
                    .with_for_update(),
                )
            ).mappings().one_or_none()
            if row is None:
                raise _error("order_not_found", "Plan order not found.", 404)
            if row["status"] != "pending":
                raise _error("order_not_pending", "Only pending orders can be rejected.", 409)
            await connection.execute(
                update(tables["plan_orders"])
                .where(tables["plan_orders"].c.id == row["id"])
                .values(
                    status="rejected",
                    reviewed_by_membership_id=actor_membership,
                    decision_reason=reason,
                    updated_at=_now(),
                ),
            )
        result = dict(row)
        result.update(
            {
                "status": "rejected",
                "reviewed_by_membership_id": actor_membership,
                "decision_reason": reason,
            },
        )
        return result

    async def assign_plan(
        self,
        *,
        membership_id: Any,
        plan_code: str,
        monthly_quota: int,
        allocation_tokens: int | None = None,
        operator_membership_id: Any | None,
        tenant_id: Any | None = None,
    ) -> dict[str, Any]:
        tenant, operator = self._context(
            tenant_id=tenant_id,
            membership_id=operator_membership_id,
        )
        account = await self.account(tenant_id=tenant)
        allocation = monthly_quota if allocation_tokens is None else allocation_tokens
        result = await self.apply_delta(
            allocation,
            entry_type="admin_plan_allocation",
            membership_id=membership_id,
            created_by_membership_id=operator,
            source_type="plan",
            source_id="admin-assignment",
            tenant_id=tenant,
        )
        tables = self._table_definitions()
        from sqlalchemy import update

        async with self._engine().begin() as connection:
            await connection.execute(
                update(tables["accounts"])
                .where(tables["accounts"].c.tenant_id == tenant)
                .values(plan_code=plan_code, status="active", updated_at=_now()),
            )
        return result

    async def set_balance(self, balance: int, *, tenant_id: Any | None = None) -> None:
        if balance < 0:
            raise _error("quota_insufficient", "Tenant token balance cannot be negative.", 409)
        tenant, _ = self._context(tenant_id=tenant_id)
        tables = self._table_definitions()
        from sqlalchemy import update

        async with self._engine().begin() as connection:
            await self._ensure_account(connection, tables["accounts"], tenant)
            await connection.execute(
                update(tables["accounts"])
                .where(tables["accounts"].c.tenant_id == tenant)
                .values(token_balance=balance, updated_at=_now()),
            )

    async def ensure_available(self, *, tenant_id: Any | None = None) -> int:
        snapshot = await self.snapshot(tenant_id=tenant_id)
        if int(snapshot["pool_tokens"]) <= 0:
            raise _error(
                "quota_exhausted",
                "The tenant token balance is exhausted.",
                status.HTTP_403_FORBIDDEN,
            )
        return int(snapshot["pool_tokens"])

    async def apply_delta(
        self,
        delta_tokens: int,
        *,
        entry_type: str,
        membership_id: Any | None = None,
        created_by_membership_id: Any | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        idempotency_key: str | None = None,
        model: str | None = None,
        tokens: int | None = None,
        cost: Decimal | int | float | str | None = None,
        tenant_id: Any | None = None,
    ) -> dict[str, Any]:
        tenant, current_membership = self._context(
            tenant_id=tenant_id,
            membership_id=membership_id,
        )
        tables = self._table_definitions()
        from sqlalchemy import select, update

        async with self._engine().begin() as connection:
            await self._ensure_account(connection, tables["accounts"], tenant)
            if idempotency_key:
                existing = (
                    await connection.execute(
                        select(tables["ledger"]).where(
                            tables["ledger"].c.tenant_id == tenant,
                            tables["ledger"].c.idempotency_key == idempotency_key,
                        ).limit(1),
                    )
                ).mappings().first()
                if existing is not None:
                    return dict(existing)
            account = (
                await connection.execute(
                    select(tables["accounts"])
                    .where(tables["accounts"].c.tenant_id == tenant)
                    .with_for_update(),
                )
            ).mappings().one()
            balance_before = int(account["token_balance"] or 0)
            balance_after = tenant_balance_after(balance_before, delta_tokens)
            await connection.execute(
                update(tables["accounts"])
                .where(tables["accounts"].c.tenant_id == tenant)
                .values(
                    token_balance=balance_after,
                    version=tables["accounts"].c.version + 1,
                    updated_at=_now(),
                ),
            )
            result = await connection.execute(
                tables["ledger"].insert().values(
                    tenant_id=tenant,
                    membership_id=_uuid(membership_id) or current_membership,
                    entry_type=entry_type,
                    delta_tokens=int(delta_tokens),
                    balance_after=balance_after,
                    source_type=source_type,
                    source_id=source_id,
                    idempotency_key=idempotency_key,
                    created_by_membership_id=_uuid(created_by_membership_id),
                    model=model,
                    tokens=(abs(int(delta_tokens)) if tokens is None else int(tokens)),
                    cost=Decimal(str(cost or 0)),
                    created_at=_now(),
                ),
            )
            ledger_id = result.inserted_primary_key[0] if result.inserted_primary_key else None
        return {
            "tenant_id": str(tenant),
            "membership_id": str(_uuid(membership_id) or current_membership)
            if (_uuid(membership_id) or current_membership)
            else None,
            "delta_tokens": int(delta_tokens),
            "balance_after": balance_after,
            "ledger_id": str(ledger_id) if ledger_id is not None else "",
            "model": model,
            "tokens": abs(int(delta_tokens)) if tokens is None else int(tokens),
            "cost": str(cost or 0),
        }

    async def record_model_usage(
        self,
        *,
        model: str | None,
        input_tokens: int,
        output_tokens: int,
        cache_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        membership_id: Any | None = None,
        source_id: str | None = None,
        idempotency_key: str | None = None,
        tenant_id: Any | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled and tenant_id is None:
            return None
        model = str(model) if model is not None else None
        # AgentScope's persisted total is input + output. Cache counters are
        # retained for future pricing extensions but are not double-counted
        # into the tenant token balance.
        del cache_input_tokens, cache_creation_input_tokens
        total = max(0, int(input_tokens)) + max(0, int(output_tokens))
        return await self.apply_delta(
            -total,
            entry_type="model_usage",
            membership_id=membership_id,
            source_type="model",
            source_id=source_id,
            idempotency_key=idempotency_key or f"usage-{uuid4().hex}",
            model=model,
            tokens=total,
            cost=model_cost(model, input_tokens, output_tokens),
            tenant_id=tenant_id,
        )

    async def append_entry(
        self,
        *,
        entry_type: str,
        delta_tokens: int,
        balance_after: int,
        membership_id: Any | None = None,
        created_by_membership_id: Any | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        idempotency_key: str | None = None,
        cost: Decimal | int | float | str | None = None,
        tenant_id: Any | None = None,
    ) -> dict[str, Any]:
        """Append an already-applied adjustment without changing balance."""

        tenant, current_membership = self._context(
            tenant_id=tenant_id,
            membership_id=membership_id,
        )
        tables = self._table_definitions()
        async with self._engine().begin() as connection:
            await self._ensure_account(connection, tables["accounts"], tenant)
            result = await connection.execute(
                tables["ledger"].insert().values(
                    tenant_id=tenant,
                    membership_id=_uuid(membership_id) or current_membership,
                    entry_type=entry_type,
                    delta_tokens=int(delta_tokens),
                    balance_after=int(balance_after),
                    source_type=source_type,
                    source_id=source_id,
                    idempotency_key=idempotency_key,
                    created_by_membership_id=_uuid(created_by_membership_id),
                    model=None,
                    tokens=abs(int(delta_tokens)),
                    cost=Decimal(str(cost or 0)),
                    created_at=_now(),
                ),
            )
        ledger_id = result.inserted_primary_key[0] if result.inserted_primary_key else ""
        return {
            "ledger_id": str(ledger_id),
            "tenant_id": str(tenant),
            "delta_tokens": int(delta_tokens),
            "balance_after": int(balance_after),
            "created_at": _now().isoformat(),
        }

    async def update_plan(
        self,
        *,
        plan_code: str,
        delta_tokens: int,
        entry_type: str,
        source_id: str,
        membership_id: Any | None = None,
        created_by_membership_id: Any | None = None,
        tenant_id: Any | None = None,
    ) -> dict[str, Any]:
        result = await self.apply_delta(
            delta_tokens,
            entry_type=entry_type,
            membership_id=membership_id,
            created_by_membership_id=created_by_membership_id,
            source_type="plan",
            source_id=source_id,
            tenant_id=tenant_id,
        )
        tenant, _ = self._context(tenant_id=tenant_id)
        tables = self._table_definitions()
        from sqlalchemy import update

        async with self._engine().begin() as connection:
            await connection.execute(
                update(tables["accounts"])
                .where(tables["accounts"].c.tenant_id == tenant)
                .values(plan_code=plan_code, status="active", updated_at=_now()),
            )
        return result

    async def list_ledger(
        self,
        *,
        limit: int = 100,
        entry_type: str | None = None,
        membership_id: Any | None = None,
        tenant_id: Any | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        global_scope: bool = False,
    ) -> list[dict[str, Any]]:
        if global_scope:
            identity = get_bound_tenant_identity()
            permissions = getattr(identity, "permissions", ())
            if not (
                has_permission(permissions, PLATFORM_MANAGE)
                or has_permission(permissions, PLATFORM_OBSERVE)
            ):
                raise _error(
                    "platform_permission_required",
                    "Platform observability permission is required.",
                    status.HTTP_403_FORBIDDEN,
                )
            tenant = None
        else:
            tenant, _ = self._context(tenant_id=tenant_id)
        tables = self._table_definitions()
        from sqlalchemy import select

        clauses = []
        if tenant is not None:
            clauses.append(tables["ledger"].c.tenant_id == tenant)
        if entry_type:
            clauses.append(tables["ledger"].c.entry_type == entry_type)
        if membership_id is not None:
            clauses.append(tables["ledger"].c.membership_id == _uuid(membership_id))
        if since is not None:
            clauses.append(tables["ledger"].c.created_at >= since)
        if until is not None:
            clauses.append(tables["ledger"].c.created_at < until)
        async with self._engine().connect() as connection:
            rows = (
                await connection.execute(
                    select(tables["ledger"])
                    .where(*clauses)
                    .order_by(tables["ledger"].c.created_at.desc())
                    .limit(limit),
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def create_recharge_order(
        self,
        *,
        external_order_id: str,
        amount: str,
        requested_by_membership_id: Any | None = None,
        metadata: dict[str, Any] | None = None,
        tenant_id: Any | None = None,
    ) -> dict[str, Any]:
        tenant, current_membership = self._context(
            tenant_id=tenant_id,
            membership_id=requested_by_membership_id,
        )
        tables = self._table_definitions()
        from sqlalchemy import select

        async with self._engine().begin() as connection:
            existing = (
                await connection.execute(
                    select(tables["recharge_orders"]).where(
                        tables["recharge_orders"].c.tenant_id == tenant,
                        tables["recharge_orders"].c.external_order_id == external_order_id,
                    ).limit(1),
                )
            ).mappings().first()
            if existing is not None:
                return dict(existing)
            order_id = uuid4()
            await connection.execute(
                tables["recharge_orders"].insert().values(
                    id=order_id,
                    tenant_id=tenant,
                    external_system="sales_hub",
                    external_order_id=external_order_id,
                    amount=Decimal(str(amount)),
                    credits=None,
                    status="pending",
                    requested_by_membership_id=current_membership,
                    reviewed_by_membership_id=None,
                    metadata=metadata or {},
                    created_at=_now(),
                    updated_at=_now(),
                ),
            )
        return {
            "id": order_id,
            "tenant_id": tenant,
            "external_order_id": external_order_id,
            "amount": Decimal(str(amount)),
            "credits": None,
            "status": "pending",
            "requested_by_membership_id": current_membership,
            "metadata": metadata or {},
            "created_at": _now(),
        }

    async def list_recharge_orders(
        self,
        *,
        limit: int = 50,
        tenant_id: Any | None = None,
    ) -> list[dict[str, Any]]:
        tenant, _ = self._context(tenant_id=tenant_id)
        tables = self._table_definitions()
        from sqlalchemy import select

        async with self._engine().connect() as connection:
            rows = (
                await connection.execute(
                    select(tables["recharge_orders"])
                    .where(tables["recharge_orders"].c.tenant_id == tenant)
                    .order_by(tables["recharge_orders"].c.created_at.desc())
                    .limit(limit),
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def credit_recharge(
        self,
        *,
        external_order_id: str,
        tokens: int,
        amount: str,
        created_by_membership_id: Any | None = None,
        idempotency_key: str | None = None,
        tenant_id: Any | None = None,
    ) -> dict[str, Any]:
        tenant, current_membership = self._context(
            tenant_id=tenant_id,
            membership_id=created_by_membership_id,
        )
        tables = self._table_definitions()
        from sqlalchemy import select, update

        async with self._engine().begin() as connection:
            order = (
                await connection.execute(
                    select(tables["recharge_orders"])
                    .where(
                        tables["recharge_orders"].c.tenant_id == tenant,
                        tables["recharge_orders"].c.external_order_id == external_order_id,
                    )
                    .with_for_update(),
                )
            ).mappings().one_or_none()
            if order is None:
                raise _error("recharge_order_not_found", "Recharge order not found.", 404)
            if order["status"] in {"approved", "completed"}:
                return {
                    "external_order_id": external_order_id,
                    "tokens": int(order.get("credits") or 0),
                    "status": order["status"],
                }
            await self._ensure_account(connection, tables["accounts"], tenant)
            account = (
                await connection.execute(
                    select(tables["accounts"])
                    .where(tables["accounts"].c.tenant_id == tenant)
                    .with_for_update(),
                )
            ).mappings().one()
            balance_after = tenant_balance_after(
                int(account["token_balance"] or 0),
                int(tokens),
            )
            await connection.execute(
                update(tables["accounts"])
                .where(tables["accounts"].c.tenant_id == tenant)
                .values(
                    token_balance=balance_after,
                    version=tables["accounts"].c.version + 1,
                    updated_at=_now(),
                ),
            )
            await connection.execute(
                tables["ledger"].insert().values(
                    tenant_id=tenant,
                    membership_id=current_membership,
                    entry_type="recharge",
                    delta_tokens=int(tokens),
                    balance_after=balance_after,
                    source_type="recharge_order",
                    source_id=external_order_id,
                    idempotency_key=idempotency_key,
                    created_by_membership_id=current_membership,
                    model=None,
                    tokens=int(tokens),
                    cost=Decimal(str(amount)),
                    created_at=_now(),
                ),
            )
            await connection.execute(
                update(tables["recharge_orders"])
                .where(tables["recharge_orders"].c.id == order["id"])
                .values(
                    credits=int(tokens),
                    amount=Decimal(str(amount)),
                    status="completed",
                    reviewed_by_membership_id=current_membership,
                    updated_at=_now(),
                ),
            )
        return {
            "external_order_id": external_order_id,
            "tokens": int(tokens),
            "balance_after": balance_after,
            "status": "completed",
        }

    async def mark_recharge_completed(
        self,
        *,
        external_order_id: str,
        tokens: int,
        amount: str,
        tenant_id: Any | None = None,
    ) -> None:
        tenant, _ = self._context(tenant_id=tenant_id)
        tables = self._table_definitions()
        from sqlalchemy import update

        async with self._engine().begin() as connection:
            await connection.execute(
                update(tables["recharge_orders"])
                .where(
                    tables["recharge_orders"].c.tenant_id == tenant,
                    tables["recharge_orders"].c.external_order_id == external_order_id,
                )
                .values(
                    amount=Decimal(str(amount)),
                    credits=int(tokens),
                    status="completed",
                    updated_at=_now(),
                ),
            )


class TenantQuotaMiddleware(MiddlewareBase):
    """Record every successful AgentScope model call in quota_ledger."""

    def __init__(
        self,
        quota_service: TenantQuotaService,
        *,
        tenant_id: Any | None,
        membership_id: Any | None,
        source_id: str,
    ) -> None:
        super().__init__()
        self._quota = quota_service
        self._tenant_id = tenant_id
        self._membership_id = membership_id
        self._source_id = source_id

    async def _record(self, result: Any, model_name: str | None) -> None:
        input_tokens, output_tokens, cache_input, cache_creation = _usage(result)
        await self._quota.record_model_usage(
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_input_tokens=cache_input,
            cache_creation_input_tokens=cache_creation,
            tenant_id=self._tenant_id,
            membership_id=self._membership_id,
            source_id=self._source_id,
        )

    async def on_model_call(self, agent: Any, input_kwargs: dict[str, Any], next_handler: Any) -> Any:
        del agent
        await self._quota.ensure_available(tenant_id=self._tenant_id)
        model = input_kwargs.get("current_model")
        model_name = getattr(model, "model", None) or getattr(model, "model_name", None)
        result = await next_handler(**input_kwargs)
        if inspect.isasyncgen(result):
            async def observed() -> Any:
                last: Any = None
                async for item in result:
                    last = item
                    yield item
                await self._record(last, model_name)

            return observed()
        await self._record(result, model_name)
        return result


__all__ = [
    "TenantQuotaMiddleware",
    "TenantQuotaService",
    "model_cost",
    "tenant_balance_after",
]
