# -*- coding: utf-8 -*-
"""Contract tests for the Longxin product-level administrator APIs."""
import sys
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx

SERVICE_DIR = Path(__file__).parents[1] / "examples" / "agent_service"
sys.path.insert(0, str(SERVICE_DIR))

import sales_hub_client as hub_module  # noqa: E402
from admin_api import AdminService, admin_router, sales_hub_router  # noqa: E402
from auth import JWTAuthService  # noqa: E402


class _MemoryRedis:
    """Small async Redis subset required by the auth/admin contract tests."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.values.get(key)

    async def set(
        self,
        key: str,
        value: Any,
        *,
        nx: bool = False,
    ) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def rpush(self, key: str, value: Any) -> int:
        self.values.setdefault(key, [])
        self.values[key].append(value)
        return len(self.values[key])

    async def lrange(self, key: str, start: int, end: int) -> list[Any]:
        values = self.values.get(key, [])
        stop = None if end == -1 else end + 1
        return values[start:stop]

    async def scan_iter(self, *, match: str, count: int = 100):
        del count
        prefix, _, suffix = match.partition("*")
        for key in self.values:
            if key.startswith(prefix) and key.endswith(suffix):
                yield key


class _Storage:
    """Use one in-memory Redis instance without starting an external server."""

    def __init__(self, redis: Any) -> None:
        self._client = redis

    def get_client(self) -> Any:
        return self._client

    async def aclose(self) -> None:
        self._client = None


class _FakeHubClient:
    """Deterministic Sales Hub responses for the submit/poll/ACK contract."""

    acked_orders: list[str] = []
    reported_payloads: list[dict[str, Any]] = []

    def __init__(self, **_: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeHubClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        **_: Any,
    ) -> httpx.Response:
        if method == "POST" and url.endswith("/api/v1/integration/recharge-requests"):
            return httpx.Response(
                200,
                json={
                    "order_id": "ord-remote-1",
                    "system_id": "system-test",
                    "status": "pending",
                    "delivery_status": "not_delivered",
                },
            )
        if method == "GET" and url.endswith("/api/v1/integration/recharge-requests/poll"):
            payload = {
                "system_id": "system-test",
                "amount": "100.00",
                "tokens": 1000,
                "order_id": "ord-remote-1",
                "nonce": "nonce-remote-1",
            }
            payload_bytes = json_bytes = json_module_dumps(payload)
            signature = hmac.new(
                b"test-secret",
                payload_bytes,
                hashlib.sha256,
            ).hexdigest()
            code = (
                "LXRC2."
                + base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
                + "."
                + signature
            )
            return httpx.Response(
                200,
                json={
                    "orders": [
                        {
                            "order_id": "ord-remote-1",
                            "system_id": "system-test",
                            "amount": "100.00",
                            "tokens": 1000,
                            "status": "approved",
                            "delivery_status": "not_delivered",
                            "recharge_code": code,
                        },
                    ],
                },
            )
        if method == "POST" and url.endswith("/api/v1/integration/recharge-requests/ord-remote-1/ack"):
            self.acked_orders.append("ord-remote-1")
            return httpx.Response(200, json={"status": "delivered"})
        if method == "POST" and url.endswith("/api/v1/integration/usage-reports"):
            self.reported_payloads.append(json or {})
            return httpx.Response(
                200,
                json={"report_id": "report-1", "reported_at": "2026-09-15T00:00:00Z"},
            )
        return httpx.Response(404, json={"detail": "not found"})


def json_module_dumps(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


class AdminApiTest(IsolatedAsyncioTestCase):
    """Exercise the first administrator feature slice end to end."""

    def setUp(self) -> None:
        redis = _MemoryRedis()
        storage = _Storage(redis)
        auth = JWTAuthService(
            {"admin": ("admin-id", "admin-password", "admin")},
            "a" * 32,
            storage=storage,
        )
        app = FastAPI()
        app.state.auth = auth
        app.state.admin_service = AdminService(storage, auth)
        app.include_router(auth.router)
        app.include_router(admin_router)
        app.include_router(sales_hub_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()

    def _login(self, username: str, password: str) -> dict[str, Any]:
        response = self.client.post(
            "/auth/login",
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_admin_can_create_and_manage_user(self) -> None:
        login = self._login("admin", "admin-password")
        headers = {"Authorization": f"Bearer {login['access_token']}"}

        overview = self.client.get("/admin/overview", headers=headers)
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["admin_count"], 1)

        created = self.client.post(
            "/admin/users",
            headers=headers,
            json={
                "username": "alice",
                "initial_password": "alice-password",
                "bonus_tokens": 0,
            },
        )
        self.assertEqual(created.status_code, 201)
        user_id = created.json()["id"]
        self.assertEqual(created.json()["role"], "user")

        users = self.client.get("/admin/users", headers=headers)
        self.assertEqual(users.status_code, 200)
        self.assertEqual(users.json()["total"], 2)

        banned = self.client.patch(
            f"/admin/users/{user_id}",
            headers=headers,
            json={"status": "banned"},
        )
        self.assertEqual(banned.status_code, 200)
        self.assertEqual(banned.json()["status"], "banned")

    def test_normal_user_cannot_enter_admin_api(self) -> None:
        login = self._login("admin", "admin-password")
        admin_headers = {"Authorization": f"Bearer {login['access_token']}"}
        created = self.client.post(
            "/admin/users",
            headers=admin_headers,
            json={
                "username": "member",
                "initial_password": "member-password",
            },
        )
        self.assertEqual(created.status_code, 201)

        member_login = self._login("member", "member-password")
        member_headers = {"Authorization": f"Bearer {member_login['access_token']}"}
        response = self.client.get("/admin/overview", headers=member_headers)
        self.assertEqual(response.status_code, 403)

    def test_hub_token_and_ping_are_separate_from_browser_jwt(self) -> None:
        login = self._login("admin", "admin-password")
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        saved = self.client.patch(
            "/admin/sales-hub/config",
            headers=headers,
            json={
                "system_id": "system-test",
                "hub_url": "https://sales.example.test",
                "token": "customer-token-123",
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["token_masked"], "cust****-123")

        missing = self.client.post("/integration/sales/v1/ping")
        self.assertEqual(missing.status_code, 401)
        ping = self.client.post(
            "/integration/sales/v1/ping",
            headers={"Authorization": "Bearer customer-token-123"},
        )
        self.assertEqual(ping.status_code, 200)
        self.assertEqual(ping.json()["system_id"], "system-test")

    def test_recharge_submit_poll_redeem_ack_and_usage_report(self) -> None:
        login = self._login("admin", "admin-password")
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        saved = self.client.patch(
            "/admin/sales-hub/config",
            headers=headers,
            json={
                "system_id": "system-test",
                "hub_url": "https://sales.example.test",
                "token": "customer-token-123",
            },
        )
        self.assertEqual(saved.status_code, 200)
        _FakeHubClient.acked_orders.clear()
        _FakeHubClient.reported_payloads.clear()
        with patch.dict(os.environ, {"LONGXIN_RECHARGE_CODE_SECRET": "test-secret"}):
            with patch.object(hub_module.httpx, "AsyncClient", _FakeHubClient):
                submitted = self.client.post(
                    "/admin/quota/recharge-requests",
                    headers={
                        **headers,
                        "Idempotency-Key": "idem-submit-1",
                    },
                    json={"amount": "100.00"},
                )
                self.assertEqual(submitted.status_code, 200)
                self.assertEqual(submitted.json()["status"], "pending")
                duplicate = self.client.post(
                    "/admin/quota/recharge-requests",
                    headers={
                        **headers,
                        "Idempotency-Key": "idem-submit-1",
                    },
                    json={"amount": "100.00"},
                )
                self.assertEqual(duplicate.status_code, 200)
                self.assertEqual(duplicate.json()["order_id"], submitted.json()["order_id"])

                synced = self.client.post(
                    "/admin/quota/recharge-requests/sync",
                    headers={
                        **headers,
                        "Idempotency-Key": "idem-sync-1",
                    },
                )
                self.assertEqual(synced.status_code, 200)
                self.assertEqual(synced.json()["state"], "completed")
                self.assertEqual(_FakeHubClient.acked_orders, ["ord-remote-1"])
                synced_again = self.client.post(
                    "/admin/quota/recharge-requests/sync",
                    headers={
                        **headers,
                        "Idempotency-Key": "idem-sync-2",
                    },
                )
                self.assertEqual(synced_again.status_code, 200)
                self.assertEqual(_FakeHubClient.acked_orders, ["ord-remote-1"])

                orders = self.client.get("/admin/quota/recharge-requests", headers=headers)
                self.assertEqual(orders.json()["orders"][0]["delivery_status"], "delivered")
                quota = self.client.get("/admin/quota", headers=headers)
                self.assertEqual(quota.json()["pool_tokens"], 1000)

                report = self.client.post(
                    "/admin/sales-hub/usage-report",
                    headers={
                        **headers,
                        "Idempotency-Key": "idem-report-1",
                    },
                )
                self.assertEqual(report.status_code, 200)
                self.assertEqual(report.json()["state"], "completed")
                self.assertEqual(len(_FakeHubClient.reported_payloads), 1)
