# -*- coding: utf-8 -*-
"""Small read-only client for resolving Logto user display profiles."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping
from typing import Any

import httpx


class LogtoDirectoryClient:
    """Read organization users from Logto's Management API."""

    def __init__(
        self,
        endpoint: str,
        app_id: str,
        app_secret: str,
        management_api_resource: str,
        *,
        timeout: float = 5.0,
        cache_ttl: float = 30.0,
    ) -> None:
        self.endpoint = endpoint.strip().rstrip("/")
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self.management_api_resource = management_api_resource.strip()
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._cache: dict[str, tuple[float, dict[str, Mapping[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls) -> "LogtoDirectoryClient | None":
        """Create the client only when all Management API settings exist."""

        endpoint = (
            os.getenv("LOGTO_MANAGEMENT_ENDPOINT", "").strip()
            or os.getenv("LOGTO_ENDPOINT", "").strip()
        )
        values = (
            endpoint,
            os.getenv("LOGTO_M2M_APP_ID", "").strip(),
            os.getenv("LOGTO_M2M_APP_SECRET", "").strip(),
            os.getenv("LOGTO_MANAGEMENT_API_RESOURCE", "").strip(),
        )
        if not all(values):
            return None
        return cls(*values)

    async def _token(self) -> str:
        async with self._lock:
            if self._access_token and time.monotonic() < self._access_token_expires_at:
                return self._access_token
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.endpoint}/oidc/token",
                    data={
                        "grant_type": "client_credentials",
                        "resource": self.management_api_resource,
                        "scope": "all",
                    },
                    auth=(self.app_id, self.app_secret),
                )
                response.raise_for_status()
                payload = response.json()
            token = payload.get("access_token")
            if not isinstance(token, str) or not token.strip():
                raise ValueError("Logto token response did not contain access_token")
            expires_in = payload.get("expires_in", 300)
            try:
                expires_in = float(expires_in)
            except (TypeError, ValueError):
                expires_in = 300.0
            self._access_token = token
            self._access_token_expires_at = time.monotonic() + max(30.0, expires_in - 30.0)
            return token

    async def list_organization_users(
        self,
        organization_id: str,
    ) -> dict[str, Mapping[str, Any]]:
        """Return profiles keyed by Logto user ID for one organization."""

        cached = self._cache.get(organization_id)
        now = time.monotonic()
        if cached and cached[0] > now:
            return cached[1]

        users: list[Mapping[str, Any]] = []
        page = 1
        page_size = 100
        while True:
            token = await self._token()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.endpoint}/api/organizations/{organization_id}/users",
                    params={"page": page, "page_size": page_size},
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                payload = response.json()
            if isinstance(payload, list):
                page_users = payload
            elif isinstance(payload, Mapping):
                page_users = payload.get("users") or payload.get("data") or []
            else:
                page_users = []
            page_users = [item for item in page_users if isinstance(item, Mapping)]
            users.extend(page_users)
            if len(page_users) < page_size:
                break
            page += 1

        profiles = {
            str(item["id"]): item
            for item in users
            if item.get("id")
        }
        self._cache[organization_id] = (now + self.cache_ttl, profiles)
        return profiles


__all__ = ["LogtoDirectoryClient"]
