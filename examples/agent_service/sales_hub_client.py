"""HTTP adapter for the Longxin Sales Hub contract."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx


class SalesHubClientError(RuntimeError):
    """Safe, structured adapter error without credentials or response bodies."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


ConfigLoader = Callable[[], Awaitable[dict[str, Any]]]


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
            "X-Request-ID": request_id,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self._timeout,
                    connect=self._connect_timeout,
                ),
            ) as client:
                response = await client.request(
                    method,
                    f"{hub_url.rstrip('/')}{path}",
                    headers=headers,
                    params=params,
                    json=json_body,
                )
        except httpx.TimeoutException as exc:
            raise SalesHubClientError(
                "hub_timeout",
                "Sales Hub request timed out.",
                504,
            ) from exc
        except httpx.HTTPError as exc:
            raise SalesHubClientError(
                "hub_unreachable",
                "Sales Hub could not be reached.",
                502,
            ) from exc
        if response.is_error:
            raise SalesHubClientError(
                "hub_request_failed",
                f"Sales Hub returned HTTP {response.status_code}.",
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
