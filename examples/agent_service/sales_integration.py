# -*- coding: utf-8 -*-
"""The decoupled sales-console integration for the Longxin application.

This module is intentionally kept in the deployment example instead of the
AgentScope core package.  The two systems communicate over HTTP only; this
module does not import or access the sales console database.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agentscope import __version__ as agentscope_version


_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
_INTEGRATION_KEY_PREFIX = "longxin:sales-integration:v1"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or f"req-{uuid4().hex}"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_secret(value_name: str, file_name: str) -> str:
    file_path = os.getenv(file_name, "").strip()
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except OSError:
            # The request will report an unconfigured integration instead of
            # exposing a filesystem error or a secret path to the caller.
            return ""
    return os.getenv(value_name, "").strip()


class SalesIntegrationSettings:
    """Environment-backed settings shared by the client and local routes."""

    @property
    def system_name(self) -> str:
        return os.getenv("LONGXIN_SYSTEM_NAME", "Longxin AgentScope").strip()

    @property
    def system_id(self) -> str:
        return os.getenv("LONGXIN_SYSTEM_ID", "local-system").strip()

    @property
    def app_version(self) -> str:
        return os.getenv("LONGXIN_APP_VERSION", "3.8.1").strip()

    @property
    def core_version(self) -> str:
        return os.getenv("LONGXIN_CORE_VERSION", agentscope_version).strip()

    @property
    def sales_center_base_url(self) -> str:
        return os.getenv("SALES_CENTER_BASE_URL", "").strip().rstrip("/")

    @property
    def customer_api_token(self) -> str:
        return _read_secret("SALES_CENTER_API_TOKEN", "SALES_CENTER_API_TOKEN_FILE")

    @property
    def timeout_seconds(self) -> float:
        try:
            return max(1.0, float(os.getenv("SALES_CENTER_TIMEOUT_SECONDS", "8")))
        except ValueError:
            return 8.0

    @property
    def verify_tls(self) -> bool:
        return _env_bool("SALES_CENTER_VERIFY_TLS", True)

    @property
    def upgrade_dir(self) -> Path:
        return Path(os.getenv("LONGXIN_UPGRADE_DIR", "/app/upgrade-packages"))

    @property
    def integration_admin_username(self) -> str:
        return os.getenv(
            "SALES_INTEGRATION_ADMIN_USERNAME",
            os.getenv("AGENTSCOPE_USERNAME", "admin"),
        ).strip()


class SalesHubError(RuntimeError):
    """A safe, non-secret representation of a sales console API failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SalesHubClient:
    """HTTP client for the canonical `/api/v1` sales-console contract."""

    def __init__(
        self,
        settings: SalesIntegrationSettings | None = None,
        *,
        base_url_provider: Callable[[], Awaitable[str]] | None = None,
        token_provider: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.settings = settings or SalesIntegrationSettings()
        self._base_url_provider = base_url_provider
        self._token_provider = token_provider

    async def _url(self, path: str) -> str:
        base_url = (
            await self._base_url_provider()
            if self._base_url_provider is not None
            else self.settings.sales_center_base_url
        )
        if not base_url:
            raise SalesHubError("SALES_CENTER_BASE_URL is not configured")
        return f"{base_url}/{path.lstrip('/')}"

    async def _headers(
        self,
        request_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        token = (
            await self._token_provider()
            if self._token_provider is not None
            else self.settings.customer_api_token
        )
        if not token:
            raise SalesHubError("SALES_CENTER_API_TOKEN is not configured")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "X-Request-ID": request_id,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        request_id: str | None = None,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        resolved_request_id = request_id or f"req-{uuid4().hex}"
        headers = await self._headers(
            resolved_request_id,
            idempotency_key=idempotency_key,
        )
        if body is not None:
            headers["Content-Type"] = "application/json"

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self.settings.timeout_seconds,
                    connect=min(3.0, self.settings.timeout_seconds),
                ),
                verify=self.settings.verify_tls,
            ) as client:
                response = await client.request(
                    method,
                    await self._url(path),
                    headers=headers,
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise SalesHubError("sales console request timed out") from exc
        except httpx.HTTPError as exc:
            raise SalesHubError("sales console is unreachable") from exc

        if response.status_code >= 400:
            message = f"sales console returned HTTP {response.status_code}"
            try:
                payload = response.json()
                detail = payload.get("detail") if isinstance(payload, dict) else None
                if isinstance(detail, dict) and isinstance(detail.get("message"), str):
                    message = detail["message"]
                elif isinstance(detail, str):
                    message = detail
            except (ValueError, TypeError):
                pass
            raise SalesHubError(message, status_code=response.status_code)

        if response.status_code == status.HTTP_204_NO_CONTENT or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise SalesHubError("sales console returned invalid JSON") from exc

    async def submit_recharge_request(
        self,
        *,
        amount: str,
        note: str,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> Any:
        return await self.request(
            "POST",
            "/api/v1/integration/recharge-requests",
            request_id=request_id,
            idempotency_key=idempotency_key,
            body={
                "system_id": self.settings.system_id,
                "amount": amount,
                "note": note,
                "requested_at": _iso_now(),
            },
        )

    async def poll_recharge_requests(
        self,
        *,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/v1/integration/recharge-requests/poll",
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    async def acknowledge_recharge(
        self,
        order_id: str,
        *,
        operation_id: str,
        system_id: str,
        redemption_operation_id: str,
        ledger_id: str,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> Any:
        return await self.request(
            "POST",
            f"/api/v1/integration/recharge-requests/{order_id}/ack",
            request_id=request_id,
            idempotency_key=idempotency_key,
            body={
                "operation_id": operation_id,
                "system_id": system_id,
                "redemption_operation_id": redemption_operation_id,
                "ledger_id": ledger_id,
            },
        )

    async def report_usage(
        self,
        report: dict[str, Any],
        *,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> Any:
        return await self.request(
            "POST",
            "/api/v1/integration/usage-reports",
            request_id=request_id,
            idempotency_key=idempotency_key,
            body=report,
        )

    async def verify_connection(
        self,
        *,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> Any:
        return await self.request(
            "POST",
            "/api/v1/integration/verify-connection",
            request_id=request_id,
            idempotency_key=idempotency_key,
            body={},
        )

    async def latest_release(
        self,
        artifact_type: Literal["app", "core"],
        *,
        request_id: str | None = None,
    ) -> Any:
        return await self.request(
            "GET",
            f"/api/v1/integration/releases/{artifact_type}/latest",
            request_id=request_id,
        )

    async def download_release(
        self,
        artifact_type: Literal["app", "core"],
        destination: Path,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Download a release artifact without loading it into memory."""
        resolved_request_id = request_id or f"req-{uuid4().hex}"
        headers = await self._headers(resolved_request_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.part")
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self.settings.timeout_seconds,
                    connect=min(3.0, self.settings.timeout_seconds),
                ),
                verify=self.settings.verify_tls,
            ) as http_client:
                async with http_client.stream(
                    "GET",
                    await self._url(
                        f"/api/v1/integration/releases/{artifact_type}/latest",
                    ),
                    headers=headers,
                ) as response:
                    if response.status_code >= 400:
                        raise SalesHubError(
                            f"sales console returned HTTP {response.status_code}",
                            status_code=response.status_code,
                        )
                    with temporary.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            output.write(chunk)
            temporary.replace(destination)
        except httpx.TimeoutException as exc:
            temporary.unlink(missing_ok=True)
            raise SalesHubError("sales console request timed out") from exc
        except httpx.HTTPError as exc:
            temporary.unlink(missing_ok=True)
            raise SalesHubError("sales console is unreachable") from exc
        except SalesHubError:
            temporary.unlink(missing_ok=True)
            raise
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise SalesHubError("unable to save the sales console release") from exc
        return {
            "artifact_type": artifact_type,
            "path": str(destination),
            "size_bytes": destination.stat().st_size,
            "request_id": resolved_request_id,
        }

    async def public_key(self, *, request_id: str | None = None) -> Any:
        return await self.request(
            "GET",
            "/api/v1/integration/public-key",
            request_id=request_id,
        )


class AdminPasswordResetRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=6, max_length=1024)
    expires_at: datetime | None = None


class UpgradeCommand(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    artifact_type: Literal["app", "core"]
    version: str = Field(min_length=1, max_length=128)
    sha256: str = Field(min_length=64, max_length=128)
    size_bytes: int = Field(gt=0)
    download_url: str = Field(min_length=1, max_length=2048)
    issued_at: datetime
    expires_at: datetime


class RechargeRequestInput(BaseModel):
    amount: str = Field(pattern=r"^\d+\.\d{2}$")
    note: str = Field(default="Token 池不足，申请补充系统额度", max_length=500)


class AcknowledgeInput(BaseModel):
    order_id: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(min_length=1, max_length=128)
    system_id: str = Field(min_length=1, max_length=128)
    redemption_operation_id: str = Field(min_length=1, max_length=128)
    ledger_id: str = Field(min_length=1, max_length=128)


def _error(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": {"code": code, "message": message},
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


def _success(payload: dict[str, Any], request_id: str) -> JSONResponse:
    response = dict(payload)
    response.setdefault("request_id", request_id)
    return JSONResponse(
        content=response,
        headers={"X-Request-ID": request_id},
    )


def _idempotency_digest(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _redis_or_none(storage: Any) -> Any | None:
    try:
        return storage.get_client()
    except (AttributeError, RuntimeError):
        return None


async def _read_idempotency(storage: Any, scope: str, key: str) -> dict[str, Any] | None:
    client = await _redis_or_none(storage)
    if client is None:
        return None
    raw = await client.get(f"{_INTEGRATION_KEY_PREFIX}:idempotency:{scope}:{key}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


async def _write_idempotency(
    storage: Any,
    scope: str,
    key: str,
    value: dict[str, Any],
    *,
    only_if_absent: bool = False,
) -> bool:
    client = await _redis_or_none(storage)
    if client is None:
        return False
    return bool(
        await client.set(
            f"{_INTEGRATION_KEY_PREFIX}:idempotency:{scope}:{key}",
            json.dumps(value, ensure_ascii=False),
            nx=only_if_absent,
            ex=_IDEMPOTENCY_TTL_SECONDS,
        )
    )


async def _claim_idempotency(
    storage: Any,
    scope: str,
    key: str | None,
    payload: Any,
    request_id: str,
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    if not key:
        return None, _error(
            status.HTTP_400_BAD_REQUEST,
            "idempotency_key_required",
            "Idempotency-Key is required for this operation.",
            request_id,
        )
    if len(key) > 200:
        return None, _error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_idempotency_key",
            "Idempotency-Key is too long.",
            request_id,
        )

    digest = _idempotency_digest(payload)
    existing = await _read_idempotency(storage, scope, key)
    if existing:
        if existing.get("digest") != digest:
            return None, _error(
                status.HTTP_409_CONFLICT,
                "idempotency_key_reused",
                "The Idempotency-Key was already used with a different request.",
                request_id,
            )
        if existing.get("state") == "done":
            return existing, None
        return None, _error(
            status.HTTP_409_CONFLICT,
            "idempotency_in_progress",
            "The same operation is still being processed.",
            request_id,
        )

    record = {"state": "processing", "digest": digest, "request_id": request_id}
    if await _write_idempotency(storage, scope, key, record, only_if_absent=True):
        return None, None

    # Another worker won the SET NX race. Read its result and let the caller
    # return a deterministic conflict or replay response.
    existing = await _read_idempotency(storage, scope, key)
    if existing and existing.get("digest") == digest and existing.get("state") == "done":
        return existing, None
    return None, _error(
        status.HTTP_409_CONFLICT,
        "idempotency_in_progress",
        "The same operation is still being processed.",
        request_id,
    )


async def _finish_idempotency(
    storage: Any,
    scope: str,
    key: str | None,
    payload: dict[str, Any],
    status_code: int = status.HTTP_200_OK,
) -> None:
    if not key:
        return
    await _write_idempotency(
        storage,
        scope,
        key,
        {
            "state": "done",
            "digest": _idempotency_digest(payload.get("_request_payload", {})),
            "status_code": status_code,
            "response": payload,
        },
    )


def _replay(record: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=int(record.get("status_code", status.HTTP_200_OK)),
        content=record.get("response", {}),
        headers={"X-Request-ID": record.get("response", {}).get("request_id", "")},
    )


async def _sales_hub_redis_config(storage: Any) -> dict[str, Any]:
    """Read the existing management-center Redis configuration.

    The deployed management system stores this record as JSON under the same
    key used by its existing `hub_url` settings.  Supporting both snake_case
    and camelCase keeps the adapter compatible with older local records while
    preserving the canonical HTTP contract at the network boundary.
    """
    client = await _redis_or_none(storage)
    if client is None:
        return {}
    raw = await client.get("longxin:sales-hub:v1:config")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _first_text(config: dict[str, Any], *names: str) -> str:
    for name in names:
        value = config.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def create_sales_integration_router(*, storage: Any, auth: Any) -> APIRouter:
    """Create both the customer-facing adapter and protected admin controls."""

    settings = SalesIntegrationSettings()

    async def resolved_base_url() -> str:
        config = await _sales_hub_redis_config(storage)
        return (
            _first_text(
                config,
                "hub_url",
                "hubUrl",
                "sales_center_base_url",
                "salesCenterBaseUrl",
                "base_url",
            )
            or settings.sales_center_base_url
        ).rstrip("/")

    async def resolved_token() -> str:
        config = await _sales_hub_redis_config(storage)
        return _first_text(
            config,
            "customer_api_token",
            "customerApiToken",
            "api_token",
            "apiToken",
            "sales_center_api_token",
            "salesCenterApiToken",
            "sales_hub_token",
            "salesHubToken",
            "hub_token",
            "hubToken",
            "token",
        ) or settings.customer_api_token

    client = SalesHubClient(
        settings,
        base_url_provider=resolved_base_url,
        token_provider=resolved_token,
    )
    router = APIRouter(tags=["sales-integration"])
    fallback_idempotency: dict[str, dict[str, Any]] = {}
    fallback_lock = asyncio.Lock()

    async def require_customer_token(
        authorization: str | None = Header(default=None),
    ) -> None:
        expected = await resolved_token()
        scheme, _, token = (authorization or "").partition(" ")
        if not expected:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Customer integration token is not configured.",
            )
        if scheme.lower() != "bearer" or not token or not hmac.compare_digest(token, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid customer integration token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def require_admin(user: Any = Depends(auth.get_current_user)) -> Any:
        if user.username != settings.integration_admin_username:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Sales integration controls require an administrator.",
            )
        return user

    async def claim(
        scope: str,
        key: str | None,
        payload: Any,
        request_id: str,
    ) -> tuple[dict[str, Any] | None, JSONResponse | None]:
        # Redis is the normal path. The in-process fallback keeps the example
        # usable in a local single-process run without weakening Redis-backed
        # deployments.
        if await _redis_or_none(storage) is not None:
            return await _claim_idempotency(storage, scope, key, payload, request_id)
        if not key:
            return None, _error(400, "idempotency_key_required", "Idempotency-Key is required for this operation.", request_id)
        digest = _idempotency_digest(payload)
        async with fallback_lock:
            existing = fallback_idempotency.get(f"{scope}:{key}")
            if existing:
                if existing["digest"] != digest:
                    return None, _error(409, "idempotency_key_reused", "The Idempotency-Key was already used with a different request.", request_id)
                if existing["state"] == "done":
                    return existing, None
                return None, _error(409, "idempotency_in_progress", "The same operation is still being processed.", request_id)
            fallback_idempotency[f"{scope}:{key}"] = {"state": "processing", "digest": digest}
        return None, None

    async def finish(
        scope: str,
        key: str | None,
        request_payload: Any,
        response_payload: dict[str, Any],
    ) -> None:
        record = {
            "state": "done",
            "digest": _idempotency_digest(request_payload),
            "status_code": status.HTTP_200_OK,
            "response": response_payload,
        }
        if await _redis_or_none(storage) is not None:
            await _write_idempotency(storage, scope, key, record)
        elif key:
            async with fallback_lock:
                fallback_idempotency[f"{scope}:{key}"] = record

    async def handle_hub_error(exc: SalesHubError, request_id: str) -> JSONResponse:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            code = "sales_center_unauthorized"
        elif exc.status_code == status.HTTP_409_CONFLICT:
            code = "sales_center_conflict"
        elif exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            code = "sales_center_rate_limited"
        elif exc.status_code and exc.status_code >= 500:
            code = "sales_center_unavailable"
        elif "timed out" in str(exc):
            code = "sales_center_timeout"
        elif "not configured" in str(exc):
            code = "sales_center_not_configured"
        else:
            code = "sales_center_request_failed"
        return _error(status.HTTP_502_BAD_GATEWAY, code, str(exc), request_id)

    @router.post("/integration/sales/v1/ping", dependencies=[Depends(require_customer_token)])
    async def ping(request: Request) -> JSONResponse:
        request_id = _request_id(request)
        return _success(
            {
                "ok": True,
                "status": "healthy",
                "system_name": settings.system_name,
                "system_id": settings.system_id,
                "app_version": settings.app_version,
                "core_version": settings.core_version,
                "checked_at": _iso_now(),
            },
            request_id,
        )

    @router.post(
        "/integration/sales/v1/admin-password-resets",
        dependencies=[Depends(require_customer_token)],
    )
    async def reset_admin_password(
        body: AdminPasswordResetRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        request_id = _request_id(request)
        request_payload = body.model_dump(mode="json")
        record, response = await claim("admin-password-reset", idempotency_key, request_payload, request_id)
        if response:
            return response
        if record:
            return _replay(record)
        if body.expires_at and body.expires_at <= _utc_now():
            return _error(400, "expired_password_operation", "expires_at must be in the future.", request_id)
        try:
            user = await auth.reset_password(
                body.username,
                body.new_password,
                expires_at=body.expires_at,
            )
        except ValueError as exc:
            return _error(404, "admin_not_found", str(exc), request_id)
        result = {"ok": True, "operation_id": body.operation_id, "username": user.username, "request_id": request_id}
        await finish("admin-password-reset", idempotency_key, request_payload, result)
        return _success(result, request_id)

    @router.post(
        "/integration/sales/v1/upgrades/{artifact_type}",
        dependencies=[Depends(require_customer_token)],
    )
    async def receive_upgrade(
        body: UpgradeCommand,
        request: Request,
        artifact_type: Literal["app", "core"] = Path(...),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        request_id = _request_id(request)
        request_payload = body.model_dump(mode="json")
        if body.artifact_type != artifact_type:
            return _error(400, "artifact_type_mismatch", "The path and body artifact_type must match.", request_id)
        if body.expires_at <= _utc_now():
            return _error(400, "upgrade_command_expired", "The upgrade command has expired.", request_id)
        record, response = await claim("upgrade", idempotency_key, request_payload, request_id)
        if response:
            return response
        if record:
            return _replay(record)

        # The receiver validates and queues the command. A deployment-specific
        # executor must perform backup/download/restart/rollback; no shell
        # command is executed from an HTTP request.
        queue = await _redis_or_none(storage)
        if queue is not None:
            await queue.rpush(
                f"{_INTEGRATION_KEY_PREFIX}:upgrade-queue",
                json.dumps(
                    {
                        **request_payload,
                        "received_at": _iso_now(),
                        "request_id": request_id,
                        "state": "pending",
                    },
                    ensure_ascii=False,
                ),
            )
        result = {
            "ok": True,
            "operation_id": body.operation_id,
            "state": "pending",
            "request_id": request_id,
        }
        await finish("upgrade", idempotency_key, request_payload, result)
        return _success(result, request_id)

    @router.get("/admin/integration/sales/status", dependencies=[Depends(require_admin)])
    async def integration_status(request: Request) -> JSONResponse:
        request_id = _request_id(request)
        return _success(
            {
                "configured": bool(await resolved_base_url() and await resolved_token()),
                "sales_center_base_url": await resolved_base_url() or None,
                "system_id": settings.system_id,
                "system_name": settings.system_name,
                "app_version": settings.app_version,
                "core_version": settings.core_version,
                "request_id": request_id,
            },
            request_id,
        )

    @router.post("/admin/integration/sales/verify", dependencies=[Depends(require_admin)])
    async def verify_sales_connection(
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        request_id = _request_id(request)
        if not idempotency_key:
            return _error(400, "idempotency_key_required", "Idempotency-Key is required for this operation.", request_id)
        try:
            result = await client.verify_connection(
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
            return _success({"ok": True, "sales_center": result}, request_id)
        except SalesHubError as exc:
            return await handle_hub_error(exc, request_id)

    @router.post("/admin/integration/sales/usage-report", dependencies=[Depends(require_admin)])
    async def send_usage_report(
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        request_id = _request_id(request)
        if not idempotency_key:
            return _error(400, "idempotency_key_required", "Idempotency-Key is required for this operation.", request_id)
        user_ids = set(auth.user_ids)
        redis_client = await _redis_or_none(storage)
        if redis_client is not None:
            async for key in redis_client.scan_iter(match="longxin:auth:user:*"):
                user_ids.add(str(key).rsplit(":", 1)[-1])
        cumulative_consumed = 0
        for user_id in sorted(user_ids):
            try:
                usage = await auth.get_token_usage(user_id)
            except Exception:
                continue
            cumulative_consumed += usage.total_tokens
        report = {
            "system_id": settings.system_id,
            "pool_tokens": int(os.getenv("LONGXIN_POOL_TOKENS", "0")),
            "total_recharged": os.getenv("LONGXIN_TOTAL_RECHARGED", "0.00"),
            "cumulative_consumed": cumulative_consumed,
            "cumulative_credits": int(os.getenv("LONGXIN_CUMULATIVE_CREDITS", "0")),
            "app_version": settings.app_version,
            "client_reported_at": _iso_now(),
        }
        try:
            result = await client.report_usage(report, idempotency_key=idempotency_key, request_id=request_id)
            return _success({"ok": True, "report": report, "sales_center": result}, request_id)
        except SalesHubError as exc:
            return await handle_hub_error(exc, request_id)

    @router.post("/admin/integration/sales/recharge-requests", dependencies=[Depends(require_admin)])
    async def create_recharge_request(
        body: RechargeRequestInput,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        request_id = _request_id(request)
        if not idempotency_key:
            return _error(400, "idempotency_key_required", "Idempotency-Key is required for this operation.", request_id)
        try:
            result = await client.submit_recharge_request(
                amount=body.amount,
                note=body.note,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
            return _success({"ok": True, "sales_center": result}, request_id)
        except SalesHubError as exc:
            return await handle_hub_error(exc, request_id)

    @router.get("/admin/integration/sales/recharge-requests/poll", dependencies=[Depends(require_admin)])
    async def poll_recharge_requests(
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        request_id = _request_id(request)
        if not idempotency_key:
            return _error(400, "idempotency_key_required", "Idempotency-Key is required for this operation.", request_id)
        try:
            result = await client.poll_recharge_requests(
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
            return _success({"ok": True, "sales_center": result}, request_id)
        except SalesHubError as exc:
            return await handle_hub_error(exc, request_id)

    @router.post("/admin/integration/sales/recharge-requests/ack", dependencies=[Depends(require_admin)])
    async def acknowledge_recharge(
        body: AcknowledgeInput,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        request_id = _request_id(request)
        if not idempotency_key:
            return _error(400, "idempotency_key_required", "Idempotency-Key is required for this operation.", request_id)
        try:
            result = await client.acknowledge_recharge(
                body.order_id,
                operation_id=body.operation_id,
                system_id=body.system_id,
                redemption_operation_id=body.redemption_operation_id,
                ledger_id=body.ledger_id,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
            return _success({"ok": True, "sales_center": result}, request_id)
        except SalesHubError as exc:
            return await handle_hub_error(exc, request_id)

    @router.get("/admin/integration/sales/public-key", dependencies=[Depends(require_admin)])
    async def get_sales_public_key(request: Request) -> JSONResponse:
        request_id = _request_id(request)
        try:
            result = await client.public_key(request_id=request_id)
            return _success({"ok": True, "public_key": result}, request_id)
        except SalesHubError as exc:
            return await handle_hub_error(exc, request_id)

    @router.get("/admin/integration/sales/releases/{artifact_type}", dependencies=[Depends(require_admin)])
    async def get_latest_release(
        request: Request,
        artifact_type: Literal["app", "core"] = Path(...),
    ) -> JSONResponse:
        request_id = _request_id(request)
        try:
            result = await client.download_release(
                artifact_type,
                settings.upgrade_dir / f"latest-{artifact_type}.tar.gz",
                request_id=request_id,
            )
            return _success({"ok": True, "artifact_type": artifact_type, "release": result}, request_id)
        except SalesHubError as exc:
            return await handle_hub_error(exc, request_id)

    return router
