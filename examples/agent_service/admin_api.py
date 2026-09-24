# -*- coding: utf-8 -*-
"""Product-level administrator APIs for the Longxin AgentScope service."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, ValidationError

from agentscope.app.storage import MCPRecord, SkillRecord
from agentscope.mcp import MCPClient
from auth import AuthUser, JWTAuthService
from longxin_admin.distributed_lock import DistributedLease
from longxin_admin.plan_billing.catalog import (
    PLAN_VALUES,
    UNASSIGNED_MONTHLY_QUOTA,
    UNASSIGNED_PLAN_ID,
    UNASSIGNED_PLAN_NAME,
)
from sales_hub_client import SalesHubClient, SalesHubClientError


_logger = logging.getLogger(__name__)


_PREFIX = "longxin:admin:v1"
_SALES_HUB_KEY = "longxin:sales-hub:v1:config"
_LEDGER_KEY = f"{_PREFIX}:ledger"
_AUDIT_KEY = f"{_PREFIX}:audit"
_RESOURCE_PUBLICATIONS_KEY = f"{_PREFIX}:resource-publications"
# The plan-billing extension owns the catalog.  This projection keeps the
# existing member-management API backward compatible without duplicating the
# plan definitions in the legacy admin module.
_PLANS = PLAN_VALUES
_MONEY_INPUT_PATTERN = r"^[0-9]+(?:\.[0-9]{1,2})?$"
_MONEY_PATTERN = r"^[0-9]+\.[0-9]{2}$"
_DEFAULT_BUILTIN_SKILLS = (
    ("work-report", "工作汇报", "把工作进展组织成结论清晰的汇报。"),
    ("project-initiation", "项目立项", "说明项目为什么做、如何做及需要的资源。"),
    ("customer-solution", "客户方案", "以客户需求为中心组织销售解决方案。"),
    ("training-course", "培训课件", "把知识拆成可理解、可练习的课程。"),
    ("research-report", "研究汇报", "清晰呈现研究问题、依据和结论。"),
    ("presentation-review", "演示内容审阅", "检查演示结构、逻辑和信息密度。"),
    ("executive-brief", "领导简报", "把复杂材料压缩成快速决策所需的信息。"),
    ("job-presentation", "述职演示", "把阶段成果、复盘和计划讲得有重点。"),
    ("roadmap-plan", "路线图与计划", "用阶段、依赖和验收点说明行动计划。"),
    ("solution-comparison", "方案对比", "用统一口径比较多个方案并给出建议。"),
    ("speech-story", "演讲叙事", "增强开场、转场和收束的连续性。"),
    ("data-report", "数据汇报", "让数据结论有口径、有依据、有行动。"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _temporary_password_expiry() -> str:
    try:
        ttl_seconds = int(os.getenv("LONGXIN_TEMP_PASSWORD_TTL_SECONDS", "86400"))
    except ValueError:
        ttl_seconds = 86400
    ttl_seconds = max(60, ttl_seconds)
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()


def _format_money(value: str, *, require_positive: bool = False) -> str:
    """Return a canonical fixed-point amount required by the machine API."""
    try:
        amount = Decimal(value)
        if not amount.is_finite():
            raise InvalidOperation
        if amount < 0 or (require_positive and amount == 0):
            raise InvalidOperation
        quantized = amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise _error("invalid_amount", "Amount must be a non-negative decimal.", 422) from exc
    return format(quantized, ".2f")


def _parse_utc_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise _error("invalid_recharge_code", f"The recharge code {field} is invalid.", 400)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _error("invalid_recharge_code", f"The recharge code {field} is invalid.", 400) from exc
    if parsed.tzinfo is None:
        raise _error("invalid_recharge_code", f"The recharge code {field} needs a timezone.", 400)
    return parsed.astimezone(timezone.utc)


def _secret_cipher() -> Any | None:
    secret = os.getenv("LONGXIN_CONFIG_ENCRYPTION_KEY") or os.getenv("AGENTSCOPE_JWT_SECRET")
    if not secret:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise _error(
            "crypto_unavailable",
            "Secret encryption support is not installed.",
            503,
        ) from exc
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _seal_secret(value: str) -> str:
    cipher = _secret_cipher()
    if cipher is None:
        return value
    return "fernet:v1:" + cipher.encrypt(value.encode("utf-8")).decode("ascii")


def _unseal_secret(value: str) -> str:
    if not value.startswith("fernet:v1:"):
        return value
    cipher = _secret_cipher()
    if cipher is None:
        raise _error(
            "secret_key_not_configured",
            "The configuration encryption key is not configured.",
            503,
        )
    try:
        return cipher.decrypt(value.removeprefix("fernet:v1:").encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise _error(
            "secret_decryption_failed",
            "The stored Sales Hub token could not be decrypted.",
            503,
        ) from exc


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


def _decode_base64url(value: str) -> bytes:
    try:
        return base64.b64decode(
            value.encode("ascii") + b"=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeEncodeError) as exc:
        raise _error("invalid_recharge_code", "The encoded recharge value is invalid.", 400) from exc


def _load_ed25519_public_key(value: Any) -> Any:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        if not isinstance(value, str):
            raise TypeError("The configured public key must be text.")

        encoded_key = value.strip()
        if "BEGIN PUBLIC KEY" in encoded_key:
            signer = serialization.load_pem_public_key(encoded_key.encode("utf-8"))
        else:
            try:
                key_bytes = _decode_base64url(encoded_key)
            except HTTPException:
                try:
                    # Sales Hub returns the SubjectPublicKeyInfo DER value as
                    # standard Base64 (including '+'/'/' and optional '=');
                    # accept that representation in addition to raw base64url.
                    key_bytes = base64.b64decode(
                        encoded_key.encode("ascii"),
                        validate=True,
                    )
                except (binascii.Error, UnicodeEncodeError, ValueError):
                    key_bytes = bytes.fromhex(encoded_key)
            try:
                signer = Ed25519PublicKey.from_public_bytes(key_bytes)
            except ValueError:
                # A standard Base64 public key may contain the DER/SPKI
                # wrapper rather than only the 32-byte Ed25519 key.
                signer = serialization.load_der_public_key(key_bytes)
        if not isinstance(signer, Ed25519PublicKey):
            raise TypeError("The configured key is not an Ed25519 public key.")
        return signer
    except Exception as exc:
        raise _error("invalid_public_key", "The configured public key is invalid.", 422) from exc


class AdminUserView(BaseModel):
    id: str
    username: str
    subject_id: str | None = None
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


class DeleteUserRequest(UserActionRequest):
    confirm: bool = False


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
    account_count: int
    admin_count: int
    updated_at: str
    request_id: str


class LedgerEntryView(BaseModel):
    ledger_id: str
    type: str
    delta_tokens: int
    balance_after: int
    amount: str | None = None
    order_id: str | None = None
    related_user_id: str | None = None
    operator_id: str | None = None
    idempotency_key: str | None = None
    source: str
    created_at: str


class LedgerListResponse(BaseModel):
    entries: list[LedgerEntryView]
    total: int
    request_id: str


class AuditEventView(BaseModel):
    event_id: str
    actor_type: Literal["admin", "user", "system"] = "admin"
    actor_id: str
    actor_name: str
    target_user_id: str | None = None
    target_user_name: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    reason: str
    request_id: str = ""
    status: Literal["completed", "failed"] = "completed"
    result_summary: str | None = None
    created_at: str


class AuditEventListResponse(BaseModel):
    events: list[AuditEventView]
    total: int
    request_id: str


class AuditOverviewRequest(UserActionRequest):
    target_user_id: str = Field(min_length=1, max_length=128)


class AuditSessionAccessRequest(UserActionRequest):
    overview_event_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)


class AuditDocumentAccessRequest(UserActionRequest):
    overview_event_id: str = Field(min_length=1, max_length=128)
    knowledge_base_id: str = Field(min_length=1, max_length=128)


class AuditResourceResponse(BaseModel):
    target_user_id: str
    resource_type: Literal["overview", "session", "document"]
    resource_id: str | None = None
    data: dict[str, Any]
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


ResourceKind = Literal["mcp", "skill"]
PublicationScope = Literal["all", "selected", "none"]


class ResourcePublicationRequest(BaseModel):
    """The safe catalog projection an administrator wants to publish.

    MCP configuration values are deliberately not part of this model. The
    server uses ``source_record_id`` to copy the administrator's configured
    record without ever returning its secrets to the browser. Built-in
    skills use ``source_id`` without a storage record.
    """

    kind: ResourceKind
    source_id: str = Field(min_length=1, max_length=256)
    source_record_id: str | None = Field(default=None, max_length=256)
    name: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=256)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    author: str | None = Field(default=None, max_length=256)
    icon_url: str | None = Field(default=None, max_length=2000)
    version: str | None = Field(default=None, max_length=128)
    scope: PublicationScope = "none"
    user_ids: list[str] = Field(default_factory=list, max_length=5000)
    enabled: bool = True


class ResourcePublicationView(BaseModel):
    id: str
    kind: ResourceKind
    source_id: str
    source_record_id: str | None = None
    name: str
    display_name: str | None = None
    description: str
    tags: list[str]
    author: str | None = None
    icon_url: str | None = None
    version: str | None = None
    scope: PublicationScope
    user_ids: list[str]
    enabled: bool
    updated_at: str


class ResourcePublicationListResponse(BaseModel):
    resources: list[ResourcePublicationView]
    total: int


class PublishedResourceView(BaseModel):
    id: str
    kind: ResourceKind
    name: str
    display_name: str | None = None
    description: str
    tags: list[str]
    author: str | None = None
    icon_url: str | None = None
    version: str | None = None


class PublishedResourceListResponse(BaseModel):
    resources: list[PublishedResourceView]
    total: int


class RechargeRequest(BaseModel):
    amount: str = Field(pattern=_MONEY_INPUT_PATTERN)
    note: str | None = Field(default=None, max_length=300)


class RechargeRequestView(BaseModel):
    order_id: str
    system_id: str
    amount: str
    status: Literal["pending", "approved", "rejected", "unknown"]
    delivery_status: Literal["not_delivered", "delivered"] = "not_delivered"
    created_at: str
    request_id: str = ""


class RechargeRequestListResponse(BaseModel):
    orders: list[RechargeRequestView]
    total: int
    request_id: str


class RechargePollItem(BaseModel):
    order_id: str
    system_id: str
    amount: str = Field(pattern=_MONEY_PATTERN)
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


class RechargeAckResponse(BaseModel):
    order_id: str = Field(min_length=1, max_length=128)
    status: Literal["approved"]
    delivery_status: Literal["delivered"]
    request_id: str = Field(min_length=1, max_length=128)


class UsageReportRequest(BaseModel):
    system_id: str = Field(min_length=1, max_length=128)
    pool_tokens: int = Field(ge=0)
    total_recharged: str = Field(pattern=_MONEY_PATTERN)
    app_version: str = Field(min_length=1, max_length=128)
    cumulative_consumed: int = Field(ge=0)
    cumulative_credits: int = Field(ge=0)
    client_reported_at: str


class UsageReportResponse(BaseModel):
    report_id: str = Field(min_length=1, max_length=128)
    system_id: str = Field(min_length=1, max_length=128)
    accepted: bool
    reported_at: str = Field(min_length=1)
    cumulative_consumed_delta: int = Field(ge=0)
    cumulative_credits_delta: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=128)


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


class AdminPolicyView(BaseModel):
    admin_api_requires_admin_role: bool
    credential_management: Literal["admin_only"]
    sales_hub_authentication: Literal["customer_bearer_token"]
    recharge_legacy_hmac_enabled: bool
    high_risk_plugin_installation: Literal["disabled"]
    policy_mutation_from_chat: bool
    request_id_enforced: bool
    generated_at: str


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


class RemotePasswordResetResponse(BaseModel):
    ok: bool
    operation_id: str
    username: str
    request_id: str


class AdminService:
    """Persist product management data beside the existing AgentScope store."""

    def __init__(
        self,
        storage: Any,
        auth: JWTAuthService,
        plan_billing: Any | None = None,
        workspace_service_provider: Any | None = None,
    ) -> None:
        self._storage = storage
        self._auth = auth
        self._plan_billing = plan_billing
        # Kept as a provider because the generic app creates the workspace
        # service during its lifespan. This stays in the product layer and
        # avoids changing AgentScope's application factory.
        self._workspace_service_provider = workspace_service_provider
        self._lock = asyncio.Lock()
        self._sales_hub_client = SalesHubClient(self._hub_connection_config)

    def _mutation_lock(self) -> DistributedLease:
        return DistributedLease(
            self._client,
            self._lock,
            f"{_PREFIX}:mutation-lock",
        )

    def _client(self) -> Any:
        client = self._storage.get_client()
        if client is None:
            raise _error("storage_not_ready", "Admin storage is not ready.", 503)
        return client

    async def _hub_connection_config(self) -> dict[str, Any]:
        value = await self._read_json(_SALES_HUB_KEY) or {}
        token = value.get("token")
        if isinstance(token, str) and token:
            value["token"] = _unseal_secret(token)
        return value

    @staticmethod
    def _request_id(request: Request) -> str:
        state_value = getattr(request.state, "request_id", "")
        if state_value:
            return state_value
        value = request.headers.get("X-Request-ID", "").strip()
        if value:
            return value
        generated = f"req-{uuid4().hex}"
        request.state.request_id = generated
        return generated

    @staticmethod
    def _user_key(user_id: str) -> str:
        return f"{_PREFIX}:user:{user_id}"

    @staticmethod
    def _order_key(order_id: str) -> str:
        return f"{_PREFIX}:order:{order_id}"

    @staticmethod
    def _sales_code_order(
        *,
        order_id: str,
        system_id: str,
        amount: str,
        tokens: int,
        nonce: str,
        issued_at: datetime,
        request_id: str,
    ) -> dict[str, Any]:
        """Create the local shadow order for a directly issued Sales code."""
        return {
            "order_id": order_id,
            "system_id": system_id,
            "amount": amount,
            "status": "approved",
            "delivery_status": "not_delivered",
            "created_at": issued_at.isoformat(),
            "request_id": request_id,
            "source": "sales_hub_code",
            "tokens": tokens,
            "nonce": nonce,
        }

    @staticmethod
    def _idempotency_key(operation: str, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"{_PREFIX}:idempotency:{operation}:{digest}"

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def _read_idempotent(
        self,
        key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        saved = await self._read_json(key)
        if saved is None:
            return None
        # Accept the old raw-result format so an in-flight development
        # deployment can be upgraded without replaying a remote operation.
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
            "plan_id": UNASSIGNED_PLAN_ID,
            "plan_name": UNASSIGNED_PLAN_NAME,
            "monthly_quota": UNASSIGNED_MONTHLY_QUOTA,
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
            subject_id=account.subject_id,
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
        plan_id: str | None,
        page: int,
        page_size: int,
    ) -> UserListResponse:
        accounts = [
            item
            for item in await self._auth.list_accounts()
            if item.status != "deleted"
        ]
        if keyword:
            needle = keyword.casefold()
            accounts = [
                item
                for item in accounts
                if needle in item.username.casefold() or needle in item.id.casefold()
            ]
        if account_status:
            accounts = [item for item in accounts if item.status == account_status]
        views = [await self._view(item) for item in accounts]
        if plan_id:
            views = [item for item in views if item.plan_id == plan_id]
        total = len(views)
        start = (page - 1) * page_size
        return UserListResponse(
            users=views[start : start + page_size],
            total=total,
            page=page,
            page_size=page_size,
            request_id="",
        )

    async def _resource_publications(self) -> list[dict[str, Any]]:
        value = await self._read_json(_RESOURCE_PUBLICATIONS_KEY)
        items = value.get("resources", []) if value else []
        return [item for item in items if isinstance(item, dict)]

    async def _save_resource_publications(
        self,
        resources: list[dict[str, Any]],
    ) -> None:
        await self._write_json(_RESOURCE_PUBLICATIONS_KEY, {"resources": resources})

    async def ensure_default_builtin_publications(self) -> None:
        """Seed product-owned skills as visible to everyone on first boot."""

        resources = await self._resource_publications()
        existing_ids = {item.get("id") for item in resources}
        changed = False
        for source_id, display_name, description in _DEFAULT_BUILTIN_SKILLS:
            publication_id = self._resource_publication_id("skill", source_id)
            if publication_id in existing_ids:
                existing = next(
                    item for item in resources if item.get("id") == publication_id
                )
                # Migrate the first version of the seed, which used the
                # directory id as ``name`` instead of SKILL.md's name.
                if existing.get("name") == source_id:
                    existing["name"] = display_name
                    existing["display_name"] = display_name
                    existing["description"] = description
                    existing["updated_at"] = _now()
                    changed = True
                continue
            resources.append(
                {
                    "id": publication_id,
                    "kind": "skill",
                    "source_id": source_id,
                    "source_record_id": None,
                    "name": display_name,
                    "display_name": display_name,
                    "description": description,
                    "tags": ["办公流程"],
                    "author": "Longxin",
                    "icon_url": None,
                    "version": "builtin",
                    "scope": "all",
                    "user_ids": [],
                    "enabled": True,
                    "updated_at": _now(),
                    "provisioned": {},
                },
            )
            existing_ids.add(publication_id)
            changed = True
        if changed:
            await self._save_resource_publications(resources)

    @staticmethod
    def _resource_publication_id(kind: str, source_id: str) -> str:
        return f"{kind}:{source_id}"

    @staticmethod
    def _publication_targets(
        publication: dict[str, Any],
        accounts: list[AuthUser],
    ) -> set[str]:
        active_ids = {account.id for account in accounts if account.status == "active"}
        scope = publication.get("scope")
        if scope == "all":
            return active_ids
        if scope == "selected":
            return set(publication.get("user_ids", [])) & active_ids
        return set()

    @staticmethod
    def _copy_mcp_record(source: MCPRecord, user_id: str) -> MCPRecord:
        """Create a storage-safe copy of an installed MCP record.

        ``MCPClient`` owns private runtime connection state.  A deep copy of
        a client that has been used by a workspace can therefore reach an
        async generator and fail during publication.  Rebuild the client
        from its public, serializable configuration instead; this preserves
        the desired MCP configuration without sharing a live connection
        between users.
        """

        record_data = source.model_dump(mode="json", exclude={"name"})
        record_data["user_id"] = user_id
        record_data["client"] = MCPClient.model_validate(
            source.client.model_dump(mode="json"),
        )
        return MCPRecord.model_validate(record_data)

    async def _sync_resource_publication(
        self,
        publication: dict[str, Any],
        accounts: list[AuthUser],
    ) -> bool:
        """Copy an administrator-owned record to the current target users.

        The copied MCP record retains its server-side configuration, while
        public catalog responses only contain the safe display projection.
        Built-in skills do not need a copy because the workspace manager
        seeds their files when a workspace is created.
        """

        if not publication.get("enabled", True):
            targets: set[str] = set()
        else:
            targets = self._publication_targets(publication, accounts)

        kind = publication.get("kind")
        source_user_id = publication.get("source_user_id")
        source_record_id = publication.get("source_record_id")
        provisioned = {
            str(user_id): str(record_id)
            for user_id, record_id in (publication.get("provisioned") or {}).items()
        }
        changed = False

        source: MCPRecord | SkillRecord | None = None
        if source_user_id and source_record_id:
            if kind == "mcp":
                source = await self._storage.get_mcp(source_user_id, source_record_id)
            elif kind == "skill":
                source = await self._storage.get_skill(source_user_id, source_record_id)

        if publication.get("tenant_id"):
            # Organization-owned records are shared in place. The legacy
            # local-account flow below still provisions individual copies.
            for user_id, record_id in provisioned.items():
                if kind == "mcp":
                    await self._storage.delete_mcp(user_id, record_id)
                elif kind == "skill":
                    await self._storage.delete_skill(user_id, record_id)
            if provisioned:
                publication["provisioned"] = {}
                return True
            return False

        if source is not None and kind in {"mcp", "skill"}:
            for user_id in targets:
                copied = (
                    self._copy_mcp_record(source, user_id)
                    if kind == "mcp"
                    else source.model_copy(deep=True)
                )
                if kind == "skill":
                    copied.user_id = user_id
                copied.enabled = True
                if kind == "mcp":
                    existing = await self._storage.get_mcp_by_name(
                        user_id,
                        source.client.name,
                    )
                    if existing is not None and existing.id != copied.id:
                        # Do not overwrite a user's own same-named resource.
                        # The published catalog still exposes the approved
                        # name, while the user's existing record remains
                        # recoverable when publication is withdrawn.
                        continue
                    await self._storage.upsert_mcp(user_id, copied)
                else:
                    existing = await self._storage.get_skill_by_name(
                        user_id,
                        source.name,
                    )
                    if existing is not None and existing.id != copied.id:
                        continue
                    await self._storage.upsert_skill(user_id, copied)
                if provisioned.get(user_id) != copied.id:
                    provisioned[user_id] = copied.id
                    changed = True

        for user_id in set(provisioned) - targets:
            record_id = provisioned[user_id]
            # The source record belongs to the administrator and is the
            # configuration authority. Unpublishing must not delete it.
            if user_id == source_user_id:
                provisioned.pop(user_id, None)
                changed = True
                continue
            if kind == "mcp":
                await self._storage.delete_mcp(user_id, record_id)
            elif kind == "skill":
                await self._storage.delete_skill(user_id, record_id)
            provisioned.pop(user_id, None)
            changed = True

        if publication.get("provisioned") != provisioned:
            publication["provisioned"] = provisioned
            changed = True
        return changed

    async def _sync_resource_publications(
        self,
        resources: list[dict[str, Any]],
    ) -> None:
        accounts = await self._auth.list_accounts()
        changed = False
        for publication in resources:
            changed = await self._sync_resource_publication(
                publication,
                accounts,
            ) or changed
        if changed:
            await self._save_resource_publications(resources)

    async def list_resource_publications(
        self,
        kind: ResourceKind | None = None,
    ) -> ResourcePublicationListResponse:
        resources = await self._resource_publications()
        if kind is not None:
            resources = [item for item in resources if item.get("kind") == kind]
        views = [ResourcePublicationView.model_validate(item) for item in resources]
        return ResourcePublicationListResponse(resources=views, total=len(views))

    async def managed_resource_names(
        self,
        kind: ResourceKind,
    ) -> set[str]:
        """Return names controlled by the administrator publication catalog."""

        return {
            str(item["name"])
            for item in await self._resource_publications()
            if item.get("kind") == kind and isinstance(item.get("name"), str)
        }

    async def resolve_published_mcp_for_user(
        self,
        user: AuthUser,
        publication_id: str,
    ) -> MCPRecord:
        """Resolve the server-side MCP record currently authorized for a user.

        The publication id is the only value accepted from a browser.  This
        prevents a user from substituting another library record id or an MCP
        configuration when attaching a managed MCP to a workspace.
        """

        if user.status != "active":
            raise _error("mcp_not_authorized", "The user is not active.", 403)
        async with self._mutation_lock():
            resources = await self._resource_publications()
            publication = next(
                (
                    item
                    for item in resources
                    if item.get("id") == publication_id
                    and item.get("kind") == "mcp"
                ),
                None,
            )
            if publication is None:
                raise _error(
                    "mcp_publication_not_found",
                    "The published MCP was not found.",
                    404,
                )

            accounts = await self._auth.list_accounts()
            targets = self._publication_targets(publication, accounts)
            if not publication.get("enabled", True) or user.id not in targets:
                raise _error(
                    "mcp_not_authorized",
                    "The administrator has not authorized this MCP for you.",
                    403,
                )

            if await self._sync_resource_publication(publication, accounts):
                await self._save_resource_publications(resources)

            if publication.get("tenant_id"):
                if publication.get("tenant_id") != user.tenant_id:
                    raise _error(
                        "mcp_not_authorized",
                        "The MCP is not published to your organization.",
                        403,
                    )
                record = await self._storage.get_mcp(
                    str(publication.get("tenant_id")),
                    str(publication.get("source_record_id") or ""),
                )
            else:
                record_id = (publication.get("provisioned") or {}).get(user.id)
                record = (
                    await self._storage.get_mcp(user.id, record_id)
                    if isinstance(record_id, str)
                    else None
                )
            if record is None:
                raise _error(
                    "mcp_publication_unavailable",
                    "The authorized MCP is not available in your library.",
                    409,
                )
            return record

    async def publish_resource(
        self,
        body: ResourcePublicationRequest,
        actor: AuthUser,
    ) -> ResourcePublicationView:
        is_tenant_resource = bool(actor.tenant_id)
        if not is_tenant_resource and body.scope == "selected" and not body.user_ids:
            raise _error(
                "users_required",
                "Select at least one user for a selected publication.",
                422,
            )

        accounts = await self._auth.list_accounts()
        known_ids = {account.id for account in accounts if account.status == "active"}
        unknown_ids = set(body.user_ids) - known_ids if not is_tenant_resource else set()
        if unknown_ids:
            raise _error(
                "user_not_found",
                "One or more selected users do not exist or are inactive.",
                422,
            )

        source_record: MCPRecord | SkillRecord | None = None
        source_owner_id = actor.tenant_id or actor.id
        if body.source_record_id:
            if body.kind == "mcp":
                source_record = await self._storage.get_mcp(source_owner_id, body.source_record_id)
            else:
                source_record = await self._storage.get_skill(source_owner_id, body.source_record_id)
            if source_record is None:
                raise _error("resource_not_found", "The administrator resource was not found.", 404)
        elif body.kind == "mcp":
            raise _error("resource_record_required", "An installed MCP record is required.", 422)

        publication_id = self._resource_publication_id(body.kind, body.source_id)
        async with self._mutation_lock():
            resources = await self._resource_publications()
            existing = next(
                (item for item in resources if item.get("id") == publication_id),
                None,
            )
            previous_targets = (
                self._publication_targets(existing, accounts)
                if existing is not None and existing.get("enabled", True)
                else set()
            )
            publication = {
                **(existing or {}),
                **body.model_dump(),
                "id": publication_id,
                "source_user_id": source_owner_id,
                "updated_at": _now(),
            }
            if is_tenant_resource:
                publication.update(
                    tenant_id=actor.tenant_id,
                    scope="all",
                    user_ids=[],
                    provisioned={},
                )
            if source_record is not None:
                publication["source_record_id"] = source_record.id
            if existing is None:
                resources.append(publication)
            else:
                resources[resources.index(existing)] = publication
            await self._sync_resource_publication(publication, accounts)
            await self._save_resource_publications(resources)
            if body.kind == "mcp":
                current_targets = (
                    self._publication_targets(publication, accounts)
                    if publication.get("enabled", True)
                    else set()
                )
                revoked_user_ids = previous_targets - current_targets
                if revoked_user_ids:
                    await self._remove_mcp_from_workspaces(
                        {publication["name"]},
                        [
                            account
                            for account in accounts
                            if account.id in revoked_user_ids
                        ],
                    )
            return ResourcePublicationView.model_validate(publication)

    async def remove_installed_skill(
        self,
        skill_id: str,
        actor: AuthUser,
    ) -> None:
        """Remove an administrator skill and its existing workspace copies.

        The library record and publication are application data, while the
        extracted files live in workspaces. Deleting only the record leaves
        those files usable by a later AgentScope turn, so the admin action
        explicitly reconciles both layers through the existing workspace
        service API.
        """

        source = await self._storage.get_skill(actor.id, skill_id)
        if source is None:
            raise _error("resource_not_found", "The administrator skill was not found.", 404)

        accounts = await self._auth.list_accounts()
        resources = await self._resource_publications()
        publication_id = self._resource_publication_id("skill", skill_id)
        publication = next(
            (item for item in resources if item.get("id") == publication_id),
            None,
        )

        async with self._mutation_lock():
            # Withdraw first so no new user-library copy can be provisioned
            # while the existing workspaces are being reconciled.
            if publication is not None:
                withdrawn = {
                    **publication,
                    "scope": "none",
                    "user_ids": [],
                    "enabled": False,
                }
                await self._sync_resource_publication(withdrawn, accounts)

            await self._remove_skill_from_workspaces(
                {
                    name
                    for name in (
                        getattr(source, "name", None),
                        publication.get("name") if publication else None,
                        publication.get("display_name") if publication else None,
                    )
                    if isinstance(name, str) and name
                },
                accounts,
            )
            await self._storage.delete_skill(actor.id, skill_id)
            resources = [
                item for item in resources if item.get("id") != publication_id
            ]
            await self._save_resource_publications(resources)

    async def remove_installed_mcp(
        self,
        mcp_id: str,
        actor: AuthUser,
    ) -> None:
        """Remove an administrator MCP and withdraw its publication."""

        source = await self._storage.get_mcp(actor.id, mcp_id)
        if source is None:
            raise _error("resource_not_found", "The administrator MCP was not found.", 404)

        accounts = await self._auth.list_accounts()
        resources = await self._resource_publications()
        publication_id = self._resource_publication_id("mcp", mcp_id)
        publication = next(
            (item for item in resources if item.get("id") == publication_id),
            None,
        )

        async with self._mutation_lock():
            if publication is not None:
                withdrawn = {
                    **publication,
                    "scope": "none",
                    "user_ids": [],
                    "enabled": False,
                }
                await self._sync_resource_publication(withdrawn, accounts)
                await self._remove_mcp_from_workspaces(
                    {str(publication["name"])},
                    accounts,
                )

            await self._storage.delete_mcp(actor.id, mcp_id)
            resources = [
                item for item in resources if item.get("id") != publication_id
            ]
            await self._save_resource_publications(resources)

    async def _remove_skill_from_workspaces(
        self,
        skill_names: set[str],
        accounts: list[AuthUser],
    ) -> None:
        provider = self._workspace_service_provider
        if not skill_names or provider is None:
            return
        workspace_service = provider()
        if workspace_service is None:
            return

        # A workspace can be shared by several sessions. Resolve each
        # workspace id once, but still inspect every user/agent pair.
        seen: set[tuple[str, str, str]] = set()
        for account in accounts:
            if account.status == "deleted":
                continue
            try:
                agents = await self._storage.list_agents(account.id)
            except Exception:
                _logger.exception("Unable to enumerate agents for skill cleanup")
                continue
            for agent in agents:
                try:
                    sessions = await self._storage.list_sessions(account.id, agent.id)
                except Exception:
                    _logger.exception(
                        "Unable to enumerate sessions for skill cleanup: %s",
                        agent.id,
                    )
                    continue
                for session in sessions:
                    workspace_id = getattr(session.config, "workspace_id", None)
                    key = (account.id, agent.id, workspace_id or "__agent__")
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        workspace = await workspace_service.resolve(
                            account.id,
                            agent.id,
                            session.id,
                        )
                        available = await workspace.list_skills(agent_id=agent.id)
                        for skill in available:
                            if skill.name in skill_names:
                                await workspace.remove_skill(
                                    skill.name,
                                    agent_id=agent.id,
                                )
                    except Exception:
                        # A stale/closed remote workspace must not prevent the
                        # administrator from removing the catalog record.
                        _logger.exception(
                            "Unable to remove deleted skill from workspace %s",
                            key,
                        )

    async def _remove_mcp_from_workspaces(
        self,
        mcp_names: set[str],
        accounts: list[AuthUser],
    ) -> None:
        """Immediately detach withdrawn managed MCPs from live workspaces."""

        provider = self._workspace_service_provider
        if not mcp_names or provider is None:
            return
        workspace_service = provider()
        if workspace_service is None:
            return
        seen: set[tuple[str, str, str]] = set()
        for account in accounts:
            if account.status == "deleted":
                continue
            try:
                agents = await self._storage.list_agents(account.id)
            except Exception:
                _logger.exception("Unable to enumerate agents for MCP cleanup")
                continue
            for agent in agents:
                try:
                    sessions = await self._storage.list_sessions(account.id, agent.id)
                except Exception:
                    _logger.exception(
                        "Unable to enumerate sessions for MCP cleanup: %s",
                        agent.id,
                    )
                    continue
                for session in sessions:
                    workspace_id = getattr(session.config, "workspace_id", None)
                    key = (account.id, agent.id, workspace_id or "__agent__")
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        workspace = await workspace_service.resolve(
                            account.id,
                            agent.id,
                            session.id,
                        )
                        attached = await workspace.list_mcps(
                            agent_id=agent.id,
                            session_id=session.id,
                        )
                        for client in attached:
                            if client.name in mcp_names:
                                await workspace.remove_mcp(
                                    client.name,
                                    agent_id=agent.id,
                                    session_id=session.id,
                                )
                    except Exception:
                        _logger.exception(
                            "Unable to remove withdrawn MCP from workspace %s",
                            key,
                        )

    async def published_resources(
        self,
        user: AuthUser,
        kind: ResourceKind | None = None,
    ) -> PublishedResourceListResponse:
        resources = await self._resource_publications()
        await self._sync_resource_publications(resources)
        visible = [
            item
            for item in resources
            if item.get("enabled", True)
            and item.get("scope") in {"all", "selected"}
            and (item.get("scope") == "all" or user.id in item.get("user_ids", []))
            and (kind is None or item.get("kind") == kind)
        ]
        views = [
            PublishedResourceView(
                id=item["id"],
                kind=item["kind"],
                name=item["name"],
                display_name=item.get("display_name"),
                description=item.get("description", ""),
                tags=item.get("tags", []),
                author=item.get("author"),
                icon_url=item.get("icon_url"),
                version=item.get("version"),
            )
            for item in visible
        ]
        return PublishedResourceListResponse(resources=views, total=len(views))

    async def create_user(
        self,
        body: CreateUserRequest,
        actor: AuthUser,
        *,
        request_id: str,
        idempotency_key: str,
    ) -> AdminUserView:
        if body.plan_id not in _PLANS:
            raise _error("plan_not_found", "The selected plan does not exist.", 409)
        request_fingerprint = {"operation": "user.create", **body.model_dump()}
        idem_key = self._idempotency_key(f"user-create:{actor.id}", idempotency_key)
        async with self._mutation_lock():
            existing = await self._read_idempotent(idem_key, request_fingerprint)
            if existing is not None:
                return AdminUserView.model_validate(existing)
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
            # selected plan is provisioned immediately; callers that omit it
            # create an account with no plan and zero monthly quota.
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
                await self._save_profile(
                    {
                        "user_id": account.id,
                        "plan_id": UNASSIGNED_PLAN_ID,
                        "plan_name": UNASSIGNED_PLAN_NAME,
                        "monthly_quota": UNASSIGNED_MONTHLY_QUOTA,
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
                    idempotency_key=idempotency_key,
                )
            view = await self._view(account)
            await self._audit(
                actor=actor,
                action="user.create",
                target_id=account.id,
                reason="Administrator created a member.",
                request_id=request_id,
                result_summary=f"plan_id={view.plan_id}; bonus_tokens={body.bonus_tokens}",
            )
            await self._write_idempotent(idem_key, request_fingerprint, view.model_dump())
            return view

    async def update_user(
        self,
        user_id: str,
        body: UpdateUserRequest,
        actor: AuthUser,
        *,
        request_id: str,
        idempotency_key: str,
    ) -> AdminUserView:
        request_fingerprint = {
            "operation": "user.update",
            "user_id": user_id,
            **body.model_dump(),
        }
        idem_key = self._idempotency_key(f"user-update:{actor.id}", idempotency_key)
        async with self._mutation_lock():
            existing = await self._read_idempotent(idem_key, request_fingerprint)
            if existing is not None:
                return AdminUserView.model_validate(existing)
            account = await self._auth._account_by_id(user_id)
            if account is None:
                raise _error("user_not_found", "User not found.", 404)
            if user_id == actor.id and body.status in {"banned", "locked"}:
                raise _error("last_admin_protected", "The current admin cannot be disabled.", 409)
            if account.role == "admin" and body.status in {"banned", "locked"}:
                active_admins = [
                    item
                    for item in await self._auth.list_accounts()
                    if item.role == "admin" and item.status == "active"
                ]
                if len(active_admins) <= 1:
                    raise _error("last_admin_protected", "At least one admin is required.", 409)
            account_view = self._auth._public_user(account)
            profile = await self._profile(account_view)
            system = await self._system()
            if body.plan_id is not None:
                if self._plan_billing is not None:
                    await self._plan_billing.admin_assign_plan(
                        user_id,
                        body.plan_id,
                        actor.id,
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
                        idempotency_key=idempotency_key,
                    )
            await self._save_profile(profile)
            if body.status is not None:
                account = await self._auth.set_status(user_id, body.status)
                account_view = account
            view = await self._view(account_view)
            await self._audit(
                actor=actor,
                action="user.update",
                target_id=user_id,
                reason="Administrator updated member settings.",
                request_id=request_id,
                result_summary=(
                    f"status={view.status}; plan_id={view.plan_id}; "
                    f"bonus_tokens={view.bonus_tokens}"
                ),
            )
            await self._write_idempotent(idem_key, request_fingerprint, view.model_dump())
            return view

    async def delete_user(
        self,
        user_id: str,
        actor: AuthUser,
        body: DeleteUserRequest,
        *,
        request_id: str,
        idempotency_key: str,
    ) -> None:
        if not body.confirm:
            raise _error("confirmation_required", "Explicit confirmation is required.", 400)
        request_fingerprint = {
            "operation": "user.delete",
            "user_id": user_id,
            **body.model_dump(),
        }
        idem_key = self._idempotency_key(f"user-delete:{actor.id}", idempotency_key)
        async with self._mutation_lock():
            existing = await self._read_idempotent(idem_key, request_fingerprint)
            if existing is not None:
                return
            account = await self._auth._account_by_id(user_id)
            if account is None:
                raise _error("user_not_found", "User not found.", 404)
            if user_id == actor.id or account.role == "admin":
                raise _error("admin_protected", "An administrator cannot be deleted.", 409)
            await self._auth.set_status(user_id, "deleted")
            await self._auth.revoke_sessions(user_id)
            await self._audit(
                actor=actor,
                action="user.delete",
                target_id=user_id,
                reason=body.reason,
                request_id=request_id,
                result_summary="Member soft-deleted; profile and ledger retained.",
            )
            await self._write_idempotent(
                idem_key,
                request_fingerprint,
                {"state": "completed", "user_id": user_id},
            )

    async def reset_password(
        self,
        user_id: str,
        actor: AuthUser,
        body: ResetPasswordRequest,
        *,
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> ResetPasswordResponse:
        if not await self._auth.verify_password(actor.id, body.admin_password):
            raise _error("admin_password_invalid", "The current admin password is invalid.", 403)
        if not idempotency_key:
            raise _error("idempotency_required", "Idempotency-Key is required.", 400)
        fingerprint_payload = {
            "operation": "reset_password",
            "user_id": user_id,
            "reason": body.reason,
        }
        idem_key = self._idempotency_key("reset-password", idempotency_key)
        async with self._mutation_lock():
            existing = await self._read_json(idem_key)
            if existing is not None:
                if existing.get("fingerprint") != self._fingerprint(fingerprint_payload):
                    raise _error(
                        "idempotency_key_reused",
                        "The idempotency key was already used with another request.",
                        409,
                    )
                raise _error(
                    "operation_already_processed",
                    "The password reset operation was already processed; the temporary password was shown once.",
                    409,
                )
            now = datetime.now(timezone.utc).timestamp()
            rate_key = self._idempotency_key("password-reset-rate", actor.id)
            rate_state = await self._read_json(rate_key) or {"timestamps": []}
            timestamps = [
                float(value)
                for value in rate_state.get("timestamps", [])
                if isinstance(value, (int, float)) and now - float(value) < 300
            ]
            if len(timestamps) >= 5:
                raise _error(
                    "rate_limited",
                    "Password reset rate limit exceeded. Try again later.",
                    429,
                )
            timestamps.append(now)
            await self._write_json(rate_key, {"timestamps": timestamps})
            account = await self._auth._account_by_id(user_id)
            if account is None or account.status == "deleted":
                raise _error("user_not_found", "User not found.", 404)
            await self._write_json(
                idem_key,
                {
                    "fingerprint": self._fingerprint(fingerprint_payload),
                    "state": "started",
                },
            )
            temporary_password = secrets.token_urlsafe(12)
            expires_at = _temporary_password_expiry()
            updated = await self._auth.reset_password(
                user_id,
                temporary_password,
                temporary_password_expires_at=expires_at,
            )
            await self._audit(
                actor=actor,
                action="user.reset_password",
                target_id=user_id,
                reason=body.reason,
                request_id=request_id,
                result_summary="Temporary password issued; existing sessions revoked.",
            )
            result = ResetPasswordResponse(
                operation_id=f"op-{uuid4().hex}",
                request_id="",
                state="completed",
                user_id=updated.id,
                username=updated.username,
                temporary_password=temporary_password,
                expires_at=expires_at,
            )
            await self._write_json(
                idem_key,
                {
                    "fingerprint": self._fingerprint(fingerprint_payload),
                    "state": "completed",
                    "operation_id": result.operation_id,
                    "user_id": result.user_id,
                },
            )
            return result

    async def overview(self) -> dict[str, Any]:
        accounts = [
            item
            for item in await self._auth.list_accounts()
            if item.status != "deleted"
        ]
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
            "account_count": len(accounts),
            "admin_count": sum(item.role == "admin" for item in accounts),
            "updated_at": system["updated_at"],
        }

    async def policy(self) -> AdminPolicyView:
        return AdminPolicyView(
            admin_api_requires_admin_role=True,
            credential_management="admin_only",
            sales_hub_authentication="customer_bearer_token",
            recharge_legacy_hmac_enabled=(
                os.getenv("LONGXIN_ALLOW_LEGACY_HMAC_CODES", "false").lower() == "true"
            ),
            high_risk_plugin_installation="disabled",
            policy_mutation_from_chat=False,
            request_id_enforced=True,
            generated_at=_now(),
        )

    async def revoke_user_sessions(
        self,
        user_id: str,
        actor: AuthUser,
        *,
        request_id: str,
        idempotency_key: str,
    ) -> None:
        request_fingerprint = {"operation": "user.revoke_sessions", "user_id": user_id}
        idem_key = self._idempotency_key(f"revoke-sessions:{actor.id}", idempotency_key)
        async with self._mutation_lock():
            existing = await self._read_idempotent(idem_key, request_fingerprint)
            if existing is not None:
                return
            account = await self._auth._account_by_id(user_id)
            if account is None:
                raise _error("user_not_found", "User not found.", 404)
            await self._auth.revoke_sessions(user_id)
            await self._audit(
                actor=actor,
                action="user.revoke_sessions",
                target_id=user_id,
                reason="Administrator session revocation.",
                request_id=request_id,
            )
            await self._write_idempotent(
                idem_key,
                request_fingerprint,
                {"state": "completed", "user_id": user_id},
            )

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
        idempotency_key: str | None = None,
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
            idempotency_key=idempotency_key,
            source=source,
            created_at=_now(),
        )
        await self._client().rpush(_LEDGER_KEY, entry.model_dump_json())
        return entry

    async def ledger(
        self,
        limit: int,
        *,
        entry_type: str | None = None,
        related_user_id: str | None = None,
        order_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[LedgerEntryView]:
        raw_entries = await self._client().lrange(_LEDGER_KEY, 0, -1)
        result: list[LedgerEntryView] = []
        for raw in reversed(raw_entries):
            try:
                entry = LedgerEntryView.model_validate_json(raw)
            except ValueError:
                continue
            if entry_type and entry.type != entry_type:
                continue
            if related_user_id and entry.related_user_id != related_user_id:
                continue
            if order_id and entry.order_id != order_id:
                continue
            if since and entry.created_at < since:
                continue
            if until and entry.created_at > until:
                continue
            result.append(entry)
            if len(result) >= limit:
                break
        return result

    async def _cumulative_consumed(self) -> int:
        total = 0
        for account in await self._auth.list_accounts():
            if account.role != "user" or account.status == "deleted":
                continue
            usage = await self._auth.get_token_usage(account.id)
            total += max(0, int(usage.total_tokens))
        return total

    async def audit_events(self, limit: int) -> list[AuditEventView]:
        raw_events = await self._client().lrange(_AUDIT_KEY, -limit, -1)
        result: list[AuditEventView] = []
        for raw in reversed(raw_events):
            try:
                result.append(AuditEventView.model_validate_json(raw))
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

    @staticmethod
    def _normalize_recharge_poll_response(
        remote: dict[str, Any],
        system_id: str,
    ) -> dict[str, Any]:
        """Normalize the legacy single-order poll response locally.

        The contract response is ``{"orders": [...], "request_id": ...}``.
        Some Sales Hub deployments currently return one approved order at the
        top level and call the code field ``code``.  Keep the contract shape as
        the canonical form, but adapt that legacy response before validation so
        the rest of the redemption and ACK flow remains unchanged.
        """
        request_id = remote.get("request_id")
        raw_orders = remote.get("orders")
        if isinstance(raw_orders, list):
            orders: list[Any] = []
            for raw_order in raw_orders:
                if not isinstance(raw_order, dict):
                    orders.append(raw_order)
                    continue
                order = dict(raw_order)
                if "recharge_code" not in order and "code" in order:
                    order["recharge_code"] = order["code"]
                order.setdefault("system_id", system_id)
                orders.append(order)
            return {"orders": orders, "request_id": request_id}

        if isinstance(remote.get("order_id"), str):
            order = dict(remote)
            if "recharge_code" not in order and "code" in order:
                order["recharge_code"] = order["code"]
            order.setdefault("system_id", system_id)
            order.pop("request_id", None)
            return {"orders": [order], "request_id": request_id}

        return remote

    async def create_recharge_request(
        self,
        body: RechargeRequest,
        *,
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> RechargeRequestView:
        if not idempotency_key:
            raise _error("idempotency_required", "Idempotency-Key is required.", 400)
        effective_request_id = request_id or f"req-{uuid4().hex}"
        amount = _format_money(body.amount, require_positive=True)
        request_fingerprint = {
            "operation": "recharge_submit",
            "amount": amount,
            "note": body.note,
        }
        idem_key = self._idempotency_key("recharge-submit", idempotency_key)
        requested_at = _now()
        async with self._mutation_lock():
            existing = await self._read_idempotent(idem_key, request_fingerprint)
            if existing:
                return RechargeRequestView.model_validate(existing)
            system = await self._system()
            json_body: dict[str, Any] = {
                "system_id": system["system_id"],
                "amount": amount,
                "requested_at": requested_at,
            }
            if body.note is not None:
                json_body["note"] = body.note
            remote = await self._hub_request(
                "POST",
                "/api/v1/integration/recharge-requests",
                request_id=effective_request_id,
                idempotency_key=idempotency_key,
                json_body=json_body,
            )
            order_id = remote.get("order_id")
            if not isinstance(order_id, str) or not order_id:
                raise _error("hub_invalid_response", "Sales Hub did not return an order ID.", 502)
            remote_request_id = remote.get("request_id")
            if not isinstance(remote_request_id, str) or not remote_request_id:
                raise _error(
                    "hub_invalid_response",
                    "Sales Hub did not return the request ID.",
                    502,
                )
            if remote_request_id != effective_request_id:
                raise _error(
                    "request_id_mismatch",
                    "Sales Hub returned a different request ID.",
                    409,
                )
            remote_system_id = remote.get("system_id", system["system_id"])
            if remote_system_id != system["system_id"]:
                raise _error("system_id_mismatch", "Sales Hub returned another system ID.", 409)
            remote_status = remote.get("status")
            if remote_status not in {"pending", "approved", "rejected", "unknown"}:
                raise _error(
                    "hub_invalid_response",
                    "Sales Hub returned an invalid order status.",
                    502,
                )
            delivery_status = remote.get("delivery_status")
            if delivery_status not in {"not_delivered", "delivered"}:
                raise _error(
                    "hub_invalid_response",
                    "Sales Hub returned an invalid delivery status.",
                    502,
                )
            order = RechargeRequestView(
                order_id=order_id,
                system_id=system["system_id"],
                amount=amount,
                status=remote_status,
                delivery_status=delivery_status,
                created_at=_now(),
                request_id=remote_request_id,
            )
            await self._write_json(self._order_key(order.order_id), order.model_dump())
            await self._client().rpush(f"{_PREFIX}:orders", order.order_id)
            await self._write_idempotent(idem_key, request_fingerprint, order.model_dump())
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
        system_id = (await self._system())["system_id"]
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
        try:
            poll = RechargePollResponse.model_validate(
                self._normalize_recharge_poll_response(remote, system_id),
            )
        except ValidationError as exc:
            normalized = self._normalize_recharge_poll_response(remote, system_id)
            details: dict[str, Any] = {
                "response_keys": sorted(remote.keys()),
                "normalized_keys": sorted(normalized.keys()),
                "orders_type": type(normalized.get("orders")).__name__,
                "order_item_keys": [
                    sorted(item.keys())
                    for item in normalized.get("orders", [])[:5]
                    if isinstance(item, dict)
                ],
                "validation_errors": [
                    {
                        "location": ".".join(str(part) for part in error.get("loc", ())),
                        "type": error.get("type", "validation_error"),
                        "message": error.get("msg", "Invalid value."),
                    }
                    for error in exc.errors()
                ],
            }
            return OperationResponse(
                operation_id=operation_id,
                request_id=request_id,
                state="failed",
                error={
                    "code": "hub_invalid_response",
                    "message": "Sales Hub returned an invalid recharge poll response.",
                    "details": details,
                },
            )
        if poll.request_id != request_id:
            return OperationResponse(
                operation_id=operation_id,
                request_id=request_id,
                state="failed",
                error={
                    "code": "request_id_mismatch",
                    "message": "Sales Hub did not echo the poll request ID.",
                },
            )
        completed: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for item in poll.orders:
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
            if local is not None:
                try:
                    local_amount = _format_money(str(local.get("amount")), require_positive=True)
                except HTTPException:
                    failures.append(
                        {
                            "order_id": item.order_id,
                            "code": "invalid_local_amount",
                            "message": "The local order amount is invalid.",
                        },
                    )
                    continue
                if local_amount != item.amount:
                    failures.append(
                        {
                            "order_id": item.order_id,
                            "code": "amount_mismatch",
                        "message": "The Sales Hub amount does not match the local order.",
                        },
                    )
                    continue
            if local is None:
                failures.append(
                    {
                        "order_id": item.order_id,
                        "code": "recharge_order_not_found",
                        "message": "The Sales Hub order does not match a local recharge request.",
                    },
                )
                continue
            ledger_id = local.get("ledger_id")
            redemption_operation_id = local.get("redemption_operation_id")
            if not isinstance(ledger_id, str) or not isinstance(redemption_operation_id, str):
                try:
                    redeemed = await self.redeem_code(
                        RedeemCodeRequest(code=item.recharge_code, confirm=True),
                        actor,
                        request_id=request_id,
                        idempotency_key=f"{idempotency_key or operation_id}:redeem:{item.order_id}",
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
                ack_remote = await self._hub_request(
                    "POST",
                    f"/api/v1/integration/recharge-requests/{item.order_id}/ack",
                    request_id=request_id,
                    idempotency_key=f"{idempotency_key or operation_id}:{item.order_id}",
                    json_body=RechargeAckRequest(
                        operation_id=operation_id,
                        system_id=system_id,
                        redemption_operation_id=redemption_operation_id,
                        ledger_id=ledger_id,
                    ).model_dump(),
                )
                ack = RechargeAckResponse.model_validate(ack_remote)
                if ack.order_id != item.order_id or ack.request_id != request_id:
                    raise _error(
                        "ack_invalid_response",
                        "Sales Hub returned an ACK for another request or order.",
                        502,
                    )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {
                    "code": "ack_failed",
                    "message": str(exc.detail),
                }
                failures.append({"order_id": item.order_id, **detail})
                continue
            except ValueError:
                failures.append(
                    {
                        "order_id": item.order_id,
                        "code": "ack_invalid_response",
                        "message": "Sales Hub returned an invalid ACK response.",
                    },
                )
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
        cumulative_consumed = await self._cumulative_consumed()
        report = UsageReportRequest(
            system_id=system["system_id"],
            pool_tokens=int(system["pool_tokens"]),
            total_recharged=_format_money(str(system["total_recharged"])),
            app_version=os.getenv("LONGXIN_APP_VERSION", "3.8.1"),
            cumulative_consumed=cumulative_consumed,
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
        try:
            response = UsageReportResponse.model_validate(remote)
        except ValueError:
            return OperationResponse(
                operation_id=operation_id,
                request_id=request_id,
                state="failed",
                error={
                    "code": "hub_invalid_response",
                    "message": "Sales Hub returned an invalid usage report response.",
                },
            )
        if response.system_id != report.system_id:
            return OperationResponse(
                operation_id=operation_id,
                request_id=request_id,
                state="failed",
                error={
                    "code": "system_id_mismatch",
                    "message": "Sales Hub returned another system ID for the usage report.",
                },
            )
        if response.request_id != request_id:
            return OperationResponse(
                operation_id=operation_id,
                request_id=request_id,
                state="failed",
                error={
                    "code": "request_id_mismatch",
                    "message": "Sales Hub did not echo the usage report request ID.",
                },
            )
        if not response.accepted:
            return OperationResponse(
                operation_id=operation_id,
                request_id=request_id,
                state="failed",
                error={
                    "code": "usage_report_rejected",
                    "message": "Sales Hub rejected the usage report.",
                },
            )
        system["cumulative_consumed"] = cumulative_consumed
        system["last_report_at"] = response.reported_at
        await self._save_system(system)
        return OperationResponse(
            operation_id=operation_id,
            request_id=request_id,
            state="completed",
            result={
                "report_id": response.report_id,
                "system_id": report.system_id,
                "accepted": response.accepted,
                "reported_at": response.reported_at,
                "sales_request_id": response.request_id,
                "cumulative_consumed": report.cumulative_consumed,
                "cumulative_credits": report.cumulative_credits,
                "cumulative_consumed_delta": response.cumulative_consumed_delta,
                "cumulative_credits_delta": response.cumulative_credits_delta,
            },
        )

    async def redeem_code(
        self,
        body: RedeemCodeRequest,
        actor: AuthUser,
        *,
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not body.confirm:
            raise _error("confirmation_required", "Explicit confirmation is required.", 400)
        if not idempotency_key:
            raise _error("idempotency_required", "Idempotency-Key is required.", 400)
        request_fingerprint = {
            "operation": "redeem_code",
            "code_sha256": hashlib.sha256(body.code.encode("utf-8")).hexdigest(),
            "confirm": body.confirm,
        }
        idem_key = self._idempotency_key("redeem-code", idempotency_key)
        existing = await self._read_idempotent(idem_key, request_fingerprint)
        if existing is not None:
            return existing
        parts = body.code.split(".", 2)
        if len(parts) != 3 or parts[0] != "LXRC2":
            raise _error("invalid_recharge_code", "Unsupported recharge code format.", 400)
        try:
            payload_bytes = _decode_base64url(parts[1])
            payload = json.loads(payload_bytes)
        except (ValueError, json.JSONDecodeError) as exc:
            raise _error("invalid_recharge_code", "The recharge code payload is invalid.", 400) from exc
        if not isinstance(payload, dict):
            raise _error("invalid_recharge_code", "The recharge code payload is invalid.", 400)
        config = await self._read_json(_SALES_HUB_KEY) or {}
        public_key = config.get("public_key")
        if public_key:
            try:
                signer = _load_ed25519_public_key(public_key)
                signature = _decode_base64url(parts[2])
                signer.verify(signature, payload_bytes)
            except Exception as exc:
                raise _error(
                    "invalid_recharge_signature",
                    "The recharge code signature is invalid.",
                    400,
                ) from exc
        else:
            # Legacy HMAC is retained only for explicitly marked compatibility
            # environments. Production requires the Sales Hub Ed25519 key.
            allow_legacy_hmac = os.getenv(
                "LONGXIN_ALLOW_LEGACY_HMAC_CODES",
                "false",
            ).lower() == "true"
            secret = os.getenv("LONGXIN_RECHARGE_CODE_SECRET")
            if not allow_legacy_hmac or not secret:
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
        version = payload.get("version")
        amount = payload.get("amount")
        nonce = payload.get("nonce")
        order_id = payload.get("order_id")
        if (
            not isinstance(tokens, int)
            or isinstance(tokens, bool)
            or tokens <= 0
            or version != os.getenv("LONGXIN_RECHARGE_CODE_VERSION", "1")
            or not isinstance(amount, str)
            or not isinstance(nonce, str)
            or not nonce
            or not isinstance(order_id, str)
            or not order_id
        ):
            raise _error("invalid_recharge_code", "The recharge code fields are invalid.", 400)
        try:
            canonical_amount = _format_money(amount, require_positive=True)
            amount_value = Decimal(canonical_amount)
        except HTTPException as exc:
            raise _error("invalid_recharge_code", "The recharge amount is invalid.", 400)
        issued_at = _parse_utc_timestamp(payload.get("issued_at"), "issued_at")
        expires_at = _parse_utc_timestamp(payload.get("expires_at"), "expires_at")
        now = datetime.now(timezone.utc)
        if expires_at <= now or expires_at <= issued_at or issued_at > now + timedelta(minutes=5):
            raise _error("invalid_recharge_code", "The recharge code validity window is invalid.", 400)
        if canonical_amount != amount:
            raise _error("invalid_recharge_code", "The recharge amount is invalid.", 400)
        order = await self._read_json(self._order_key(order_id))
        if order is None:
            order = self._sales_code_order(
                order_id=order_id,
                system_id=system["system_id"],
                amount=amount,
                tokens=tokens,
                nonce=nonce,
                issued_at=issued_at,
                request_id=request_id,
            )
        if order.get("system_id") != system["system_id"]:
            raise _error("system_id_mismatch", "The recharge order targets another system.", 409)
        if order.get("delivery_status") == "delivered":
            raise _error("code_already_redeemed", "The recharge order was already delivered.", 409)
        try:
            local_amount = _format_money(str(order.get("amount")), require_positive=True)
        except HTTPException as exc:
            raise _error(
                "amount_mismatch",
                "The local recharge order amount is invalid.",
                409,
            ) from exc
        if local_amount != amount:
            raise _error("amount_mismatch", "The recharge code amount does not match the order.", 409)
        operation_id = f"op-{uuid4().hex}"
        async with self._mutation_lock():
            existing = await self._read_idempotent(idem_key, request_fingerprint)
            if existing is not None:
                return existing
            current_order = await self._read_json(self._order_key(order_id))
            if current_order is None:
                current_order = self._sales_code_order(
                    order_id=order_id,
                    system_id=system["system_id"],
                    amount=amount,
                    tokens=tokens,
                    nonce=nonce,
                    issued_at=issued_at,
                    request_id=request_id,
                )
                external_order = True
            else:
                external_order = False
            if current_order.get("delivery_status") == "delivered" or current_order.get(
                "redemption_operation_id",
            ):
                raise _error("code_already_redeemed", "The recharge order was already redeemed.", 409)
            order = current_order
            nonce_key = f"{_PREFIX}:recharge-nonce:{nonce}"
            created = await self._client().set(nonce_key, "1", nx=True)
            if not created:
                raise _error("code_already_redeemed", "The recharge code was already redeemed.", 409)
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
                idempotency_key=idempotency_key,
            )
            order.update(
                {
                    "status": "approved",
                    "delivery_status": "delivered" if external_order else "not_delivered",
                    "ledger_id": ledger.ledger_id,
                    "redemption_operation_id": operation_id,
                },
            )
            await self._write_json(self._order_key(order_id), order)
            if external_order:
                await self._client().rpush(f"{_PREFIX}:orders", order_id)
            result = {
                "operation_id": operation_id,
                "state": "completed",
                "system_id": system["system_id"],
                "amount": amount,
                "tokens": tokens,
                "ledger_id": ledger.ledger_id,
                "pool_tokens_after": int(system["pool_tokens"]),
                "request_id": request_id,
            }
            await self._write_idempotent(idem_key, request_fingerprint, result)
            return result

    async def hub_config(self) -> dict[str, Any]:
        value = await self._hub_connection_config()
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

    async def update_hub_config(
        self,
        body: SalesHubConfigUpdate,
        actor: AuthUser,
        *,
        request_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request_fingerprint = {"operation": "sales_hub.update_config", **body.model_dump()}
        idem_key = self._idempotency_key(f"sales-hub-config:{actor.id}", idempotency_key)
        async with self._mutation_lock():
            existing = await self._read_idempotent(idem_key, request_fingerprint)
            if existing is not None:
                return existing
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
                value["token"] = _seal_secret(body.token)
            if body.public_key is not None:
                if body.public_key:
                    _load_ed25519_public_key(body.public_key)
                value["public_key"] = body.public_key
            await self._write_json(_SALES_HUB_KEY, value)
            result = await self.hub_config()
            await self._audit(
                actor=actor,
                action="sales_hub.update_config",
                target_id=None,
                reason="Administrator updated Sales Hub configuration.",
                request_id=request_id,
            )
            await self._write_idempotent(idem_key, request_fingerprint, result)
            return result

    async def verify_hub(
        self,
        request_id: str,
        *,
        idempotency_key: str,
    ) -> OperationResponse:
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
                idempotency_key=idempotency_key,
                json_body={},
            )
            if result.get("request_id") != request_id:
                value["outbound_status"] = "unknown"
                value["inbound_status"] = "unknown"
                await self._write_json(_SALES_HUB_KEY, value)
                return OperationResponse(
                    operation_id=operation_id,
                    request_id=request_id,
                    state="failed",
                    error={
                        "code": "request_id_mismatch",
                        "message": "Sales Hub did not echo the verification request ID.",
                    },
                )
            outbound = result.get("outbound") if isinstance(result, dict) else None
            if isinstance(outbound, dict):
                outbound_ok = bool(outbound.get("ok", False))
            else:
                outbound_ok = outbound is True
            ping = result.get("ping") if isinstance(result, dict) else None
            ping_ok = (
                isinstance(ping, dict)
                and ping.get("ok") is True
                and ping.get("system_id") == value.get("system_id", (await self._system())["system_id"])
            )
            inbound = result.get("inbound") if isinstance(result, dict) else None
            if isinstance(inbound, bool):
                # The current Sales Hub returns a compact boolean, while the
                # contract-compatible shape is {"ok": true}.
                inbound_ok = inbound
            elif isinstance(inbound, dict):
                inbound_ok = bool(inbound.get("ok", False))
            else:
                inbound_ok = False
            value["outbound_status"] = "ok" if outbound_ok and ping_ok else "failed"
            value["inbound_status"] = "ok" if inbound_ok else "failed"
            value["last_verified_at"] = _now()
            await self._write_json(_SALES_HUB_KEY, value)
            if not outbound_ok or not ping_ok:
                return OperationResponse(
                    operation_id=operation_id,
                    request_id=request_id,
                    state="failed",
                    result=result,
                    error={
                        "code": "outbound_verification_failed",
                        "message": "Sales Hub did not confirm the configured system identity.",
                    },
                )
            if not inbound_ok:
                return OperationResponse(
                    operation_id=operation_id,
                    request_id=request_id,
                    state="failed",
                    result=result,
                    error={
                        "code": "inbound_verification_failed",
                        "message": "Sales Hub could not complete the reverse connection check.",
                    },
                )
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

    async def reset_admin_password_from_hub(
        self,
        body: RemotePasswordResetRequest,
        *,
        request_id: str,
        idempotency_key: str,
    ) -> RemotePasswordResetResponse:
        expires_at = body.expires_at
        if expires_at is not None:
            try:
                parsed_expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise _error(
                    "invalid_expiry",
                    "expires_at must be an ISO-8601 timestamp.",
                    422,
                ) from exc
            if parsed_expires_at.tzinfo is None or parsed_expires_at <= datetime.now(timezone.utc):
                raise _error(
                    "invalid_expiry",
                    "expires_at must be in the future and include a timezone.",
                    422,
                )
            expires_at = parsed_expires_at.astimezone(timezone.utc).isoformat()
        fingerprint_payload = {
            "operation": "remote_admin_password_reset",
            "operation_id": body.operation_id,
            "username": body.username,
            "new_password_sha256": hashlib.sha256(body.new_password.encode("utf-8")).hexdigest(),
            "expires_at": expires_at,
        }
        idem_key = self._idempotency_key("remote-password-reset", idempotency_key)
        operation_key = self._idempotency_key("remote-password-reset-operation", body.operation_id)
        async with self._mutation_lock():
            previous = await self._read_idempotent(idem_key, fingerprint_payload)
            if previous is not None:
                return RemotePasswordResetResponse.model_validate(previous)
            if await self._read_json(operation_key) is not None:
                raise _error(
                    "operation_already_processed",
                    "The remote password reset operation was already processed.",
                    409,
                )
            account = await self._auth._account_by_username(body.username)
            if account is None or account.role != "admin":
                raise _error("admin_not_found", "The target administrator was not found.", 404)
            await self._auth.reset_password(
                account.user_id,
                body.new_password,
                temporary_password_expires_at=expires_at,
            )
            result = RemotePasswordResetResponse(
                ok=True,
                operation_id=body.operation_id,
                username=account.username,
                request_id=request_id,
            )
            await self._write_idempotent(idem_key, fingerprint_payload, result.model_dump())
            await self._write_json(
                operation_key,
                {
                    "fingerprint": self._fingerprint(fingerprint_payload),
                    "result": result.model_dump(),
                },
            )
            await self._audit(
                actor=AuthUser(
                    id="sales-hub",
                    username="sales-hub",
                    role="admin",
                ),
                action="sales_hub.reset_admin_password",
                target_id=account.user_id,
                reason="Remote Sales Hub password reset command.",
                request_id=request_id,
                result_summary="Administrator password reset; existing sessions revoked.",
                actor_type="system",
            )
            return result

    async def _audit_target(self, target_user_id: str) -> AuthUser:
        account = await self._auth._account_by_id(target_user_id)
        if account is None or account.status == "deleted":
            raise _error("user_not_found", "The audit target user was not found.", 404)
        return self._auth._public_user(account)

    async def audit_overview(
        self,
        actor: AuthUser,
        body: AuditOverviewRequest,
        *,
        request_id: str,
    ) -> AuditResourceResponse:
        target = await self._audit_target(body.target_user_id)
        storage = self._storage
        agents = await storage.list_agents(target.id)
        agent_views: list[dict[str, Any]] = []
        session_views: list[dict[str, Any]] = []
        for agent in agents:
            agent_views.append(
                {
                    "id": agent.id,
                    "name": getattr(agent, "name", agent.id),
                    "created_at": agent.created_at.isoformat(),
                    "updated_at": agent.updated_at.isoformat(),
                },
            )
            for session in await storage.list_sessions(target.id, agent.id):
                session_views.append(
                    {
                        "id": session.id,
                        "agent_id": agent.id,
                        "name": session.config.name,
                        "created_at": session.created_at.isoformat(),
                        "updated_at": session.updated_at.isoformat(),
                        "origin": session.origin.type,
                    },
                )
        knowledge_bases = await storage.list_knowledge_bases(target.id)
        knowledge_base_views: list[dict[str, Any]] = []
        document_views: list[dict[str, Any]] = []
        for knowledge_base in knowledge_bases:
            knowledge_base_views.append(
                {
                    "id": knowledge_base.id,
                    "name": getattr(knowledge_base, "name", knowledge_base.id),
                    "created_at": knowledge_base.created_at.isoformat(),
                    "updated_at": knowledge_base.updated_at.isoformat(),
                },
            )
            for document in await storage.list_knowledge_documents(target.id, knowledge_base.id):
                document_views.append(
                    {
                        "id": document.id,
                        "knowledge_base_id": knowledge_base.id,
                        "filename": document.data.filename,
                        "size": document.data.size,
                        "status": document.status,
                        "chunk_count": document.data.chunk_count,
                        "created_at": document.created_at.isoformat(),
                        "updated_at": document.updated_at.isoformat(),
                    },
                )
        await self._audit(
            actor=actor,
            action="audit.overview",
            target_id=target.id,
            reason=body.reason,
            request_id=request_id,
            resource_type="user",
            resource_id=target.id,
            result_summary="Audit overview granted; resource bodies were not returned.",
        )
        return AuditResourceResponse(
            target_user_id=target.id,
            resource_type="overview",
            data={
                "user": {
                    "id": target.id,
                    "username": target.username,
                    "role": target.role,
                    "status": target.status,
                },
                "agents": agent_views,
                "sessions": session_views,
                "knowledge_bases": knowledge_base_views,
                "documents": document_views,
            },
            request_id=request_id,
        )

    async def _require_audit_overview(
        self,
        actor: AuthUser,
        event_id: str,
        target_user_id: str,
        reason: str,
        request_id: str,
    ) -> None:
        raw_events = await self._client().lrange(_AUDIT_KEY, 0, -1)
        for raw in reversed(raw_events):
            try:
                event = AuditEventView.model_validate_json(raw)
            except ValueError:
                continue
            if (
                event.event_id == event_id
                and event.action == "audit.overview"
                and event.target_user_id == target_user_id
                and event.status == "completed"
            ):
                await self._audit(
                    actor=actor,
                    action="audit.resource_access",
                    target_id=target_user_id,
                    reason=reason,
                    request_id=request_id,
                    resource_type="audit",
                    resource_id=event_id,
                    result_summary="Second-step audit access granted.",
                )
                return
        raise _error(
            "audit_overview_required",
            "A completed audit overview is required before accessing a resource.",
            403,
        )

    async def audit_session(
        self,
        actor: AuthUser,
        session_id: str,
        body: AuditSessionAccessRequest,
        *,
        request_id: str,
    ) -> AuditResourceResponse:
        target = await self._audit_target_from_event(body.overview_event_id)
        await self._require_audit_overview(
            actor,
            body.overview_event_id,
            target,
            body.reason,
            request_id,
        )
        session = await self._storage.get_session(target, body.agent_id, session_id)
        if session is None:
            raise _error("resource_not_found", "The requested session was not found.", 404)
        messages, has_more = await self._storage.list_messages(
            target,
            session_id,
            limit=100,
        )
        return AuditResourceResponse(
            target_user_id=target,
            resource_type="session",
            resource_id=session_id,
            data={
                "session": {
                    "id": session.id,
                    "agent_id": session.agent_id,
                    "name": session.config.name,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                },
                "messages": [
                    message.model_dump(mode="json")
                    if hasattr(message, "model_dump")
                    else message
                    for message in messages
                ],
                "has_more": has_more,
            },
            request_id=request_id,
        )

    async def _audit_target_from_event(self, event_id: str) -> str:
        raw_events = await self._client().lrange(_AUDIT_KEY, 0, -1)
        for raw in reversed(raw_events):
            try:
                event = AuditEventView.model_validate_json(raw)
            except ValueError:
                continue
            if event.event_id == event_id and event.action == "audit.overview":
                if event.target_user_id:
                    return event.target_user_id
                break
        raise _error(
            "audit_overview_required",
            "A completed audit overview is required before accessing a resource.",
            403,
        )

    async def audit_document(
        self,
        actor: AuthUser,
        document_id: str,
        body: AuditDocumentAccessRequest,
        *,
        request_id: str,
    ) -> AuditResourceResponse:
        target = await self._audit_target_from_event(body.overview_event_id)
        await self._require_audit_overview(
            actor,
            body.overview_event_id,
            target,
            body.reason,
            request_id,
        )
        document = await self._storage.get_knowledge_document(
            target,
            body.knowledge_base_id,
            document_id,
        )
        if document is None:
            raise _error("resource_not_found", "The requested document was not found.", 404)
        return AuditResourceResponse(
            target_user_id=target,
            resource_type="document",
            resource_id=document_id,
            data={
                "document": {
                    "id": document.id,
                    "knowledge_base_id": document.knowledge_base_id,
                    "filename": document.data.filename,
                    "size": document.data.size,
                    "content_type": document.data.content_type,
                    "status": document.status,
                    "chunk_count": document.data.chunk_count,
                    "error": document.data.error,
                    "created_at": document.created_at.isoformat(),
                    "updated_at": document.updated_at.isoformat(),
                },
            },
            request_id=request_id,
        )

    async def _audit(
        self,
        *,
        actor: AuthUser,
        action: str,
        target_id: str | None,
        reason: str,
        request_id: str = "",
        status_value: Literal["completed", "failed"] = "completed",
        result_summary: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        actor_type: Literal["admin", "user", "system"] = "admin",
    ) -> None:
        target_name = None
        if target_id:
            account = await self._auth._account_by_id(target_id)
            target_name = account.username if account is not None else None
        event = {
            "event_id": f"evt-{uuid4().hex}",
            "actor_type": actor_type,
            "actor_id": actor.id,
            "actor_name": actor.username,
            "target_user_id": target_id,
            "target_user_name": target_name,
            "action": action,
            "resource_type": resource_type or action.split(".", 1)[0],
            "resource_id": resource_id or target_id,
            "reason": reason,
            "request_id": request_id,
            "status": status_value,
            "result_summary": result_summary,
            "created_at": _now(),
        }
        await self._client().rpush(_AUDIT_KEY, json.dumps(event, ensure_ascii=False))

    async def authorize_hub(self, authorization: str | None) -> None:
        scheme, _, token = (authorization or "").partition(" ")
        value = await self._hub_connection_config()
        stored = value.get("token")
        if (
            scheme.lower() != "bearer"
            or not token
            or not stored
            or not hmac.compare_digest(token, stored)
        ):
            raise _error("invalid_customer_token", "The Sales Hub token is invalid.", 401)


async def _auth_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AuthUser:
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
resource_router = APIRouter(prefix="/resources", tags=["resources"])


@resource_router.get(
    "/published",
    response_model=PublishedResourceListResponse,
)
async def list_published_resources(
    kind: ResourceKind | None = Query(default=None),
    user: AuthUser = Depends(_auth_user),
    service: AdminService = Depends(get_admin_service),
) -> PublishedResourceListResponse:
    return await service.published_resources(user, kind)


@admin_router.get(
    "/resources",
    response_model=ResourcePublicationListResponse,
)
async def list_resource_publications(
    kind: ResourceKind | None = Query(default=None),
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> ResourcePublicationListResponse:
    return await service.list_resource_publications(kind)


@admin_router.post(
    "/resources",
    response_model=ResourcePublicationView,
)
async def publish_resource(
    body: ResourcePublicationRequest,
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> ResourcePublicationView:
    return await service.publish_resource(body, actor)


@admin_router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_installed_skill(
    skill_id: str,
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> None:
    await service.remove_installed_skill(skill_id, actor)


@admin_router.delete("/mcps/{mcp_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_installed_mcp(
    mcp_id: str,
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> None:
    await service.remove_installed_mcp(mcp_id, actor)


@admin_router.get("/overview")
async def get_overview(
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> dict[str, Any]:
    return await service.overview()


@admin_router.get("/policy", response_model=AdminPolicyView)
async def get_admin_policy(
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> AdminPolicyView:
    return await service.policy()


@admin_router.get("/users", response_model=UserListResponse)
async def list_users(
    request: Request,
    keyword: str | None = Query(default=None, max_length=64),
    account_status: str | None = Query(default=None, alias="status"),
    plan_id: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> UserListResponse:
    response = await service.list_users(
        keyword=keyword,
        account_status=account_status,
        plan_id=plan_id,
        page=page,
        page_size=page_size,
    )
    response.request_id = service._request_id(request)
    return response


@admin_router.post(
    "/users",
    response_model=AdminUserView,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    body: CreateUserRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> AdminUserView:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    return await service.create_user(
        body,
        actor,
        request_id=service._request_id(request),
        idempotency_key=idempotency_key,
    )


@admin_router.patch("/users/{user_id}", response_model=AdminUserView)
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> AdminUserView:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    return await service.update_user(
        user_id,
        body,
        actor,
        request_id=service._request_id(request),
        idempotency_key=idempotency_key,
    )


@admin_router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    body: DeleteUserRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> None:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    await service.delete_user(
        user_id,
        actor,
        body,
        request_id=service._request_id(request),
        idempotency_key=idempotency_key,
    )


@admin_router.post(
    "/users/{user_id}/reset-password",
    response_model=ResetPasswordResponse,
)
async def reset_password(
    user_id: str,
    body: ResetPasswordRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> ResetPasswordResponse:
    request_id = service._request_id(request)
    response = await service.reset_password(
        user_id,
        actor,
        body,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    response.request_id = request_id
    return response


@admin_router.delete("/users/{user_id}/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_sessions(
    user_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> None:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    await service.revoke_user_sessions(
        user_id,
        actor,
        request_id=service._request_id(request),
        idempotency_key=idempotency_key,
    )


@admin_router.get("/quota", response_model=SystemAccountView)
async def get_quota(
    request: Request,
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> SystemAccountView:
    value = await service.quota()
    return SystemAccountView(**value, request_id=service._request_id(request))


@admin_router.get("/quota/ledger", response_model=LedgerListResponse)
async def get_ledger(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    entry_type: str | None = Query(default=None, alias="type", max_length=64),
    related_user_id: str | None = Query(default=None, max_length=128),
    order_id: str | None = Query(default=None, max_length=128),
    since: str | None = Query(default=None, max_length=64),
    until: str | None = Query(default=None, max_length=64),
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> LedgerListResponse:
    entries = await service.ledger(
        limit,
        entry_type=entry_type,
        related_user_id=related_user_id,
        order_id=order_id,
        since=since,
        until=until,
    )
    return LedgerListResponse(
        entries=entries,
        total=len(entries),
        request_id=service._request_id(request),
    )


@admin_router.get("/audit/events", response_model=AuditEventListResponse)
async def get_audit_events(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> AuditEventListResponse:
    events = await service.audit_events(limit)
    return AuditEventListResponse(
        events=events,
        total=len(events),
        request_id=service._request_id(request),
    )


@admin_router.post("/audit/overview", response_model=AuditResourceResponse)
async def audit_overview(
    body: AuditOverviewRequest,
    request: Request,
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> AuditResourceResponse:
    return await service.audit_overview(
        actor,
        body,
        request_id=service._request_id(request),
    )


@admin_router.post(
    "/audit/sessions/{session_id}",
    response_model=AuditResourceResponse,
)
async def audit_session(
    session_id: str,
    body: AuditSessionAccessRequest,
    request: Request,
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> AuditResourceResponse:
    return await service.audit_session(
        actor,
        session_id,
        body,
        request_id=service._request_id(request),
    )


@admin_router.post(
    "/audit/documents/{document_id}",
    response_model=AuditResourceResponse,
)
async def audit_document(
    document_id: str,
    body: AuditDocumentAccessRequest,
    request: Request,
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> AuditResourceResponse:
    return await service.audit_document(
        actor,
        document_id,
        body,
        request_id=service._request_id(request),
    )


@admin_router.post("/quota/redeem-code")
async def redeem_code(
    body: RedeemCodeRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> dict[str, Any]:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    return await service.redeem_code(
        body,
        actor,
        request_id=service._request_id(request),
        idempotency_key=idempotency_key,
    )


@admin_router.post("/quota/recharge-requests", response_model=RechargeRequestView)
async def create_recharge_request(
    body: RechargeRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> RechargeRequestView:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    return await service.create_recharge_request(
        body,
        request_id=service._request_id(request),
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
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    return await service.sync_recharge(
        actor,
        request_id=service._request_id(request),
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
        request_id=service._request_id(request),
    )


@admin_router.get("/sales-hub/config", response_model=SalesHubConfigView)
async def get_sales_hub_config(
    request: Request,
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> SalesHubConfigView:
    return SalesHubConfigView(
        **await service.hub_config(),
        request_id=service._request_id(request),
    )


@admin_router.patch("/sales-hub/config", response_model=SalesHubConfigView)
async def update_sales_hub_config(
    body: SalesHubConfigUpdate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> SalesHubConfigView:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    value = await service.update_hub_config(
        body,
        actor,
        request_id=service._request_id(request),
        idempotency_key=idempotency_key,
    )
    return SalesHubConfigView(
        **value,
        request_id=service._request_id(request),
    )


@admin_router.post("/sales-hub/verify", response_model=OperationResponse)
async def verify_sales_hub(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> OperationResponse:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    request_id = service._request_id(request)
    return await service.verify_hub(request_id, idempotency_key=idempotency_key)


@admin_router.post("/sales-hub/usage-report", response_model=OperationResponse)
async def report_sales_hub_usage(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _: AuthUser = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
) -> OperationResponse:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    return await service.report_usage(
        request_id=service._request_id(request),
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
        request_id=service._request_id(request),
    )


@sales_hub_router.post(
    "/admin-password-resets",
    response_model=RemotePasswordResetResponse,
)
async def sales_hub_reset_admin_password(
    body: RemotePasswordResetRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service: AdminService = Depends(get_admin_service),
) -> RemotePasswordResetResponse:
    await service.authorize_hub(authorization)
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    return await service.reset_admin_password_from_hub(
        body,
        request_id=service._request_id(request),
        idempotency_key=idempotency_key,
    )
