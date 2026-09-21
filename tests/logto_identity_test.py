# -*- coding: utf-8 -*-
"""Tests for Logto Resource Server token verification dependencies."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from examples.agent_service.identity.dependencies import (
    get_tenant_identity,
    require_scope,
)
from examples.agent_service.identity.logto_verifier import LogtoVerifier
from examples.agent_service.identity.models import TenantIdentity


class _StaticJWKClient:
    def __init__(self, public_key) -> None:
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        del token
        return SimpleNamespace(key=self._public_key)


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


class LogtoIdentityTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        cls.verifier = LogtoVerifier(
            "https://example.logto.app",
            "https://api.example.test",
            jwks_client=_StaticJWKClient(cls.private_key.public_key()),
        )

    def setUp(self) -> None:
        self.app = FastAPI()
        self.app.state.logto_verifier = self.verifier
        self.app.state.tenant_binding_repository = _Repository()

        @self.app.get("/identity")
        async def identity(
            tenant_identity: TenantIdentity = Depends(get_tenant_identity),
        ) -> dict[str, str]:
            return {
                "tenant_id": str(tenant_identity.tenant_id),
                "external_org_id": tenant_identity.external_org_id,
                "external_user_id": tenant_identity.external_user_id,
            }

        @self.app.get("/scoped")
        async def scoped(
            tenant_identity: TenantIdentity = Depends(
                require_scope("tasks:read"),
            ),
        ) -> dict[str, str]:
            return {"tenant_id": str(tenant_identity.tenant_id)}

        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()

    def _token(self, **overrides) -> str:
        now = datetime.now(timezone.utc)
        claims = {
            "sub": "logto-user-1",
            "organization_id": "logto-org-1",
            "scope": "tasks:read profile",
            "client_id": "client-1",
            "iss": self.verifier.issuer,
            "aud": self.verifier.api_resource,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        }
        claims.update(overrides)
        return jwt.encode(
            claims,
            self.private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    def test_no_token_returns_401(self) -> None:
        response = self.client.get("/identity")

        self.assertEqual(response.status_code, 401)

    def test_expired_token_returns_401(self) -> None:
        token = self._token(
            exp=datetime.now(timezone.utc) - timedelta(minutes=1),
        )

        response = self.client.get(
            "/identity",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 401)

    def test_wrong_issuer_returns_401(self) -> None:
        token = self._token(iss="https://wrong.example.test/oidc")

        response = self.client.get(
            "/identity",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 401)

    def test_wrong_audience_returns_401(self) -> None:
        token = self._token(aud="https://wrong.example.test")

        response = self.client.get(
            "/identity",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 401)

    def test_missing_organization_returns_403(self) -> None:
        token = self._token(organization_id=None)

        response = self.client.get(
            "/identity",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"]["code"],
            "organization_required",
        )

    def test_missing_scope_returns_403(self) -> None:
        token = self._token(scope="profile")

        response = self.client.get(
            "/scoped",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"]["code"],
            "insufficient_scope",
        )

    def test_correct_token_returns_tenant_identity(self) -> None:
        token = self._token()

        response = self.client.get(
            "/identity",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["external_org_id"], "logto-org-1")
        self.assertEqual(response.json()["external_user_id"], "logto-user-1")

    def test_es384_token_returns_tenant_identity(self) -> None:
        private_key = ec.generate_private_key(ec.SECP384R1())
        verifier = LogtoVerifier(
            "https://example.logto.app",
            "https://api.example.test",
            jwks_client=_StaticJWKClient(private_key.public_key()),
        )
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "sub": "logto-user-es384",
                "organization_id": "logto-org-1",
                "scope": "tasks:read",
                "iss": verifier.issuer,
                "aud": verifier.api_resource,
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            private_key,
            algorithm="ES384",
            headers={"kid": "test-key"},
        )

        principal = verifier.verify_sync(token)

        self.assertEqual(principal.subject, "logto-user-es384")
        self.assertEqual(principal.organization_id, "logto-org-1")
