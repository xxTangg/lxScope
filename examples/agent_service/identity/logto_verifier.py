# -*- coding: utf-8 -*-
"""Verification of Logto Resource Server access tokens."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError

from .models import LogtoPrincipal

_LOGTO_OIDC_PATH = "/oidc"
_LOGTO_JWKS_PATH = "/jwks"
# Logto OSS currently publishes an EC P-384 signing key (ES384) for this
# deployment. Keep RS256 for compatible installations that use RSA keys.
_SUPPORTED_ALGORITHMS = ("ES384", "RS256")


class LogtoTokenVerificationError(ValueError):
    """Raised when a token cannot be verified as a Logto access token."""

    code = "invalid_logto_token"


class LogtoOrganizationRequiredError(ValueError):
    """Raised when a valid token does not carry an organization_id claim."""

    code = "organization_required"


class LogtoVerifier:
    """Verify Logto Resource Server JWTs with the Logto JWKS endpoint."""

    def __init__(
        self,
        endpoint: str | None = None,
        api_resource: str | None = None,
        *,
        jwks_url: str | None = None,
        jwks_client: Any | None = None,
    ) -> None:
        normalized_endpoint = (
            endpoint or os.getenv("LOGTO_ENDPOINT", "")
        ).strip().rstrip("/")
        normalized_resource = (
            api_resource or os.getenv("LOGTO_API_RESOURCE", "")
        ).strip()
        if not normalized_endpoint:
            raise ValueError("LOGTO_ENDPOINT must be configured.")
        if not normalized_resource:
            raise ValueError("LOGTO_API_RESOURCE must be configured.")

        self.endpoint = normalized_endpoint
        self.api_resource = normalized_resource
        self.issuer = (
            normalized_endpoint
            if normalized_endpoint.endswith(_LOGTO_OIDC_PATH)
            else f"{normalized_endpoint}{_LOGTO_OIDC_PATH}"
        )
        self.jwks_url = (
            jwks_url or os.getenv("LOGTO_JWKS_URL", "")
        ).strip() or f"{self.issuer}{_LOGTO_JWKS_PATH}"
        self.userinfo_url = (
            os.getenv("LOGTO_USERINFO_URL", "")
        ).strip() or f"{self.issuer}/me"
        self._jwks_client = jwks_client or PyJWKClient(self.jwks_url)

    @classmethod
    def from_env(cls) -> "LogtoVerifier":
        """Create a verifier from the configured Logto environment."""

        return cls()

    async def verify(self, token: str) -> LogtoPrincipal:
        """Verify a token without blocking the async request event loop."""

        return await asyncio.to_thread(self.verify_sync, token)

    async def enrich_profile(
        self,
        principal: LogtoPrincipal,
        token: str,
    ) -> LogtoPrincipal:
        """Best-effortly attach the Logto user display profile.

        Resource-server access tokens commonly contain ``sub`` and tenant
        claims but omit profile claims.  The OIDC userinfo endpoint fills that
        presentation gap without affecting authorization: the JWT remains
        the only source of identity and permissions.
        """

        if principal.display_name or principal.username or principal.email:
            return principal
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    self.userinfo_url,
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                profile = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return principal
        if not isinstance(profile, dict):
            return principal

        username = profile.get("username") or profile.get("preferred_username")
        display_name = profile.get("name") or profile.get("display_name")
        email = profile.get("email")
        return LogtoPrincipal(
            subject=principal.subject,
            organization_id=principal.organization_id,
            scopes=principal.scopes,
            client_id=principal.client_id,
            organization_roles=principal.organization_roles,
            username=username if isinstance(username, str) and username.strip() else None,
            display_name=(
                display_name
                if isinstance(display_name, str) and display_name.strip()
                else None
            ),
            email=email if isinstance(email, str) and email.strip() else None,
        )

    def verify_sync(self, token: str) -> LogtoPrincipal:
        """Verify claims and signature using PyJWT and Logto JWKS."""

        if not isinstance(token, str) or not token.strip():
            raise LogtoTokenVerificationError("Bearer token is empty.")

        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=list(_SUPPORTED_ALGORITHMS),
                issuer=self.issuer,
                audience=self.api_resource,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": True,
                    "require": ["sub", "exp", "iss", "aud"],
                },
            )
        except (
            InvalidTokenError,
            PyJWKClientError,
            TypeError,
            ValueError,
        ) as exc:
            raise LogtoTokenVerificationError(
                "The Logto access token is invalid or expired.",
            ) from exc

        organization_id = claims.get("organization_id")
        if not isinstance(organization_id, str) or not organization_id.strip():
            raise LogtoOrganizationRequiredError(
                "The Logto access token must contain organization_id.",
            )

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise LogtoTokenVerificationError(
                "The Logto access token must contain a string subject.",
            )

        scope_claim = claims.get("scope", "")
        if isinstance(scope_claim, str):
            scopes = frozenset(scope_claim.split())
        elif isinstance(scope_claim, list):
            scopes = frozenset(
                value.strip()
                for value in scope_claim
                if isinstance(value, str) and value.strip()
            )
        else:
            scopes = frozenset()

        organization_roles_claim = claims.get("organization_roles", [])
        if isinstance(organization_roles_claim, str):
            organization_roles = frozenset(
                value.strip()
                for value in organization_roles_claim.split()
                if value.strip()
            )
        elif isinstance(organization_roles_claim, list):
            organization_roles = frozenset(
                value.strip()
                for value in organization_roles_claim
                if isinstance(value, str) and value.strip()
            )
        else:
            organization_roles = frozenset()

        client_id = claims.get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            client_id = None

        username = claims.get("username") or claims.get("preferred_username")
        if not isinstance(username, str) or not username.strip():
            username = None
        display_name = claims.get("name") or claims.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = None
        email = claims.get("email")
        if not isinstance(email, str) or not email.strip():
            email = None

        return LogtoPrincipal(
            subject=subject,
            organization_id=organization_id,
            scopes=scopes,
            organization_roles=organization_roles,
            client_id=client_id,
            username=username,
            display_name=display_name,
            email=email,
        )


__all__ = [
    "LogtoOrganizationRequiredError",
    "LogtoPrincipal",
    "LogtoTokenVerificationError",
    "LogtoVerifier",
]
