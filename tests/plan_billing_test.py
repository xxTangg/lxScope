# -*- coding: utf-8 -*-
"""Contract tests for the isolated Longxin plan-billing extension."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest import TestCase

from fastapi import FastAPI
from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).parents[1] / "examples" / "agent_service"
sys.path.insert(0, str(SERVICE_DIR))

from auth import JWTAuthService  # noqa: E402
from longxin_admin.plan_billing import PlanBillingService, plan_billing_router  # noqa: E402
from longxin_admin.plan_billing.models import CreatePlanOrderRequest  # noqa: E402


class _MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.values.get(key)

    async def set(self, key: str, value: Any, *, nx: bool = False) -> bool:
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
    def __init__(self, redis: _MemoryRedis) -> None:
        self._redis = redis

    def get_client(self) -> _MemoryRedis:
        return self._redis


class PlanBillingTest(TestCase):
    def setUp(self) -> None:
        self.redis = _MemoryRedis()
        self.storage = _Storage(self.redis)
        self.auth = JWTAuthService(
            {"admin": ("admin-id", "admin-password", "admin")},
            "a" * 32,
            storage=self.storage,
        )
        self.service = PlanBillingService(self.storage, self.auth)
        self.app = FastAPI()
        self.app.state.auth = self.auth
        self.app.state.plan_billing_service = self.service
        self.app.include_router(self.auth.router)
        self.app.include_router(plan_billing_router)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()

    def _login(self, username: str, password: str) -> dict[str, Any]:
        response = self.client.post(
            "/auth/login",
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_user_order_admin_approval_updates_plan_pool_and_ledger(self) -> None:
        system = {
            "system_id": "local-system",
            "pool_tokens": 500_000,
            "total_recharged": "0.00",
            "test_default_tokens": 0,
            "updated_at": "",
        }

        async def seed() -> None:
            await self.service._save_system(system)

        import asyncio

        asyncio.run(seed())
        registered = self.client.post(
            "/auth/register",
            json={"username": "alice", "password": "alice-password"},
        )
        self.assertEqual(registered.status_code, 201)
        member_headers = {
            "Authorization": f"Bearer {registered.json()['access_token']}",
        }
        admin_headers = {
            "Authorization": f"Bearer {self._login('admin', 'admin-password')['access_token']}",
        }

        catalog = self.client.get("/plans", headers=member_headers)
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual([item["id"] for item in catalog.json()["plans"]], [
            "plan_basic",
            "plan_pro",
            "plan_flagship",
        ])

        created = self.client.post(
            "/account/orders",
            headers={**member_headers, "Idempotency-Key": "order-1"},
            json={"plan_id": "plan_pro", "note": "升级团队额度"},
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["status"], "pending")
        order_id = created.json()["order_id"]

        approved = self.client.post(
            f"/admin/orders/{order_id}/approve",
            headers={**admin_headers, "Idempotency-Key": "approve-1"},
            json={"reason": "已确认付款"},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "approved")
        self.assertEqual(approved.json()["allocated_tokens"], 500_000)

        repeated = self.client.post(
            f"/admin/orders/{order_id}/approve",
            headers={**admin_headers, "Idempotency-Key": "approve-1"},
            json={"reason": "已确认付款"},
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["order_id"], order_id)

        current = self.client.get("/account/plan", headers=member_headers)
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["plan_id"], "plan_pro")
        self.assertEqual(current.json()["remaining_tokens"], 500_000)

        raw_system = self.redis.values["longxin:admin:v1:system"]
        self.assertEqual(json.loads(raw_system)["pool_tokens"], 0)
        self.assertEqual(len(self.redis.values["longxin:admin:v1:ledger"]), 1)

    def test_rejected_order_does_not_change_pool(self) -> None:
        import asyncio

        asyncio.run(
            self.service._save_system(
                {
                    "system_id": "local-system",
                    "pool_tokens": 100_000,
                    "total_recharged": "0.00",
                    "test_default_tokens": 0,
                    "updated_at": "",
                },
            ),
        )
        registered = self.client.post(
            "/auth/register",
            json={"username": "bob", "password": "bob-password"},
        )
        member_headers = {
            "Authorization": f"Bearer {registered.json()['access_token']}",
        }
        admin_headers = {
            "Authorization": f"Bearer {self._login('admin', 'admin-password')['access_token']}",
        }
        created = self.client.post(
            "/account/orders",
            headers={**member_headers, "Idempotency-Key": "order-2"},
            json={"plan_id": "plan_basic"},
        )
        order_id = created.json()["order_id"]
        rejected = self.client.post(
            f"/admin/orders/{order_id}/reject",
            headers={**admin_headers, "Idempotency-Key": "reject-1"},
            json={"reason": "资料尚未完成"},
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()["status"], "rejected")
        system = json.loads(self.redis.values["longxin:admin:v1:system"])
        self.assertEqual(system["pool_tokens"], 100_000)
        self.assertNotIn("longxin:admin:v1:ledger", self.redis.values)

    def test_registered_account_starts_without_a_plan(self) -> None:
        registered = self.client.post(
            "/auth/register",
            json={"username": "new-user", "password": "new-user-password"},
        )
        self.assertEqual(registered.status_code, 201)
        headers = {
            "Authorization": f"Bearer {registered.json()['access_token']}",
        }

        current = self.client.get("/account/plan", headers=headers)

        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["plan_id"], "plan_none")
        self.assertEqual(current.json()["monthly_quota"], 0)
        self.assertEqual(current.json()["remaining_tokens"], 0)
        self.assertEqual(current.json()["status"], "inactive")

    def test_existing_orders_use_the_current_account_username(self) -> None:
        import asyncio

        member = asyncio.run(self.auth.register("alice", "alice-password"))
        asyncio.run(
            self.service.create_order(
                member,
                CreatePlanOrderRequest(plan_id="plan_basic"),
                idempotency_key="username-display-order",
            ),
        )
        list_accounts = self.auth.list_accounts

        async def renamed_accounts() -> list[Any]:
            accounts = await list_accounts()
            return [
                account.model_copy(update={"username": "alice-from-logto"})
                if account.id == member.id
                else account
                for account in accounts
            ]

        self.auth.list_accounts = renamed_accounts
        result = asyncio.run(self.service.list_orders(order_status="pending"))

        self.assertEqual(len(result.orders), 1)
        self.assertEqual(result.orders[0].username, "alice-from-logto")

    def test_member_cannot_downgrade_after_activation(self) -> None:
        import asyncio

        asyncio.run(
            self.service._save_system(
                {
                    "system_id": "local-system",
                    "pool_tokens": 1_000_000,
                    "total_recharged": "0.00",
                    "test_default_tokens": 0,
                    "updated_at": "",
                },
            ),
        )
        registered = self.client.post(
            "/auth/register",
            json={"username": "upgrade-user", "password": "upgrade-password"},
        )
        member_headers = {
            "Authorization": f"Bearer {registered.json()['access_token']}",
        }
        admin_headers = {
            "Authorization": f"Bearer {self._login('admin', 'admin-password')['access_token']}",
        }
        created = self.client.post(
            "/account/orders",
            headers={**member_headers, "Idempotency-Key": "order-upgrade-user"},
            json={"plan_id": "plan_pro"},
        )
        self.assertEqual(created.status_code, 201)
        order_id = created.json()["order_id"]
        approved = self.client.post(
            f"/admin/orders/{order_id}/approve",
            headers={**admin_headers, "Idempotency-Key": "approve-upgrade-user"},
            json={"reason": "已确认申请"},
        )
        self.assertEqual(approved.status_code, 200)

        downgrade = self.client.post(
            "/account/orders",
            headers={**member_headers, "Idempotency-Key": "order-downgrade-user"},
            json={"plan_id": "plan_basic"},
        )

        self.assertEqual(downgrade.status_code, 409)
        self.assertEqual(downgrade.json()["detail"]["code"], "plan_upgrade_only")
