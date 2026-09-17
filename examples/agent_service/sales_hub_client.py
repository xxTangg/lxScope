"""HTTP adapter for the Longxin Sales Hub contract."""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx


logger = logging.getLogger(__name__)


class SalesHubClientError(RuntimeError):
    """Safe, structured adapter error without credentials or raw response bodies."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


ConfigLoader = Callable[[], Awaitable[dict[str, Any]]]

_REMOTE_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|access[_-]?token|token|password|secret|api[_-]?key)(\s*[:=]\s*)([^,}\s]+)"
)


def _sanitize_remote_text(value: str) -> str:
    compact = " ".join(value.split())
    compact = re.sub(r"(?i)Bearer\s+[^\s,}]+", "Bearer [redacted]", compact)
    return _REMOTE_SECRET_PATTERN.sub(r"\1\2[redacted]", compact)[:300]


def _safe_remote_error(response: httpx.Response) -> str | None:
    """Extract only a short error code/message from a remote JSON error."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None

    detail = payload.get("detail")
    candidates: list[Any] = [payload]
    if isinstance(detail, (dict, list)):
        candidates.append(detail)
    error = payload.get("error")
    if isinstance(error, (dict, list)):
        candidates.append(error)
    for candidate in candidates:
        values = candidate if isinstance(candidate, list) else [candidate]
        for value in values:
            if not isinstance(value, dict):
                continue
            code = value.get("code")
            message = value.get("message") or value.get("msg")
            if not isinstance(message, str) or not message.strip():
                continue
            if isinstance(code, str) and code.strip():
                return f"{code.strip()}: {_sanitize_remote_text(message)}"
            return _sanitize_remote_text(message)
    raw = response.text.strip()
    return _sanitize_remote_text(raw) if raw else None


class SalesHubClient:
    """Keep remote HTTP, timeout, auth-header, and response rules out of services."""

    def __init__(
        self,
        config_loader: ConfigLoader,
        *,
        timeout: float = 8.0,
        connect_timeout: float = 3.0,
    ) -> None:
        self._config_loader = config_loader
        self._timeout = timeout
        self._connect_timeout = connect_timeout

    async def request(
        self,
        method: str,
        path: str,
        *,
        request_id: str,
        idempotency_key: str | None = None,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = await self._config_loader()
        hub_url = config.get("hub_url")
        token = config.get("token")
        if not hub_url or not token:
            raise SalesHubClientError(
                "hub_not_configured",
                "Sales Hub is not configured.",
                503,
            )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        method_upper = method.upper()
        retryable = bool(idempotency_key) or method_upper in {"GET", "HEAD", "OPTIONS"}
        max_attempts = 3 if retryable else 1
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self._timeout,
                    connect=self._connect_timeout,
                ),
            ) as client:
                for attempt in range(max_attempts):
                    try:
                        response = await client.request(
                            method,
                            f"{hub_url.rstrip('/')}{path}",
                            headers=headers,
                            params=params,
                            json=json_body,
                        )
                    except httpx.TimeoutException as exc:
                        if attempt + 1 < max_attempts:
                            await asyncio.sleep(0.2 * (2**attempt))
                            continue
                        raise SalesHubClientError(
                            "hub_timeout",
                            "Sales Hub request timed out.",
                            504,
                        ) from exc
                    except httpx.HTTPError as exc:
                        if attempt + 1 < max_attempts:
                            await asyncio.sleep(0.2 * (2**attempt))
                            continue
                        raise SalesHubClientError(
                            "hub_unreachable",
                            "Sales Hub could not be reached.",
                            502,
                        ) from exc
                    if response.status_code >= 500 and attempt + 1 < max_attempts:
                        await asyncio.sleep(0.2 * (2**attempt))
                        continue
                    break
        except SalesHubClientError:
            raise
        if response.is_error:
            remote_error = _safe_remote_error(response)
            message = f"Sales Hub returned HTTP {response.status_code}."
            if remote_error:
                message += f" {remote_error}"
            logger.warning(
                "Sales Hub rejected %s %s with HTTP %s: %s",
                method_upper,
                path,
                response.status_code,
                remote_error or "no safe error detail",
            )
            raise SalesHubClientError(
                "hub_request_failed",
                message,
                502,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SalesHubClientError(
                "hub_invalid_response",
                "Sales Hub returned invalid JSON.",
                502,
            ) from exc
        if not isinstance(payload, dict):
            raise SalesHubClientError(
                "hub_invalid_response",
                "Sales Hub returned an invalid response.",
                502,
            )
        return payload
