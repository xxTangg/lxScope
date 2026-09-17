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
    submitted_payloads: list[dict[str, Any]] = []
    reported_payloads: list[dict[str, Any]] = []
    usage_response: dict[str, Any] | None = None

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
        headers: dict[str, str] | None = None,
        **_: Any,
    ) -> httpx.Response:
        if method == "POST" and url.endswith("/api/v1/integration/recharge-requests"):
            self.submitted_payloads.append(json or {})
            return httpx.Response(
                200,
                json={
                    "order_id": "ord-remote-1",
                    "system_id": "system-test",
                    "status": "pending",
                    "delivery_status": "not_delivered",
                    "request_id": (headers or {}).get("X-Request-ID", ""),
                },
            )
        if method == "GET" and url.endswith("/api/v1/integration/recharge-requests/poll"):
            payload = {
                "system_id": "system-test",
                "amount": "100.00",
                "tokens": 1000,
                "order_id": "ord-remote-1",
                "nonce": "nonce-remote-1",
                "version": "1",
                "issued_at": "2026-09-16T00:00:00+00:00",
                "expires_at": "2099-01-01T00:00:00+00:00",
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
                    "request_id": (headers or {}).get("X-Request-ID", "poll-request"),
                },
            )
        if method == "POST" and url.endswith("/api/v1/integration/recharge-requests/ord-remote-1/ack"):
            self.acked_orders.append("ord-remote-1")
            return httpx.Response(
                200,
                json={
                    "order_id": "ord-remote-1",
                    "status": "approved",
                    "delivery_status": "delivered",
                    "request_id": (headers or {}).get("X-Request-ID", ""),
                },
            )
        if method == "POST" and url.endswith("/api/v1/integration/usage-reports"):
            self.reported_payloads.append(json or {})
            return httpx.Response(
                200,
                json=self.usage_response
                or {
                    "report_id": "report-1",
                    "system_id": "system-test",
                    "accepted": True,
                    "reported_at": "2026-09-15T00:00:00Z",
                    "cumulative_consumed_delta": 0,
                    "cumulative_credits_delta": 1000,
                    "request_id": (headers or {}).get("X-Request-ID", "report-request"),
                },
            )
        return httpx.Response(404, json={"detail": "not found"})


class _VerifyHubClient:
    """Return a configurable bidirectional verification response."""

    payload: dict[str, Any] = {}

    def __init__(self, **_: Any) -> None:
        pass

    async def __aenter__(self) -> "_VerifyHubClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def request(self, *args: Any, **kwargs: Any) -> httpx.Response:
        del args, kwargs
        return httpx.Response(200, json=self.payload)


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
            headers={**headers, "Idempotency-Key": "idem-create-alice"},
            json={
                "username": "alice",
                "initial_password": "alice-password",
                "bonus_tokens": 0,
            },
        )
        self.assertEqual(created.status_code, 201)
        user_id = created.json()["id"]
        self.assertEqual(created.json()["role"], "user")

        replayed = self.client.post(
            "/admin/users",
            headers={**headers, "Idempotency-Key": "idem-create-alice"},
            json={
                "username": "alice",
                "initial_password": "alice-password",
                "bonus_tokens": 0,
            },
        )
        self.assertEqual(replayed.status_code, 201)
        self.assertEqual(replayed.json()["id"], user_id)
        reused = self.client.post(
            "/admin/users",
            headers={**headers, "Idempotency-Key": "idem-create-alice"},
            json={
                "username": "alice-two",
                "initial_password": "alice-password",
                "bonus_tokens": 0,
            },
        )
        self.assertEqual(reused.status_code, 409)
        self.assertEqual(reused.json()["detail"]["code"], "idempotency_key_reused")

        users = self.client.get("/admin/users", headers=headers)
        self.assertEqual(users.status_code, 200)
        self.assertEqual(users.json()["total"], 2)

        banned = self.client.patch(
            f"/admin/users/{user_id}",
            headers={**headers, "Idempotency-Key": "idem-update-alice"},
            json={"status": "banned"},
        )
        self.assertEqual(banned.status_code, 200)
        self.assertEqual(banned.json()["status"], "banned")

        deleted = self.client.delete(
            f"/admin/users/{user_id}",
            headers={**headers, "Idempotency-Key": "idem-delete-alice"},
            json={"confirm": True, "reason": "account closure requested"},
        )
        self.assertEqual(deleted.status_code, 204)
        deleted_again = self.client.delete(
            f"/admin/users/{user_id}",
            headers={**headers, "Idempotency-Key": "idem-delete-alice"},
            json={"confirm": True, "reason": "account closure requested"},
        )
        self.assertEqual(deleted_again.status_code, 204)

    def test_normal_user_cannot_enter_admin_api(self) -> None:
        login = self._login("admin", "admin-password")
        admin_headers = {"Authorization": f"Bearer {login['access_token']}"}
        created = self.client.post(
            "/admin/users",
            headers={**admin_headers, "Idempotency-Key": "idem-create-member"},
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

    def test_reset_password_expires_and_audit_is_queryable(self) -> None:
        login = self._login("admin", "admin-password")
        headers = {
            "Authorization": f"Bearer {login['access_token']}",
            "X-Request-ID": "req-reset-1",
        }
        created = self.client.post(
            "/admin/users",
            headers={**headers, "Idempotency-Key": "idem-create-reset-target"},
            json={"username": "reset-target", "initial_password": "old-password"},
        )
        self.assertEqual(created.status_code, 201)
        user_id = created.json()["id"]

        rejected = self.client.post(
            f"/admin/users/{user_id}/reset-password",
            headers={**headers, "Idempotency-Key": "idem-reset-invalid-1"},
            json={"reason": "support reset", "admin_password": "wrong-password"},
        )
        self.assertEqual(rejected.status_code, 403)

        reset = self.client.post(
            f"/admin/users/{user_id}/reset-password",
            headers={**headers, "Idempotency-Key": "idem-reset-1"},
            json={"reason": "support reset", "admin_password": "admin-password"},
        )
        self.assertEqual(reset.status_code, 200)
        self.assertGreater(reset.json()["expires_at"], "2026-01-01T00:00:00+00:00")
        temporary_login = self._login("reset-target", reset.json()["temporary_password"])
        self.assertEqual(temporary_login["user"]["id"], user_id)

        revoked = self.client.delete(
            f"/admin/users/{user_id}/sessions",
            headers={**headers, "Idempotency-Key": "idem-revoke-reset-target"},
        )
        self.assertEqual(revoked.status_code, 204)
        old_token = self.client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {temporary_login['access_token']}"},
        )
        self.assertEqual(old_token.status_code, 401)

        events = self.client.get(
            "/admin/audit/events",
            headers={"Authorization": f"Bearer {login['access_token']}"},
        )
        self.assertEqual(events.status_code, 200)
        self.assertTrue(any(item["action"] == "user.reset_password" for item in events.json()["events"]))

    def test_hub_token_and_ping_are_separate_from_browser_jwt(self) -> None:
        login = self._login("admin", "admin-password")
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        saved = self.client.patch(
            "/admin/sales-hub/config",
            headers={**headers, "Idempotency-Key": "idem-config-ping"},
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

    def test_verify_accepts_boolean_inbound_response(self) -> None:
        login = self._login("admin", "admin-password")
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        saved = self.client.patch(
            "/admin/sales-hub/config",
            headers={**headers, "Idempotency-Key": "idem-config-verify"},
            json={
                "system_id": "system-test",
                "hub_url": "https://sales.example.test",
                "token": "customer-token-123",
            },
        )
        self.assertEqual(saved.status_code, 200)

        _VerifyHubClient.payload = {
            "outbound": True,
            "inbound": True,
            "ping": {"ok": True, "system_id": "system-test"},
            "request_id": "req-verify-1",
        }
        try:
            with patch.object(hub_module.httpx, "AsyncClient", _VerifyHubClient):
                verified = self.client.post(
                    "/admin/sales-hub/verify",
                    headers={**headers, "X-Request-ID": "req-verify-1", "Idempotency-Key": "idem-verify-1"},
                )
        finally:
            _VerifyHubClient.payload = {}

        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.json()["state"], "completed")
        config = self.client.get("/admin/sales-hub/config", headers=headers)
        self.assertEqual(config.status_code, 200)
        self.assertEqual(config.json()["outbound_status"], "ok")
        self.assertEqual(config.json()["inbound_status"], "ok")

    def test_recharge_submit_poll_redeem_ack_and_usage_report(self) -> None:
        login = self._login("admin", "admin-password")
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        saved = self.client.patch(
            "/admin/sales-hub/config",
            headers={**headers, "Idempotency-Key": "idem-config-recharge"},
            json={
                "system_id": "system-test",
                "hub_url": "https://sales.example.test",
                "token": "customer-token-123",
            },
        )
        self.assertEqual(saved.status_code, 200)
        _FakeHubClient.acked_orders.clear()
        _FakeHubClient.submitted_payloads.clear()
        _FakeHubClient.reported_payloads.clear()
        _FakeHubClient.usage_response = None
        with patch.dict(
            os.environ,
            {
                "LONGXIN_RECHARGE_CODE_SECRET": "test-secret",
                "LONGXIN_ALLOW_LEGACY_HMAC_CODES": "true",
            },
        ):
            with patch.object(hub_module.httpx, "AsyncClient", _FakeHubClient):
                submitted = self.client.post(
                    "/admin/quota/recharge-requests",
                    headers={
                        **headers,
                        "X-Request-ID": "req-submit-1",
                        "Idempotency-Key": "idem-submit-1",
                    },
                    json={"amount": "100"},
                )
                self.assertEqual(submitted.status_code, 200)
                self.assertEqual(submitted.json()["status"], "pending")
                self.assertEqual(submitted.json()["request_id"], "req-submit-1")
                self.assertEqual(_FakeHubClient.submitted_payloads[0]["amount"], "100.00")
                self.assertNotIn("note", _FakeHubClient.submitted_payloads[0])
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
                self.assertEqual(_FakeHubClient.reported_payloads[0]["total_recharged"], "100.00")

    def test_direct_sales_code_creates_local_shadow_order(self) -> None:
        login = self._login("admin", "admin-password")
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        payload = {
            "system_id": "local-system",
            "amount": "100.00",
            "tokens": 1000,
            "version": "1",
            "order_id": "ord-direct-code-1",
            "nonce": "nonce-direct-code-1",
            "issued_at": "2026-09-17T00:00:00+00:00",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
        payload_bytes = json_module_dumps(payload)
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
        with patch.dict(
            os.environ,
            {
                "LONGXIN_RECHARGE_CODE_SECRET": "test-secret",
                "LONGXIN_ALLOW_LEGACY_HMAC_CODES": "true",
            },
        ):
            redeemed = self.client.post(
                "/admin/quota/redeem-code",
                headers={
                    **headers,
                    "X-Request-ID": "req-direct-code-1",
                    "Idempotency-Key": "idem-direct-code-1",
                },
                json={"code": code, "confirm": True},
            )
            self.assertEqual(redeemed.status_code, 200)
            self.assertEqual(redeemed.json()["amount"], "100.00")
            self.assertEqual(redeemed.json()["tokens"], 1000)

            quota = self.client.get("/admin/quota", headers=headers)
            self.assertEqual(quota.status_code, 200)
            self.assertEqual(quota.json()["pool_tokens"], 1000)

            orders = self.client.get("/admin/quota/recharge-requests", headers=headers)
            self.assertEqual(orders.status_code, 200)
            self.assertEqual(orders.json()["orders"][0]["order_id"], "ord-direct-code-1")
            self.assertEqual(orders.json()["orders"][0]["delivery_status"], "delivered")

            duplicate = self.client.post(
                "/admin/quota/redeem-code",
                headers={**headers, "Idempotency-Key": "idem-direct-code-2"},
                json={"code": code, "confirm": True},
            )
            self.assertEqual(duplicate.status_code, 409)
            self.assertEqual(duplicate.json()["detail"]["code"], "code_already_redeemed")

    def test_usage_report_rejects_incomplete_sales_response(self) -> None:
        login = self._login("admin", "admin-password")
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        saved = self.client.patch(
            "/admin/sales-hub/config",
            headers={**headers, "Idempotency-Key": "idem-config-usage-invalid"},
            json={
                "system_id": "system-test",
                "hub_url": "https://sales.example.test",
                "token": "customer-token-123",
            },
        )
        self.assertEqual(saved.status_code, 200)
        _FakeHubClient.usage_response = {
            "reported_at": "2026-09-15T00:00:00Z",
        }
        try:
            with patch.object(hub_module.httpx, "AsyncClient", _FakeHubClient):
                report = self.client.post(
                    "/admin/sales-hub/usage-report",
                    headers={**headers, "Idempotency-Key": "idem-report-invalid-1"},
                )
        finally:
            _FakeHubClient.usage_response = None

        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json()["state"], "failed")
        self.assertEqual(report.json()["error"]["code"], "hub_invalid_response")
