# -*- coding: utf-8 -*-
"""Phase 1 tests for identity context and permission boundaries."""

from __future__ import annotations

from unittest import TestCase
from uuid import uuid4

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from examples.agent_service.identity.context_api import auth_context_router
from examples.agent_service.identity.dependencies import (
    require_platform_permission,
    require_tenant_permission,
    get_tenant_identity,
)
from examples.agent_service.identity.models import (
    LogtoPrincipal,
    TenantIdentity,
)


class _Verifier:
    async def verify(self, token: str) -> LogtoPrincipal:
        scopes = {
            "platform": frozenset({"platform:upgrade"}),
        }.get(token, frozenset())
        organization_roles = {
            "tenant": frozenset({"org-1:admin"}),
            "wrong-org": frozenset({"other-org:admin"}),
        }.get(token, frozenset())
        return LogtoPrincipal(
            subject="user-1",
            organization_id="org-1",
            scopes=scopes,
            organization_roles=organization_roles,
        )


class _Repository:
    async def resolve(self, principal) -> TenantIdentity:
        return TenantIdentity(
            tenant_id=uuid4(),
            user_id=uuid4(),
            membership_id=uuid4(),
            identity_provider=principal.identity_provider,
            external_org_id=principal.external_org_id,
            external_user_id=principal.external_user_id,
            role="member",
            status="active",
        )


class _InactiveRepository:
    async def resolve(self, principal) -> TenantIdentity:
        return TenantIdentity(
            tenant_id=uuid4(),
            user_id=uuid4(),
            membership_id=uuid4(),
            identity_provider=principal.identity_provider,
            external_org_id=principal.external_org_id,
            external_user_id=principal.external_user_id,
            role="member",
            status="active",
            tenant_status="suspended",
        )


class Phase1IdentityContextTest(TestCase):
    def setUp(self) -> None:
        self.app = FastAPI()
        self.app.state.auth_provider = "logto"
        self.app.state.logto_verifier = _Verifier()
        self.app.state.tenant_binding_repository = _Repository()

        @self.app.get("/tenant")
        async def tenant_route(
            user=Depends(require_tenant_permission("manage")),
        ):
            return {"user_id": user.id}

        @self.app.get("/platform")
        async def platform_route(
            user=Depends(require_platform_permission("upgrade")),
        ):
            return {"user_id": user.id}

        @self.app.get("/identity")
        async def identity_route(identity: TenantIdentity = Depends(get_tenant_identity)):
            return {"membership_id": str(identity.membership_id)}

        self.app.include_router(auth_context_router)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_auth_context_uses_backend_permissions_and_membership_id(self) -> None:
        response = self.client.get(
            "/auth/context",
            headers={"Authorization": "Bearer tenant"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provider"], "logto")
        self.assertEqual(payload["permissions"], ["tenant:manage"])
        self.assertEqual(payload["user"]["id"], payload["membership_id"])

    def test_tenant_and_platform_permissions_are_separate(self) -> None:
        tenant = self.client.get(
            "/tenant",
            headers={"Authorization": "Bearer tenant"},
        )
        self.assertEqual(tenant.status_code, 200)

        denied = self.client.get(
            "/platform",
            headers={"Authorization": "Bearer tenant"},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"]["code"], "insufficient_permission")

        platform = self.client.get(
            "/platform",
            headers={"Authorization": "Bearer platform"},
        )
        self.assertEqual(platform.status_code, 200)

    def test_logto_organization_admin_maps_to_tenant_permission_only(self) -> None:
        response = self.client.get(
            "/auth/context",
            headers={"Authorization": "Bearer tenant"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("tenant:manage", payload["permissions"])
        self.assertNotIn("platform:manage", payload["permissions"])
        self.assertNotIn("platform:upgrade", payload["permissions"])

    def test_role_for_another_organization_does_not_grant_admin(self) -> None:
        response = self.client.get(
            "/auth/context",
            headers={"Authorization": "Bearer wrong-org"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["permissions"], [])
        self.assertEqual(payload["user"]["role"], "user")

    def test_inactive_identity_is_rejected_at_context_boundary(self) -> None:
        self.app.state.tenant_binding_repository = _InactiveRepository()

        response = self.client.get(
            "/auth/context",
            headers={"Authorization": "Bearer tenant"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"]["code"],
            "tenant_identity_inactive",
        )
