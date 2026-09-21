# -*- coding: utf-8 -*-
"""Verification of Logto Resource Server access tokens."""

from __future__ import annotations

import asyncio
import os
from typing import Any

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
        self._jwks_client = jwks_client or PyJWKClient(self.jwks_url)

    @classmethod
    def from_env(cls) -> "LogtoVerifier":
        """Create a verifier from the configured Logto environment."""

        return cls()

    async def verify(self, token: str) -> LogtoPrincipal:
        """Verify a token without blocking the async request event loop."""

        return await asyncio.to_thread(self.verify_sync, token)

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

        client_id = claims.get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            client_id = None

        return LogtoPrincipal(
            subject=subject,
            organization_id=organization_id,
            scopes=scopes,
            client_id=client_id,
        )


__all__ = [
    "LogtoOrganizationRequiredError",
    "LogtoPrincipal",
    "LogtoTokenVerificationError",
    "LogtoVerifier",
]
