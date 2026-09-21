# -*- coding: utf-8 -*-
"""Tests for the application-level AgentScope auth provider switch."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest import TestCase
from uuid import NAMESPACE_URL, uuid5

from fastapi import Depends
from fastapi.testclient import TestClient

from agentscope.app.deps import get_current_user_id
from examples.agent_service.identity.models import (
    LogtoPrincipal,
    TenantIdentity,
)

SERVICE_DIR = Path(__file__).parents[1] / "examples" / "agent_service"
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


class _Verifier:
    async def verify(self, token: str) -> LogtoPrincipal:
        return LogtoPrincipal(
            subject="logto-user-1",
            organization_id=token,
            scopes=frozenset({"tasks:read"}),
        )


class _Repository:
    async def resolve(self, principal) -> TenantIdentity:
        tenant_id = uuid5(
            NAMESPACE_URL,
            f"test:tenant:{principal.external_org_id}",
        )
        user_id = uuid5(
            NAMESPACE_URL,
            f"test:user:{principal.external_user_id}",
        )
        membership_id = uuid5(
            NAMESPACE_URL,
            f"test:membership:{tenant_id}:{user_id}",
        )
        return TenantIdentity(
            tenant_id=tenant_id,
            user_id=user_id,
            membership_id=membership_id,
            identity_provider=principal.identity_provider,
            external_org_id=principal.external_org_id,
            external_user_id=principal.external_user_id,
            role="member",
            status="active",
        )


class MainAuthProviderTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._previous_provider = os.environ.get("LXSCOPE_AUTH_PROVIDER")
        os.environ["LXSCOPE_AUTH_PROVIDER"] = "logto"
        sys.modules.pop("main", None)
        import main

        cls.main = main
        cls.main.app.state.logto_verifier = _Verifier()
        cls.main.app.state.tenant_binding_repository = _Repository()

        @cls.main.app.get("/__test_agent_scope_user_id")
        async def agent_scope_user_id(
            user_id: str = Depends(get_current_user_id),
        ) -> dict[str, str]:
            return {"user_id": user_id}

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._previous_provider is None:
            os.environ.pop("LXSCOPE_AUTH_PROVIDER", None)
        else:
            os.environ["LXSCOPE_AUTH_PROVIDER"] = cls._previous_provider

    def setUp(self) -> None:
        self.client = TestClient(self.main.app)

    def tearDown(self) -> None:
        self.client.close()

    def _user_id(self, organization_id: str) -> str:
        response = self.client.get(
            "/__test_agent_scope_user_id",
            headers={"Authorization": f"Bearer {organization_id}"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["user_id"]

    def test_same_logto_user_in_different_orgs_gets_different_user_ids(
        self,
    ) -> None:
        org_a_user_id = self._user_id("org-a")
        org_b_user_id = self._user_id("org-b")

        self.assertNotEqual(org_a_user_id, org_b_user_id)

    def test_same_logto_user_and_org_keeps_stable_user_id(self) -> None:
        first = self._user_id("org-a")
        second = self._user_id("org-a")

        self.assertEqual(first, second)

    def test_local_auth_public_api_remains_compatible(self) -> None:
        from auth import JWTAuthService

        auth = JWTAuthService(
            {"alice": ("local-alice", "password")},
            "a" * 32,
        )
        user = asyncio.run(auth.authenticate("alice", "password"))
        token, _ = auth.issue_access_token(user)

        user_id = asyncio.run(
            auth.get_current_user_id(f"Bearer {token}"),
        )

        self.assertEqual(user_id, "local-alice")
