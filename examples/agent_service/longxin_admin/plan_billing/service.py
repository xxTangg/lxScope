"""Plan orders and quota allocation, isolated from AgentScope internals.

The service uses the existing example Redis storage through a very small
adapter boundary.  It shares the legacy admin profile/system/ledger keys so
the old member and quota screens immediately reflect approved plan changes,
while all order records live under their own ``plan-billing`` namespace.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from auth import AuthUser, JWTAuthService

from .catalog import PLAN_BY_ID, PLAN_DEFINITIONS, PlanDefinition
from .models import (
    CreatePlanOrderRequest,
    CurrentPlanView,
    OrderDecisionRequest,
    PlanCatalogResponse,
    PlanOrderListResponse,
    PlanOrderView,
    PlanView,
)


PLAN_PREFIX = "longxin:plan-billing:v1"
LEGACY_ADMIN_PREFIX = "longxin:admin:v1"
LEGACY_SYSTEM_KEY = f"{LEGACY_ADMIN_PREFIX}:system"
LEGACY_LEDGER_KEY = f"{LEGACY_ADMIN_PREFIX}:ledger"
LEGACY_AUDIT_KEY = f"{LEGACY_ADMIN_PREFIX}:audit"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


class PlanBillingService:
    """Replaceable application service for plan selection and orders."""

    def __init__(self, storage: Any, auth: JWTAuthService) -> None:
        self._storage = storage
        self._auth = auth
        self._lock = asyncio.Lock()

    def _client(self) -> Any:
        client = self._storage.get_client()
        if client is None:
            raise _error("storage_not_ready", "Billing storage is not ready.", 503)
        return client

    @staticmethod
    def _profile_key(user_id: str) -> str:
        # Compatibility adapter: the old admin member screen already reads
        # these profile keys.  No AgentScope core module depends on this key.
        return f"{LEGACY_ADMIN_PREFIX}:user:{user_id}"

    @staticmethod
    def _order_key(order_id: str) -> str:
        return f"{PLAN_PREFIX}:order:{order_id}"

    @staticmethod
    def _order_idempotency_key(actor_scope: str, key: str) -> str:
        digest = sha256(key.encode("utf-8")).hexdigest()
        return f"{PLAN_PREFIX}:idempotency:{actor_scope}:{digest}"

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    async def _read_json(self, key: str) -> dict[str, Any] | None:
        raw = await self._client().get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    async def _write_json(self, key: str, value: dict[str, Any]) -> None:
        await self._client().set(key, json.dumps(value, ensure_ascii=False))

    async def _read_idempotent(
        self,
        key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        saved = await self._read_json(key)
        if saved is None:
            return None
        # The wrapper is new, but accepting a raw legacy result keeps a
        # development deployment from losing a previously accepted request.
        if "fingerprint" not in saved or "result" not in saved:
            return saved
        if saved["fingerprint"] != self._fingerprint(payload):
            raise _error(
                "idempotency_key_reused",
                "The idempotency key was already used with another request.",
                409,
            )
        result = saved["result"]
        return result if isinstance(result, dict) else None

    async def _write_idempotent(
        self,
        key: str,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        await self._write_json(
            key,
            {
                "fingerprint": self._fingerprint(payload),
                "result": result,
            },
        )

    async def _system(self) -> dict[str, Any]:
        value = await self._read_json(LEGACY_SYSTEM_KEY)
        if value is not None:
            return value
        return {
            "system_id": "local-system",
            "pool_tokens": 0,
            "total_recharged": "0.00",
            "test_default_tokens": 0,
            "updated_at": _now(),
        }

    async def _save_system(self, value: dict[str, Any]) -> None:
        value["updated_at"] = _now()
        await self._write_json(LEGACY_SYSTEM_KEY, value)

    async def _profile(self, user: AuthUser) -> dict[str, Any]:
        value = await self._read_json(self._profile_key(user.id))
        if value is not None:
            return value
        return {
            "user_id": user.id,
            "plan_id": "plan_basic",
            "plan_name": PLAN_BY_ID["plan_basic"].name,
            "monthly_quota": PLAN_BY_ID["plan_basic"].monthly_quota,
            "monthly_used": 0,
            "bonus_tokens": 0,
            "account_type": "standard",
            "created_at": _now(),
            "updated_at": _now(),
        }

    async def _save_profile(self, profile: dict[str, Any]) -> None:
        profile["updated_at"] = _now()
        await self._write_json(self._profile_key(profile["user_id"]), profile)

    @staticmethod
    def _plan_view(plan: PlanDefinition) -> PlanView:
        return PlanView(
            id=plan.id,
            name=plan.name,
            monthly_quota=plan.monthly_quota,
            price=plan.price,
            currency=plan.currency,
            period_days=plan.period_days,
            description=plan.description,
            features=list(plan.features),
        )

    def catalog(self) -> PlanCatalogResponse:
        return PlanCatalogResponse(
            plans=[self._plan_view(item) for item in PLAN_DEFINITIONS],
        )

    async def current_plan(self, user: AuthUser) -> CurrentPlanView:
        profile = await self._profile(user)
        expires_at = profile.get("plan_expires_at")
        started_at = profile.get("plan_started_at")
        plan_status = "inactive"
        if started_at:
            if expires_at and expires_at <= _now():
                plan_status = "expired"
            else:
                plan_status = "active"
        monthly_quota = int(profile.get("monthly_quota", 0))
        monthly_used = max(0, int(profile.get("monthly_used", 0)))
        bonus_tokens = max(0, int(profile.get("bonus_tokens", 0)))
        return CurrentPlanView(
            user_id=user.id,
            username=user.username,
            plan_id=str(profile.get("plan_id", "plan_basic")),
            plan_name=str(profile.get("plan_name", "Basic")),
            monthly_quota=monthly_quota,
            monthly_used=monthly_used,
            bonus_tokens=bonus_tokens,
            remaining_tokens=max(monthly_quota - monthly_used, 0) + bonus_tokens,
            status=plan_status,
            started_at=started_at,
            expires_at=expires_at,
            latest_order_id=profile.get("latest_plan_order_id"),
        )

    async def _orders(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        async for key in self._client().scan_iter(
            match=f"{PLAN_PREFIX}:order:*",
            count=100,
        ):
            value = await self._read_json(key)
            if value is not None:
                result.append(value)
        return sorted(
            result,
            key=lambda item: str(item.get("requested_at", "")),
            reverse=True,
        )

    @staticmethod
    def _order_view(order: dict[str, Any], request_id: str = "") -> PlanOrderView:
        return PlanOrderView(
            order_id=order["order_id"],
            user_id=order["user_id"],
            username=order["username"],
            plan_id=order["plan_id"],
            plan_name=order["plan_name"],
            monthly_quota=int(order["monthly_quota"]),
            price=order["price"],
            currency=order["currency"],
            period_days=int(order["period_days"]),
            order_type=order["order_type"],
            status=order["status"],
            note=order.get("note"),
            decision_reason=order.get("decision_reason"),
            allocated_tokens=int(order.get("allocated_tokens", 0)),
            requested_at=order["requested_at"],
            decided_at=order.get("decided_at"),
            request_id=request_id,
        )

    async def list_orders(
        self,
        *,
        user_id: str | None = None,
        order_status: str | None = None,
        limit: int = 50,
        request_id: str = "",
    ) -> PlanOrderListResponse:
        orders = await self._orders()
        if user_id is not None:
            orders = [item for item in orders if item.get("user_id") == user_id]
        if order_status is not None:
            orders = [item for item in orders if item.get("status") == order_status]
        orders = orders[:limit]
        return PlanOrderListResponse(
            orders=[self._order_view(item, request_id) for item in orders],
            total=len(orders),
            request_id=request_id,
        )

    async def _has_pending_order(self, user_id: str) -> bool:
        return any(
            item.get("user_id") == user_id and item.get("status") == "pending"
            for item in await self._orders()
        )

    async def create_order(
        self,
        user: AuthUser,
        body: CreatePlanOrderRequest,
        *,
        idempotency_key: str,
        request_id: str = "",
    ) -> PlanOrderView:
        plan = PLAN_BY_ID.get(body.plan_id)
        if plan is None:
            raise _error("plan_not_found", "The selected plan does not exist.", 409)
        if user.role == "admin":
            raise _error("admin_cannot_order", "Administrators cannot buy member plans.", 409)
        idem_key = self._order_idempotency_key(f"user:{user.id}", idempotency_key)
        request_fingerprint = {"operation": "create_order", **body.model_dump()}
        async with self._lock:
            previous = await self._read_idempotent(idem_key, request_fingerprint)
            if previous is not None:
                return self._order_view(previous, request_id)
            if await self._has_pending_order(user.id):
                raise _error(
                    "pending_order_exists",
                    "You already have a pending plan order.",
                    409,
                )
            profile = await self._profile(user)
            activated = bool(profile.get("plan_started_at"))
            current_plan_id = str(profile.get("plan_id", "plan_basic"))
            if not activated:
                order_type = "activation"
            elif body.plan_id == current_plan_id:
                order_type = "renewal"
            elif plan.monthly_quota > int(profile.get("monthly_quota", 0)):
                order_type = "upgrade"
            else:
                order_type = "downgrade"
            order = {
                "order_id": f"plan-order-{uuid4().hex}",
                "user_id": user.id,
                "username": user.username,
                "plan_id": plan.id,
                "plan_name": plan.name,
                "monthly_quota": plan.monthly_quota,
                "price": plan.price,
                "currency": plan.currency,
                "period_days": plan.period_days,
                "order_type": order_type,
                "status": "pending",
                "note": body.note,
                "allocated_tokens": 0,
                "requested_at": _now(),
            }
            await self._write_json(self._order_key(order["order_id"]), order)
            await self._client().rpush(
                f"{PLAN_PREFIX}:orders",
                order["order_id"],
            )
            await self._write_idempotent(idem_key, request_fingerprint, order)
            await self._audit(
                action="plan.order.created",
                actor=user,
                target_id=user.id,
                reason=body.note or "Plan order submitted.",
                resource_id=order["order_id"],
            )
            return self._order_view(order, request_id)

    async def _get_order(self, order_id: str) -> dict[str, Any]:
        order = await self._read_json(self._order_key(order_id))
        if order is None:
            raise _error("order_not_found", "Plan order not found.", 404)
        return order

    async def _append_ledger(
        self,
        *,
        entry_type: str,
        delta_tokens: int,
        balance_after: int,
        order_id: str,
        related_user_id: str,
        operator_id: str,
    ) -> None:
        entry = {
            "ledger_id": f"ledger-{uuid4().hex}",
            "type": entry_type,
            "delta_tokens": -delta_tokens,
            "balance_after": balance_after,
            "amount": None,
            "order_id": order_id,
            "related_user_id": related_user_id,
            "operator_id": operator_id,
            "source": "plan_billing",
            "created_at": _now(),
        }
        await self._client().rpush(
            LEGACY_LEDGER_KEY,
            json.dumps(entry, ensure_ascii=False),
        )

    async def _audit(
        self,
        *,
        action: str,
        actor: AuthUser,
        target_id: str | None,
        reason: str,
        resource_id: str | None = None,
        result_summary: str = "completed",
    ) -> None:
        event = {
            "event_id": f"event-{uuid4().hex}",
            "actor_type": actor.role,
            "actor_id": actor.id,
            "actor_name": actor.username,
            "target_user_id": target_id,
            "action": action,
            "resource_type": "plan_order",
            "resource_id": resource_id,
            "reason": reason,
            "status": "completed",
            "result_summary": result_summary,
            "created_at": _now(),
        }
        await self._client().rpush(
            LEGACY_AUDIT_KEY,
            json.dumps(event, ensure_ascii=False),
        )

    @staticmethod
    def _allocation_tokens(
        order: dict[str, Any],
        profile: dict[str, Any],
        target: PlanDefinition,
    ) -> int:
        if order["order_type"] in {"activation", "renewal"}:
            return target.monthly_quota
        return target.monthly_quota - int(profile.get("monthly_quota", 0))

    async def approve_order(
        self,
        order_id: str,
        actor: AuthUser,
        body: OrderDecisionRequest,
        *,
        idempotency_key: str,
        request_id: str = "",
    ) -> PlanOrderView:
        if not await self._auth.verify_password(actor.id, body.admin_password):
            raise _error("admin_password_invalid", "The administrator password is invalid.", 403)
        idem_key = self._order_idempotency_key(f"admin:{actor.id}", idempotency_key)
        request_fingerprint = {
            "operation": "approve_order",
            "order_id": order_id,
            "reason": body.reason,
        }
        async with self._lock:
            previous = await self._read_idempotent(idem_key, request_fingerprint)
            if previous is not None:
                return self._order_view(previous, request_id)
            order = await self._get_order(order_id)
            if order["status"] != "pending":
                raise _error("order_not_pending", "Only pending orders can be approved.", 409)
            target = PLAN_BY_ID[order["plan_id"]]
            account = next(
                (item for item in await self._auth.list_accounts() if item.id == order["user_id"]),
                None,
            )
            if account is None or account.status in {"banned", "deleted"}:
                raise _error("user_not_available", "The order user is no longer available.", 409)
            profile = await self._profile(account)
            allocation = self._allocation_tokens(order, profile, target)
            if allocation < 0 and int(profile.get("monthly_used", 0)) > target.monthly_quota:
                raise _error(
                    "plan_downgrade_blocked",
                    "The used quota is above the selected plan quota.",
                    409,
                )
            system = await self._system()
            pool_tokens = int(system.get("pool_tokens", 0))
            if allocation > pool_tokens:
                raise _error(
                    "quota_insufficient",
                    "The system token pool is insufficient for this plan.",
                    409,
                )
            system["pool_tokens"] = pool_tokens - allocation
            await self._save_system(system)
            now = datetime.now(timezone.utc)
            profile.update(
                {
                    "plan_id": target.id,
                    "plan_name": target.name,
                    "monthly_quota": target.monthly_quota,
                    "plan_started_at": now.isoformat(),
                    "plan_expires_at": (now + timedelta(days=target.period_days)).isoformat(),
                    "latest_plan_order_id": order_id,
                },
            )
            if order["order_type"] in {"activation", "renewal"}:
                profile["monthly_used"] = 0
            await self._save_profile(profile)
            await self._append_ledger(
                entry_type="plan_allocation" if allocation >= 0 else "plan_refund",
                delta_tokens=allocation,
                balance_after=int(system["pool_tokens"]),
                order_id=order_id,
                related_user_id=order["user_id"],
                operator_id=actor.id,
            )
            order.update(
                {
                    "status": "approved",
                    "allocated_tokens": allocation,
                    "decision_reason": body.reason,
                    "decided_at": _now(),
                },
            )
            await self._write_json(self._order_key(order_id), order)
            await self._write_idempotent(idem_key, request_fingerprint, order)
            await self._audit(
                action="plan.order.approved",
                actor=actor,
                target_id=order["user_id"],
                reason=body.reason,
                resource_id=order_id,
                result_summary=f"allocated_tokens={allocation}",
            )
            return self._order_view(order, request_id)

    async def reject_order(
        self,
        order_id: str,
        actor: AuthUser,
        body: OrderDecisionRequest,
        *,
        idempotency_key: str,
        request_id: str = "",
    ) -> PlanOrderView:
        if not await self._auth.verify_password(actor.id, body.admin_password):
            raise _error("admin_password_invalid", "The administrator password is invalid.", 403)
        idem_key = self._order_idempotency_key(f"admin:{actor.id}", idempotency_key)
        request_fingerprint = {
            "operation": "reject_order",
            "order_id": order_id,
            "reason": body.reason,
        }
        async with self._lock:
            previous = await self._read_idempotent(idem_key, request_fingerprint)
            if previous is not None:
                return self._order_view(previous, request_id)
            order = await self._get_order(order_id)
            if order["status"] != "pending":
                raise _error("order_not_pending", "Only pending orders can be rejected.", 409)
            order.update(
                {
                    "status": "rejected",
                    "decision_reason": body.reason,
                    "decided_at": _now(),
                },
            )
            await self._write_json(self._order_key(order_id), order)
            await self._write_idempotent(idem_key, request_fingerprint, order)
            await self._audit(
                action="plan.order.rejected",
                actor=actor,
                target_id=order["user_id"],
                reason=body.reason,
                resource_id=order_id,
            )
            return self._order_view(order, request_id)

    async def admin_assign_plan(
        self,
        user_id: str,
        plan_id: str,
        operator_id: str,
    ) -> dict[str, Any]:
        """Compatibility path for direct member provisioning by an admin.

        Normal user changes must use an order.  This path exists only for the
        already shipped member-management PATCH endpoint and still applies
        the same quota/ledger rules.
        """
        target = PLAN_BY_ID.get(plan_id)
        if target is None:
            raise _error("plan_not_found", "The selected plan does not exist.", 409)
        accounts = await self._auth.list_accounts()
        account = next((item for item in accounts if item.id == user_id), None)
        if account is None:
            raise _error("user_not_found", "User not found.", 404)
        async with self._lock:
            profile = await self._profile(account)
            old_quota = int(profile.get("monthly_quota", 0))
            allocation = target.monthly_quota - old_quota
            if not profile.get("plan_started_at"):
                allocation = target.monthly_quota
            if allocation < 0 and int(profile.get("monthly_used", 0)) > target.monthly_quota:
                raise _error(
                    "plan_downgrade_blocked",
                    "The used quota is above the selected plan quota.",
                    409,
                )
            system = await self._system()
            if allocation > int(system.get("pool_tokens", 0)):
                raise _error(
                    "quota_insufficient",
                    "The system token pool is insufficient for this plan.",
                    409,
                )
            system["pool_tokens"] = int(system.get("pool_tokens", 0)) - allocation
            await self._save_system(system)
            profile.update(
                {
                    "plan_id": target.id,
                    "plan_name": target.name,
                    "monthly_quota": target.monthly_quota,
                    "plan_started_at": profile.get("plan_started_at") or _now(),
                    "plan_expires_at": profile.get("plan_expires_at")
                    or (datetime.now(timezone.utc) + timedelta(days=target.period_days)).isoformat(),
                },
            )
            await self._save_profile(profile)
            await self._append_ledger(
                entry_type="admin_plan_allocation" if allocation >= 0 else "plan_refund",
                delta_tokens=allocation,
                balance_after=int(system["pool_tokens"]),
                order_id="admin-assignment",
                related_user_id=user_id,
                operator_id=operator_id,
            )
            return profile
