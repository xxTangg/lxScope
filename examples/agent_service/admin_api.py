# -*- coding: utf-8 -*-
"""Product-level administrator APIs for the Longxin AgentScope service."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from auth import AuthUser, JWTAuthService
from longxin_admin.plan_billing.catalog import PLAN_VALUES
from sales_hub_client import SalesHubClient, SalesHubClientError


_PREFIX = "longxin:admin:v1"
_SALES_HUB_KEY = "longxin:sales-hub:v1:config"
_LEDGER_KEY = f"{_PREFIX}:ledger"
_AUDIT_KEY = f"{_PREFIX}:audit"
# The plan-billing extension owns the catalog.  This projection keeps the
# existing member-management API backward compatible without duplicating the
# plan definitions in the legacy admin module.
_PLANS = PLAN_VALUES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(
    code: str,
    message: str,
    status_code: int,
    *,
    fields: dict[str, str] | None = None,
) -> HTTPException:
    detail: dict[str, Any] = {"code": code, "message": message}
    if fields:
        detail["fields"] = fields
    return HTTPException(status_code=status_code, detail=detail)


class AdminUserView(BaseModel):
    id: str
    username: str
    role: Literal["user", "admin"]
    status: Literal["active", "locked", "banned", "deleted"]
    plan_id: str
    plan_name: str
    monthly_quota: int
    monthly_used: int = 0
    bonus_tokens: int = 0
    account_type: Literal["standard", "test"] = "standard"
    created_at: str
    updated_at: str


class UserListResponse(BaseModel):
    users: list[AdminUserView]
    total: int
    page: int
    page_size: int
    request_id: str


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    initial_password: str = Field(min_length=8, max_length=1024)
    plan_id: str = Field(default="plan_basic", min_length=1, max_length=64)
    bonus_tokens: int = Field(default=0, ge=0, le=100_000_000)


class UpdateUserRequest(BaseModel):
    status: Literal["active", "locked", "banned"] | None = None
    plan_id: str | None = Field(default=None, min_length=1, max_length=64)
    bonus_tokens: int | None = Field(default=None, ge=0, le=100_000_000)


class UserActionRequest(BaseModel):
    reason: str = Field(min_length=4, max_length=300)


class ResetPasswordRequest(UserActionRequest):
    admin_password: str = Field(min_length=1, max_length=1024)


class ResetPasswordResponse(BaseModel):
    operation_id: str
    request_id: str
    state: Literal["completed"]
    user_id: str
    username: str
    temporary_password: str
    expires_at: str


class SystemAccountView(BaseModel):
    system_id: str
    pool_tokens: int
    total_recharged: str
    test_default_tokens: int
    account_count: int
    admin_count: int
    updated_at: str
    request_id: str


class QuotaUpdateRequest(BaseModel):
    test_default_tokens: int = Field(ge=0, le=100_000_000)


class LedgerEntryView(BaseModel):
    ledger_id: str
    type: str
    delta_tokens: int
    balance_after: int
    amount: str | None = None
    order_id: str | None = None
    related_user_id: str | None = None
    operator_id: str | None = None
    source: str
    created_at: str


class LedgerListResponse(BaseModel):
    entries: list[LedgerEntryView]
    total: int
    request_id: str


class RedeemCodeRequest(BaseModel):
    code: str = Field(min_length=20, max_length=16_384)
    confirm: bool = False


class OperationResponse(BaseModel):
    operation_id: str
    request_id: str
    state: Literal["pending", "completed", "failed", "unknown", "rolled_back"]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class RechargeRequest(BaseModel):
    amount: str = Field(pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$")
    note: str | None = Field(default=None, max_length=300)


class RechargeRequestView(BaseModel):
    order_id: str
    system_id: str
    amount: str
    status: Literal["pending", "approved", "rejected", "unknown"]
    delivery_status: Literal["not_delivered", "delivered"] = "not_delivered"
    created_at: str


class RechargeRequestListResponse(BaseModel):
    orders: list[RechargeRequestView]
    total: int
    request_id: str


class RechargePollItem(BaseModel):
    order_id: str
    system_id: str
    amount: str
    tokens: int = Field(gt=0)
    status: Literal["approved"] = "approved"
    delivery_status: Literal["not_delivered"] = "not_delivered"
    recharge_code: str = Field(min_length=20, max_length=16_384)
    expires_at: str | None = None


class RechargePollResponse(BaseModel):
    orders: list[RechargePollItem]
    request_id: str


class RechargeAckRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    system_id: str = Field(min_length=1, max_length=128)
    redemption_operation_id: str = Field(min_length=1, max_length=128)
    ledger_id: str = Field(min_length=1, max_length=128)


class UsageReportRequest(BaseModel):
    system_id: str = Field(min_length=1, max_length=128)
    pool_tokens: int = Field(ge=0)
    total_recharged: str = Field(pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$")
    app_version: str = Field(min_length=1, max_length=128)
    cumulative_consumed: int = Field(ge=0)
    cumulative_credits: int = Field(ge=0)
    client_reported_at: str


class UsageReportResponse(BaseModel):
    report_id: str
    system_id: str
    accepted: bool
    reported_at: str
    cumulative_consumed_delta: int | None = None
    cumulative_credits_delta: int | None = None
    request_id: str


class SalesHubConfigView(BaseModel):
    system_id: str
    hub_url: str
    token_masked: str | None
    public_key_fingerprint: str | None
    outbound_status: Literal["unknown", "ok", "failed"] = "unknown"
    inbound_status: Literal["unknown", "ok", "failed"] = "unknown"
    last_verified_at: str | None = None
    request_id: str


class SalesHubConfigUpdate(BaseModel):
    system_id: str | None = Field(default=None, min_length=1, max_length=128)
    hub_url: str | None = Field(default=None, max_length=2048)
    token: str | None = Field(default=None, max_length=4096)
    public_key: str | None = Field(default=None, max_length=16_384)


class HubPingResponse(BaseModel):
    ok: bool
    system_id: str
    system_name: str
    app_version: str
    core_version: str
    checked_at: str
    request_id: str


class RemotePasswordResetRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=8, max_length=1024)
    expires_at: str | None = None


class AdminService:
    """Persist product management data beside the existing AgentScope store."""

    def __init__(
        self,
        storage: Any,
        auth: JWTAuthService,
        plan_billing: Any | None = None,
    ) -> None:
        self._storage = storage
        self._auth = auth
        self._plan_billing = plan_billing
        self._lock = asyncio.Lock()
        self._sales_hub_client = SalesHubClient(self._hub_connection_config)

    def _client(self) -> Any:
        client = self._storage.get_client()
        if client is None:
            raise _error("storage_not_ready", "Admin storage is not ready.", 503)
        return client

    async def _hub_connection_config(self) -> dict[str, Any]:
        return await self._read_json(_SALES_HUB_KEY) or {}

    @staticmethod
    def _user_key(user_id: str) -> str:
        return f"{_PREFIX}:user:{user_id}"

    @staticmethod
    def _order_key(order_id: str) -> str:
        return f"{_PREFIX}:order:{order_id}"

    async def _read_json(self, key: str) -> dict[str, Any] | None:
        raw = await self._client().get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        value = json.loads(raw)
        return value if isinstance(value, dict) else None

    async def _write_json(self, key: str, value: dict[str, Any]) -> None:
        await self._client().set(key, json.dumps(value, ensure_ascii=False))

    async def _system(self) -> dict[str, Any]:
        value = await self._read_json(f"{_PREFIX}:system")
        if value is not None:
            return value
        return {
            "system_id": os.getenv("LONGXIN_SYSTEM_ID", "local-system"),
            "pool_tokens": 0,
            "total_recharged": "0.00",
            "cumulative_consumed": 0,
            "cumulative_credits": 0,
            "test_default_tokens": 0,
            "updated_at": _now(),
        }

    async def _save_system(self, value: dict[str, Any]) -> None:
        value["updated_at"] = _now()
        await self._write_json(f"{_PREFIX}:system", value)

    async def _profile(self, account: AuthUser) -> dict[str, Any]:
        value = await self._read_json(self._user_key(account.id))
        if value is not None:
            return value
        return {
            "user_id": account.id,
            "plan_id": "plan_basic",
            "plan_name": _PLANS["plan_basic"][0],
            "monthly_quota": _PLANS["plan_basic"][1],
            "monthly_used": 0,
            "bonus_tokens": 0,
            "account_type": "standard",
            "created_at": _now(),
            "updated_at": _now(),
        }

    async def _save_profile(self, profile: dict[str, Any]) -> None:
        profile["updated_at"] = _now()
        await self._write_json(self._user_key(profile["user_id"]), profile)

    async def _view(self, account: AuthUser) -> AdminUserView:
        profile = await self._profile(account)
        if self._plan_billing is not None and account.role == "user":
            # Keep the administrator's member table aligned with the same
            # usage projection returned by /account/plan.
            current = await self._plan_billing.current_plan(account)
            profile.update(
                {
                    "plan_id": current.plan_id,
                    "plan_name": current.plan_name,
                    "monthly_quota": current.monthly_quota,
                    "monthly_used": current.monthly_used,
                    "bonus_tokens": current.bonus_tokens,
                },
            )
        return AdminUserView(
            id=account.id,
            username=account.username,
            role=account.role,
            status=account.status,
            plan_id=profile["plan_id"],
            plan_name=profile["plan_name"],
            monthly_quota=int(profile["monthly_quota"]),
            monthly_used=int(profile.get("monthly_used", 0)),
            bonus_tokens=int(profile.get("bonus_tokens", 0)),
            account_type=profile.get("account_type", "standard"),
            created_at=profile["created_at"],
            updated_at=profile["updated_at"],
        )

    async def list_users(
        self,
        *,
        keyword: str | None,
        account_status: str | None,
        page: int,
        page_size: int,
    ) -> UserListResponse:
        accounts = await self._auth.list_accounts()
        if keyword:
            needle = keyword.casefold()
            accounts = [
                item
                for item in accounts
                if needle in item.username.casefold() or needle in item.id.casefold()
            ]
        if account_status:
            accounts = [item for item in accounts if item.status == account_status]
        total = len(accounts)
        start = (page - 1) * page_size
        views = [await self._view(item) for item in accounts[start : start + page_size]]
        return UserListResponse(
            users=views,
            total=total,
            page=page,
            page_size=page_size,
            request_id="",
        )

    async def create_user(self, body: CreateUserRequest) -> AdminUserView:
        if body.plan_id not in _PLANS:
            raise _error("plan_not_found", "The selected plan does not exist.", 409)
        async with self._lock:
            system = await self._system()
            explicit_plan = self._plan_billing is not None and "plan_id" in body.model_fields_set
            required_tokens = (
                _PLANS[body.plan_id][1] + body.bonus_tokens
                if explicit_plan
                else body.bonus_tokens
            )
            if required_tokens > int(system["pool_tokens"]):
                raise _error(
                    "quota_insufficient",
                    "The system token pool is insufficient.",
                    409,
                )
            account = await self._auth.create_user(
                body.username.lower(),
                body.initial_password,
            )
            # ``plan_id`` has a compatibility default.  Only an explicitly
            # selected plan is provisioned immediately; old callers that
            # omit it keep the original inactive-basic behavior.
            if explicit_plan:
                await self._plan_billing.admin_assign_plan(
                    account.id,
                    body.plan_id,
                    "admin",
                )
                profile = await self._profile(account)
                profile["bonus_tokens"] = body.bonus_tokens
                profile["bonus_tokens_granted"] = body.bonus_tokens
                await self._save_profile(profile)
                system = await self._system()
            else:
                plan_name, monthly_quota = _PLANS[body.plan_id]
                await self._save_profile(
                    {
                        "user_id": account.id,
                        "plan_id": body.plan_id,
                        "plan_name": plan_name,
                        "monthly_quota": monthly_quota,
                        "monthly_used": 0,
                        "bonus_tokens": body.bonus_tokens,
                        "account_type": "standard",
                        "created_at": _now(),
                        "updated_at": _now(),
                    },
                )
            if body.bonus_tokens:
                system["pool_tokens"] -= body.bonus_tokens
                await self._save_system(system)
                await self._append_ledger(
                    entry_type="member_allocation",
                    delta=-body.bonus_tokens,
                    balance_after=int(system["pool_tokens"]),
                    related_user_id=account.id,
                    source="admin",
                )
            return await self._view(account)

    async def update_user(
        self,
        user_id: str,
        body: UpdateUserRequest,
        actor_id: str,
    ) -> AdminUserView:
        account = await self._auth._account_by_id(user_id)
        if account is None:
            raise _error("user_not_found", "User not found.", 404)
        if user_id == actor_id and body.status in {"banned", "locked"}:
            raise _error("last_admin_protected", "The current admin cannot be disabled.", 409)
        if account.role == "admin" and body.status in {"banned", "locked"}:
            active_admins = [
                item
                for item in await self._auth.list_accounts()
                if item.role == "admin" and item.status == "active"
            ]
            if len(active_admins) <= 1:
                raise _error("last_admin_protected", "At least one admin is required.", 409)
        async with self._lock:
            account_view = self._auth._public_user(account)
            profile = await self._profile(account_view)
            system = await self._system()
            if body.plan_id is not None:
                if self._plan_billing is not None:
                    await self._plan_billing.admin_assign_plan(
                        user_id,
                        body.plan_id,
                        actor_id,
                    )
                    profile = await self._profile(account_view)
                    system = await self._system()
                else:
                    if body.plan_id not in _PLANS:
                        raise _error("plan_not_found", "The selected plan does not exist.", 409)
                    profile["plan_id"] = body.plan_id
                    profile["plan_name"], profile["monthly_quota"] = _PLANS[body.plan_id]
            if body.bonus_tokens is not None:
                old_bonus = int(profile.get("bonus_tokens", 0))
                delta = body.bonus_tokens - old_bonus
                if delta > int(system["pool_tokens"]):
                    raise _error(
                        "quota_insufficient",
                        "The system token pool is insufficient.",
                        409,
                    )
                profile["bonus_tokens"] = body.bonus_tokens
                profile["bonus_tokens_granted"] = body.bonus_tokens
                if delta:
                    system["pool_tokens"] -= delta
                    await self._save_system(system)
                    await self._append_ledger(
                        entry_type="member_allocation" if delta > 0 else "allocation_refund",
                        delta=-delta,
                        balance_after=int(system["pool_tokens"]),
                        related_user_id=user_id,
                        source="admin",
                    )
            await self._save_profile(profile)
            if body.status is not None:
                account = await self._auth.set_status(user_id, body.status)
                account_view = account
            return await self._view(account_view)

    async def delete_user(self, user_id: str, actor_id: str) -> None:
        account = await self._auth._account_by_id(user_id)
        if account is None:
            raise _error("user_not_found", "User not found.", 404)
        if user_id == actor_id or account.role == "admin":
            raise _error("admin_protected", "An administrator cannot be deleted.", 409)
        await self._auth.set_status(user_id, "deleted")
        await self._auth.revoke_sessions(user_id)

    async def reset_password(
        self,
        user_id: str,
        actor: AuthUser,
        body: ResetPasswordRequest,
    ) -> ResetPasswordResponse:
        if not await self._auth.verify_password(actor.id, body.admin_password):
            raise _error("admin_password_invalid", "The current admin password is invalid.", 403)
        account = await self._auth._account_by_id(user_id)
        if account is None or account.status == "deleted":
            raise _error("user_not_found", "User not found.", 404)
        temporary_password = secrets.token_urlsafe(12)
        updated = await self._auth.reset_password(user_id, temporary_password)
        await self._audit(
            actor=actor,
            action="user.reset_password",
            target_id=user_id,
            reason=body.reason,
        )
        return ResetPasswordResponse(
            operation_id=f"op-{uuid4().hex}",
            request_id="",
            state="completed",
            user_id=updated.id,
            username=updated.username,
            temporary_password=temporary_password,
            expires_at=datetime.now(timezone.utc).isoformat(),
        )

    async def overview(self) -> dict[str, Any]:
        accounts = await self._auth.list_accounts()
        system = await self._system()
        return {
            "system_id": system["system_id"],
            "pool_tokens": int(system["pool_tokens"]),
            "total_recharged": str(system["total_recharged"]),
            "account_count": len(accounts),
            "admin_count": sum(item.role == "admin" for item in accounts),
            "active_account_count": sum(item.status == "active" for item in accounts),
            "app_version": os.getenv("LONGXIN_APP_VERSION", "3.8.1"),
            "core_version": os.getenv("LONGXIN_CORE_VERSION", "unknown"),
            "health": "ok",
            "updated_at": system["updated_at"],
        }

    async def quota(self) -> dict[str, Any]:
        system = await self._system()
        accounts = await self._auth.list_accounts()
        return {
            "system_id": system["system_id"],
            "pool_tokens": int(system["pool_tokens"]),
            "total_recharged": str(system["total_recharged"]),
            "test_default_tokens": int(system["test_default_tokens"]),
            "account_count": len(accounts),
            "admin_count": sum(item.role == "admin" for item in accounts),
            "updated_at": system["updated_at"],
        }

    async def update_quota(self, body: QuotaUpdateRequest) -> dict[str, Any]:
        async with self._lock:
            system = await self._system()
            system["test_default_tokens"] = body.test_default_tokens
            await self._save_system(system)
        return await self.quota()

    async def _append_ledger(
        self,
        *,
        entry_type: str,
        delta: int,
        balance_after: int,
        amount: str | None = None,
        order_id: str | None = None,
        related_user_id: str | None = None,
        operator_id: str | None = None,
        source: str,
    ) -> LedgerEntryView:
        entry = LedgerEntryView(
            ledger_id=f"led-{uuid4().hex}",
            type=entry_type,
            delta_tokens=delta,
            balance_after=balance_after,
            amount=amount,
            order_id=order_id,
            related_user_id=related_user_id,
            operator_id=operator_id,
            source=source,
            created_at=_now(),
        )
        await self._client().rpush(_LEDGER_KEY, entry.model_dump_json())
        return entry

    async def ledger(self, limit: int) -> list[LedgerEntryView]:
        raw_entries = await self._client().lrange(_LEDGER_KEY, -limit, -1)
        result: list[LedgerEntryView] = []
        for raw in reversed(raw_entries):
            try:
                result.append(LedgerEntryView.model_validate_json(raw))
            except ValueError:
                continue
        return result

    async def _hub_request(
        self,
        method: str,
        path: str,
        *,
        request_id: str,
        idempotency_key: str | None = None,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._sales_hub_client.request(
                method,
                path,
                request_id=request_id,
                idempotency_key=idempotency_key,
                params=params,
                json_body=json_body,
            )
        except SalesHubClientError as exc:
            raise _error(exc.code, exc.message, exc.status_code) from exc

    async def create_recharge_request(
        self,
        body: RechargeRequest,
        *,
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> RechargeRequestView:
        if idempotency_key:
            existing = await self._read_json(f"{_PREFIX}:idempotency:{idempotency_key}")
            if existing:
                return RechargeRequestView.model_validate(existing)
        system = await self._system()
        remote = await self._hub_request(
            "POST",
            "/api/v1/integration/recharge-requests",
            request_id=request_id or f"req-{uuid4().hex}",
            idempotency_key=idempotency_key,
            json_body={
                "system_id": system["system_id"],
                "amount": body.amount,
                "note": body.note,
                "requested_at": _now(),
            },
        )
        order_id = remote.get("order_id")
        if not isinstance(order_id, str) or not order_id:
            raise _error("hub_invalid_response", "Sales Hub did not return an order ID.", 502)
        remote_system_id = remote.get("system_id", system["system_id"])
        if remote_system_id != system["system_id"]:
            raise _error("system_id_mismatch", "Sales Hub returned another system ID.", 409)
        remote_status = remote.get("status", "pending")
        if remote_status not in {"pending", "approved", "rejected", "unknown"}:
            remote_status = "unknown"
        delivery_status = remote.get("delivery_status", "not_delivered")
        if delivery_status not in {"not_delivered", "delivered"}:
            delivery_status = "not_delivered"
        order = RechargeRequestView(
            order_id=order_id,
            system_id=system["system_id"],
            amount=body.amount,
            status=remote_status,
            delivery_status=delivery_status,
            created_at=_now(),
        )
        await self._write_json(self._order_key(order.order_id), order.model_dump())
        await self._client().rpush(f"{_PREFIX}:orders", order.order_id)
        if idempotency_key:
            await self._write_json(
                f"{_PREFIX}:idempotency:{idempotency_key}",
                order.model_dump(),
            )
        return order

    async def recharge_requests(self, limit: int) -> list[RechargeRequestView]:
        ids = await self._client().lrange(f"{_PREFIX}:orders", -limit, -1)
        result: list[RechargeRequestView] = []
        for order_id in reversed(ids):
            value = await self._read_json(self._order_key(order_id))
            if value:
                result.append(RechargeRequestView.model_validate(value))
        return result

    async def sync_recharge(
        self,
        actor: AuthUser,
        *,
        request_id: str,
        idempotency_key: str | None = None,
    ) -> OperationResponse:
        operation_id = f"op-{uuid4().hex}"
        try:
            remote = await self._hub_request(
                "GET",
                "/api/v1/integration/recharge-requests/poll",
                request_id=request_id,
                idempotency_key=idempotency_key or operation_id,
                params={"system_id": (await self._system())["system_id"]},
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {
                "code": "hub_request_failed",
                "message": str(exc.detail),
            }
            return OperationResponse(
                operation_id=operation_id,
                request_id=request_id,
                state="unknown",
                error=detail,
            )
        raw_orders = remote.get("orders")
        if not isinstance(raw_orders, list):
            return OperationResponse(
                operation_id=operation_id,
                request_id=request_id,
                state="unknown",
                error={
                    "code": "hub_invalid_response",
                    "message": "Sales Hub did not return an order list.",
                },
            )
        system_id = (await self._system())["system_id"]
        completed: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for raw_order in raw_orders:
            try:
                item = RechargePollItem.model_validate(raw_order)
            except ValueError:
                failures.append({"code": "invalid_order", "message": "An order was ignored."})
                continue
            if item.system_id != system_id:
                failures.append(
                    {
                        "order_id": item.order_id,
                        "code": "system_id_mismatch",
                        "message": "An order targets another system.",
                    },
                )
                continue
            local = await self._read_json(self._order_key(item.order_id))
            if local is not None and local.get("delivery_status") == "delivered":
                continue
            if local is None:
                local = RechargeRequestView(
                    order_id=item.order_id,
                    system_id=item.system_id,
                    amount=item.amount,
                    status="approved",
                    delivery_status="not_delivered",
                    created_at=_now(),
                ).model_dump()
                await self._write_json(self._order_key(item.order_id), local)
                await self._client().rpush(f"{_PREFIX}:orders", item.order_id)
            ledger_id = local.get("ledger_id")
            redemption_operation_id = local.get("redemption_operation_id")
            if not isinstance(ledger_id, str) or not isinstance(redemption_operation_id, str):
                try:
                    redeemed = await self.redeem_code(
                        RedeemCodeRequest(code=item.recharge_code, confirm=True),
                        actor,
                        request_id=request_id,
                    )
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, dict) else {
                        "code": "redeem_failed",
                        "message": str(exc.detail),
                    }
                    failures.append({"order_id": item.order_id, **detail})
                    continue
                ledger_id = redeemed["ledger_id"]
                redemption_operation_id = redeemed["operation_id"]
                local.update(
                    {
                        "status": "approved",
                        "delivery_status": "not_delivered",
                        "ledger_id": ledger_id,
                        "redemption_operation_id": redemption_operation_id,
                    },
                )
                await self._write_json(self._order_key(item.order_id), local)
            try:
                await self._hub_request(
                    "POST",
                    f"/api/v1/integration/recharge-requests/{item.order_id}/ack",
                    request_id=request_id,
                    idempotency_key=f"{idempotency_key or operation_id}:{item.order_id}",
                    json_body={
                        "operation_id": operation_id,
                        "system_id": system_id,
                        "redemption_operation_id": redemption_operation_id,
                        "ledger_id": ledger_id,
                    },
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {
                    "code": "ack_failed",
                    "message": str(exc.detail),
                }
                failures.append({"order_id": item.order_id, **detail})
                continue
            local["status"] = "approved"
            local["delivery_status"] = "delivered"
            await self._write_json(self._order_key(item.order_id), local)
            completed.append(
                {
                    "order_id": item.order_id,
                    "ledger_id": ledger_id,
                    "delivery_status": "delivered",
                },
            )
        state: Literal["completed", "unknown"] = "unknown" if failures else "completed"
        return OperationResponse(
            operation_id=operation_id,
            request_id=request_id,
            state=state,
            result={"orders": completed, "failed_orders": failures},
            error={"code": "recharge_sync_partial", "message": "Some orders need retry."}
            if failures
            else None,
        )

    async def report_usage(
        self,
        *,
        request_id: str,
        idempotency_key: str | None = None,
    ) -> OperationResponse:
        operation_id = f"op-{uuid4().hex}"
        system = await self._system()
        report = UsageReportRequest(
            system_id=system["system_id"],
            pool_tokens=int(system["pool_tokens"]),
            total_recharged=str(system["total_recharged"]),
            app_version=os.getenv("LONGXIN_APP_VERSION", "3.8.1"),
            cumulative_consumed=int(system.get("cumulative_consumed", 0)),
            cumulative_credits=int(system.get("cumulative_credits", 0)),
            client_reported_at=_now(),
        )
        try:
            remote = await self._hub_request(
                "POST",
                "/api/v1/integration/usage-reports",
                request_id=request_id,
                idempotency_key=idempotency_key or operation_id,
                json_body=report.model_dump(),
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {
                "code": "usage_report_failed",
                "message": str(exc.detail),
            }
            return OperationResponse(
                operation_id=operation_id,
                request_id=request_id,
                state="unknown",
                error=detail,
            )
        reported_at = remote.get("reported_at", _now())
        system["last_report_at"] = reported_at
        await self._save_system(system)
        return OperationResponse(
            operation_id=operation_id,
            request_id=request_id,
            state="completed",
            result={
                "report_id": remote.get("report_id"),
                "system_id": report.system_id,
                "reported_at": reported_at,
                "cumulative_consumed": report.cumulative_consumed,
                "cumulative_credits": report.cumulative_credits,
            },
        )

    async def redeem_code(
        self,
        body: RedeemCodeRequest,
        actor: AuthUser,
        *,
        request_id: str = "",
    ) -> dict[str, Any]:
        if not body.confirm:
            raise _error("confirmation_required", "Explicit confirmation is required.", 400)
        parts = body.code.split(".", 2)
        if len(parts) != 3 or parts[0] != "LXRC2":
            raise _error("invalid_recharge_code", "Unsupported recharge code format.", 400)
        try:
            payload_bytes = base64.urlsafe_b64decode(parts[1] + "===")
            payload = json.loads(payload_bytes)
        except (ValueError, json.JSONDecodeError) as exc:
            raise _error("invalid_recharge_code", "The recharge code payload is invalid.", 400) from exc
        if not isinstance(payload, dict):
            raise _error("invalid_recharge_code", "The recharge code payload is invalid.", 400)
        secret = os.getenv("LONGXIN_RECHARGE_CODE_SECRET")
        if not secret:
            raise _error("signer_not_configured", "The recharge signer is not configured.", 503)
        expected = hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, parts[2]):
            raise _error("invalid_recharge_signature", "The recharge code signature is invalid.", 400)
        system = await self._system()
        if payload.get("system_id") != system["system_id"]:
            raise _error("system_id_mismatch", "The recharge code targets another system.", 409)
        tokens = payload.get("tokens")
        amount = payload.get("amount")
        nonce = payload.get("nonce")
        if (
            not isinstance(tokens, int)
            or isinstance(tokens, bool)
            or tokens <= 0
            or not isinstance(amount, str)
            or not nonce
        ):
            raise _error("invalid_recharge_code", "The recharge code fields are invalid.", 400)
        try:
            amount_value = Decimal(amount)
        except InvalidOperation as exc:
            raise _error("invalid_recharge_code", "The recharge amount is invalid.", 400) from exc
        if amount_value <= 0 or amount_value.as_tuple().exponent < -2:
            raise _error("invalid_recharge_code", "The recharge amount is invalid.", 400)
        nonce_key = f"{_PREFIX}:recharge-nonce:{nonce}"
        created = await self._client().set(nonce_key, "1", nx=True)
        if not created:
            raise _error("code_already_redeemed", "The recharge code was already redeemed.", 409)
        async with self._lock:
            system["pool_tokens"] = int(system["pool_tokens"]) + tokens
            system["total_recharged"] = str(
                Decimal(str(system["total_recharged"])) + amount_value,
            )
            system["cumulative_credits"] = int(system.get("cumulative_credits", 0)) + tokens
            await self._save_system(system)
            ledger = await self._append_ledger(
                entry_type="redeem_code",
                delta=tokens,
                balance_after=int(system["pool_tokens"]),
                amount=amount,
                order_id=payload.get("order_id"),
                operator_id=actor.id,
                source="sales_hub",
            )
            order_id = payload.get("order_id")
            if isinstance(order_id, str):
                order = await self._read_json(self._order_key(order_id))
                if order is not None:
                    order.update(
                        {
                            "status": "approved",
                            "delivery_status": "not_delivered",
                            "ledger_id": ledger.ledger_id,
                            "redemption_operation_id": f"op-{uuid4().hex}",
                        },
                    )
                    await self._write_json(self._order_key(order_id), order)
        return {
            "operation_id": f"op-{uuid4().hex}",
            "state": "completed",
            "system_id": system["system_id"],
            "amount": amount,
            "tokens": tokens,
            "ledger_id": ledger.ledger_id,
            "pool_tokens_after": int(system["pool_tokens"]),
            "request_id": request_id,
        }

    async def hub_config(self) -> dict[str, Any]:
        value = await self._read_json(_SALES_HUB_KEY) or {}
        token = value.get("token")
        public_key = value.get("public_key")
        return {
            "system_id": value.get("system_id", (await self._system())["system_id"]),
            "hub_url": value.get("hub_url", ""),
            "token_masked": (
                f"{token[:4]}****{token[-4:]}" if token and len(token) > 8 else None
            ),
            "public_key_fingerprint": (
                f"sha256:{hashlib.sha256(public_key.encode('utf-8')).hexdigest()[:16]}"
                if public_key
                else None
            ),
            "outbound_status": value.get("outbound_status", "unknown"),
            "inbound_status": value.get("inbound_status", "unknown"),
            "last_verified_at": value.get("last_verified_at"),
        }

    async def update_hub_config(self, body: SalesHubConfigUpdate) -> dict[str, Any]:
        value = await self._read_json(_SALES_HUB_KEY) or {}
        if body.system_id is not None:
            value["system_id"] = body.system_id
            system = await self._system()
            system["system_id"] = body.system_id
            await self._save_system(system)
        if body.hub_url is not None:
            parsed = urlparse(body.hub_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise _error("invalid_hub_url", "hub_url must be an HTTP(S) URL.", 422)
            value["hub_url"] = body.hub_url.rstrip("/")
        if body.token:
            value["token"] = body.token
        if body.public_key is not None:
            value["public_key"] = body.public_key
        await self._write_json(_SALES_HUB_KEY, value)
        return await self.hub_config()

    async def verify_hub(self, request_id: str) -> OperationResponse:
        value = await self._read_json(_SALES_HUB_KEY) or {}
        operation_id = f"op-{uuid4().hex}"
        if not value.get("hub_url") or not value.get("token"):
            return OperationResponse(
                operation_id=operation_id,
                request_id=request_id,
                state="failed",
                error={
                    "code": "hub_not_configured",
                    "message": "Sales Hub is not configured.",
                },
            )
        try:
            result = await self._hub_request(
                "POST",
                "/api/v1/integration/verify-connection",
                request_id=request_id,
                idempotency_key=operation_id,
                json_body={},
            )
            value["outbound_status"] = "ok"
            inbound = result.get("inbound", {}) if isinstance(result, dict) else {}
            value["inbound_status"] = "ok" if inbound.get("ok", True) else "failed"
            value["last_verified_at"] = _now()
            await self._write_json(_SALES_HUB_KEY, value)
            return OperationResponse(
                operation_id=operation_id,
                request_id=request_id,
                state="completed",
                result=result,
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {
                "code": "hub_unreachable",
                "message": str(exc.detail),
            }
            value["outbound_status"] = "failed"
            value["inbound_status"] = "unknown"
            await self._write_json(_SALES_HUB_KEY, value)
            return OperationResponse(
                operation_id=operation_id,
                request_id=request_id,
                state="unknown" if exc.status_code >= 500 else "failed",
                error=detail,
            )

    async def _audit(
        self,
        *,
        actor: AuthUser,
        action: str,
        target_id: str | None,
        reason: str,
    ) -> None:
        event = {
            "event_id": f"evt-{uuid4().hex}",
            "actor_id": actor.id,
            "actor_name": actor.username,
            "action": action,
            "target_id": target_id,
            "reason": reason,
            "created_at": _now(),
        }
        await self._client().rpush(_AUDIT_KEY, json.dumps(event, ensure_ascii=False))

    async def authorize_hub(self, authorization: str | None) -> None:
        scheme, _, token = (authorization or "").partition(" ")
        value = await self._read_json(_SALES_HUB_KEY) or {}
        stored = value.get("token")
        if (
            scheme.lower() != "bearer"
            or not token
            or not stored
            or not hmac.compare_digest(token, stored)
        ):
            raise _error("invalid_customer_token", "The Sales Hub token is invalid.", 401)


async def _auth_user(request: Request, authorization: str | None) -> AuthUser:
    auth: JWTAuthService = request.app.state.auth
    return await auth.get_current_user(authorization)


async def require_admin(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AuthUser:
    user = await _auth_user(request, authorization)
    if user.role != "admin" or user.status != "active":
        raise _error("admin_required", "Administrator access is required.", 403)
    return user


def get_admin_service(request: Request) -> AdminService:
    return request.app.state.admin_service


admin_router = APIRouter(prefix="/admin", tags=["admin"])


@admin_router.get("/overview")
async def get_overview(
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> dict[str, Any]:
    return await service.overview()


@admin_router.get("/users", response_model=UserListResponse)
async def list_users(
    request: Request,
    keyword: str | None = Query(default=None, max_length=64),
    account_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> UserListResponse:
    response = await service.list_users(
        keyword=keyword,
        account_status=account_status,
        page=page,
        page_size=page_size,
    )
    response.request_id = request.headers.get("X-Request-ID", "")
    return response


@admin_router.post(
    "/users",
    response_model=AdminUserView,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    body: CreateUserRequest,
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> AdminUserView:
    return await service.create_user(body)


@admin_router.patch("/users/{user_id}", response_model=AdminUserView)
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> AdminUserView:
    return await service.update_user(user_id, body, actor.id)


@admin_router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> None:
    await service.delete_user(user_id, actor.id)


@admin_router.post(
    "/users/{user_id}/reset-password",
    response_model=ResetPasswordResponse,
)
async def reset_password(
    user_id: str,
    body: ResetPasswordRequest,
    request: Request,
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> ResetPasswordResponse:
    response = await service.reset_password(user_id, actor, body)
    response.request_id = request.headers.get("X-Request-ID", "")
    return response


@admin_router.delete("/users/{user_id}/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_sessions(
    user_id: str,
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> None:
    account = await service._auth._account_by_id(user_id)
    if account is None:
        raise _error("user_not_found", "User not found.", 404)
    await service._auth.revoke_sessions(user_id)
    await service._audit(
        actor=actor,
        action="user.revoke_sessions",
        target_id=user_id,
        reason="Administrator session revocation.",
    )


@admin_router.get("/quota", response_model=SystemAccountView)
async def get_quota(
    request: Request,
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> SystemAccountView:
    value = await service.quota()
    return SystemAccountView(**value, request_id=request.headers.get("X-Request-ID", ""))


@admin_router.patch("/quota", response_model=SystemAccountView)
async def update_quota(
    body: QuotaUpdateRequest,
    request: Request,
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> SystemAccountView:
    value = await service.update_quota(body)
    await service._audit(
        actor=actor,
        action="quota.update_test_default",
        target_id=None,
        reason="Administrator updated the test account default.",
    )
    return SystemAccountView(**value, request_id=request.headers.get("X-Request-ID", ""))


@admin_router.get("/quota/ledger", response_model=LedgerListResponse)
async def get_ledger(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> LedgerListResponse:
    entries = await service.ledger(limit)
    return LedgerListResponse(
        entries=entries,
        total=len(entries),
        request_id=request.headers.get("X-Request-ID", ""),
    )


@admin_router.post("/quota/redeem-code")
async def redeem_code(
    body: RedeemCodeRequest,
    request: Request,
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> dict[str, Any]:
    return await service.redeem_code(
        body,
        actor,
        request_id=request.headers.get("X-Request-ID", ""),
    )


@admin_router.post("/quota/recharge-requests", response_model=RechargeRequestView)
async def create_recharge_request(
    body: RechargeRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> RechargeRequestView:
    return await service.create_recharge_request(
        body,
        request_id=request.headers.get("X-Request-ID", ""),
        idempotency_key=idempotency_key,
    )


@admin_router.post(
    "/quota/recharge-requests/sync",
    response_model=OperationResponse,
)
async def sync_recharge_requests(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> OperationResponse:
    return await service.sync_recharge(
        actor,
        request_id=request.headers.get("X-Request-ID", f"req-{uuid4().hex}"),
        idempotency_key=idempotency_key,
    )


@admin_router.get(
    "/quota/recharge-requests",
    response_model=RechargeRequestListResponse,
)
async def list_recharge_requests(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> RechargeRequestListResponse:
    orders = await service.recharge_requests(limit)
    return RechargeRequestListResponse(
        orders=orders,
        total=len(orders),
        request_id=request.headers.get("X-Request-ID", ""),
    )


@admin_router.get("/sales-hub/config", response_model=SalesHubConfigView)
async def get_sales_hub_config(
    request: Request,
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> SalesHubConfigView:
    return SalesHubConfigView(
        **await service.hub_config(),
        request_id=request.headers.get("X-Request-ID", ""),
    )


@admin_router.patch("/sales-hub/config", response_model=SalesHubConfigView)
async def update_sales_hub_config(
    body: SalesHubConfigUpdate,
    request: Request,
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> SalesHubConfigView:
    value = await service.update_hub_config(body)
    await service._audit(
        actor=actor,
        action="sales_hub.update_config",
        target_id=None,
        reason="Administrator updated Sales Hub configuration.",
    )
    return SalesHubConfigView(
        **value,
        request_id=request.headers.get("X-Request-ID", ""),
    )


@admin_router.post("/sales-hub/verify", response_model=OperationResponse)
async def verify_sales_hub(
    request: Request,
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> OperationResponse:
    request_id = request.headers.get("X-Request-ID", f"req-{uuid4().hex}")
    return await service.verify_hub(request_id)


@admin_router.post("/sales-hub/usage-report", response_model=OperationResponse)
async def report_sales_hub_usage(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> OperationResponse:
    return await service.report_usage(
        request_id=request.headers.get("X-Request-ID", f"req-{uuid4().hex}"),
        idempotency_key=idempotency_key,
    )


sales_hub_router = APIRouter(
    prefix="/integration/sales/v1",
    tags=["sales-integration"],
)


@sales_hub_router.post("/ping", response_model=HubPingResponse)
async def sales_hub_ping(
    request: Request,
    authorization: str | None = Header(default=None),
    service: AdminService = Depends(get_admin_service),
) -> HubPingResponse:
    await service.authorize_hub(authorization)
    system = await service._system()
    upgrade_service = getattr(request.app.state, "upgrade_service", None)
    versions = await upgrade_service.current_versions() if upgrade_service is not None else {}
    return HubPingResponse(
        ok=True,
        system_id=system["system_id"],
        system_name=os.getenv("LONGXIN_SYSTEM_NAME", "Longxin AgentScope"),
        app_version=versions.get("app") or os.getenv("LONGXIN_APP_VERSION", "3.8.1"),
        core_version=versions.get("core") or os.getenv("LONGXIN_CORE_VERSION", "unknown"),
        checked_at=_now(),
        request_id=request.headers.get("X-Request-ID", ""),
    )


@sales_hub_router.post("/admin-password-resets")
async def sales_hub_reset_admin_password(
    body: RemotePasswordResetRequest,
    authorization: str | None = Header(default=None),
    service: AdminService = Depends(get_admin_service),
) -> dict[str, Any]:
    await service.authorize_hub(authorization)
    account = await service._auth._account_by_username(body.username)
    if account is None or account.role != "admin":
        raise _error("admin_not_found", "The target administrator was not found.", 404)
    await service._auth.reset_password(account.user_id, body.new_password)
    return {
        "ok": True,
        "operation_id": body.operation_id,
        "username": account.username,
    }
