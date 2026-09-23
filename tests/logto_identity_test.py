"""Tests for optional Logto Management API profile resolution."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

SERVICE_DIR = Path(__file__).parents[1] / "examples" / "agent_service"
sys.path.insert(0, str(SERVICE_DIR))

from identity.logto import LogtoAuthService  # noqa: E402


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _ManagementClient:
    posts: list[tuple[str, dict[str, Any]]] = []
    gets: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, **_: Any) -> None:
        pass

    async def __aenter__(self) -> _ManagementClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def post(self, url: str, *, data: dict[str, Any]) -> _Response:
        self.posts.append((url, data))
        return _Response({"access_token": "management-token", "expires_in": 3600})

    async def get(self, url: str, *, headers: dict[str, str]) -> _Response:
        self.gets.append((url, headers))
        return _Response({"id": "dfalxplsltko", "username": "lxscope_admin"})


class LogtoProfileResolutionTest(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _ManagementClient.posts = []
        _ManagementClient.gets = []
        self.auth = LogtoAuthService(
            endpoint="https://tenant.logto.app",
            api_resource="https://api.example.test",
            storage=object(),
            management_client_id="m2m-client",
            management_client_secret="not-printed",
            management_api_resource="https://tenant.logto.app/api",
        )

    async def test_profile_username_is_cached(self) -> None:
        with patch("identity.logto.httpx.AsyncClient", _ManagementClient):
            first = await self.auth._logto_username("dfalxplsltko")
            second = await self.auth._logto_username("dfalxplsltko")

        self.assertEqual(first, "lxscope_admin")
        self.assertEqual(second, "lxscope_admin")
        self.assertEqual(len(_ManagementClient.posts), 1)
        self.assertEqual(len(_ManagementClient.gets), 1)
        self.assertEqual(
            _ManagementClient.gets[0][0],
            "https://tenant.logto.app/api/users/dfalxplsltko",
        )
        self.assertEqual(
            _ManagementClient.gets[0][1],
            {"Authorization": "Bearer management-token"},
        )

    async def test_profile_resolution_is_disabled_without_optional_config(self) -> None:
        auth = LogtoAuthService(
            endpoint="https://tenant.logto.app",
            api_resource="https://api.example.test",
            storage=object(),
        )

        self.assertIsNone(await auth._logto_username("dfalxplsltko"))

