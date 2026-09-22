# -*- coding: utf-8 -*-
"""Data contracts for the external-to-internal identity binding layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


@dataclass(frozen=True, slots=True)
class IdentityPrincipal:
    """The external identity dimensions needed to resolve a tenant context."""

    external_org_id: str
    external_user_id: str
    identity_provider: str = "logto"
    username: str | None = None
    display_name: str | None = None
    email: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "external_org_id",
            _required_text(self.external_org_id, "external_org_id"),
        )
        object.__setattr__(
            self,
            "external_user_id",
            _required_text(self.external_user_id, "external_user_id"),
        )
        object.__setattr__(
            self,
            "identity_provider",
            _required_text(
                self.identity_provider,
                "identity_provider",
            ).lower(),
        )
        for field_name in ("username", "display_name", "email"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _required_text(value, field_name),
                )

    @classmethod
    def from_mapping(
        cls,
        claims: Mapping[str, Any],
        *,
        identity_provider: str = "logto",
    ) -> "IdentityPrincipal":
        """Build a principal from normalized claims or common Logto names.

        Token validation is handled by :class:`LogtoVerifier`; this helper only
        adapts already-trusted claim data to the repository input.
        """

        external_org_id = claims.get("external_org_id")
        if external_org_id is None:
            external_org_id = claims.get(
                "organization_id",
                claims.get("org_id"),
            )

        external_user_id = claims.get("external_user_id")
        if external_user_id is None:
            external_user_id = claims.get("sub")

        return cls(
            external_org_id=external_org_id,
            external_user_id=external_user_id,
            identity_provider=claims.get(
                "identity_provider",
                identity_provider,
            ),
            username=claims.get("username") or claims.get(
                "preferred_username",
            ),
            display_name=claims.get("display_name") or claims.get("name"),
            email=claims.get("email"),
        )


@dataclass(frozen=True, slots=True)
class LogtoPrincipal:
    """Validated Logto Resource Server token identity."""

    subject: str
    organization_id: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    client_id: str | None = None
    # Added after the legacy positional fields to keep constructor
    # compatibility for callers that supplied client_id positionally.
    organization_roles: frozenset[str] = field(default_factory=frozenset)
    username: str | None = None
    display_name: str | None = None
    email: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject",
            _required_text(self.subject, "subject"),
        )
        object.__setattr__(
            self,
            "organization_id",
            _required_text(self.organization_id, "organization_id"),
        )
        object.__setattr__(self, "scopes", frozenset(self.scopes))
        object.__setattr__(
            self,
            "organization_roles",
            frozenset(
                value.strip()
                for value in self.organization_roles
                if isinstance(value, str) and value.strip()
            ),
        )
        if self.client_id is not None:
            object.__setattr__(
                self,
                "client_id",
                _required_text(self.client_id, "client_id"),
            )
        for field_name in ("username", "display_name", "email"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _required_text(value, field_name),
                )


@dataclass(frozen=True, slots=True)
class TenantIdentity:
    """The internal identity context resolved for one external principal."""

    tenant_id: UUID
    user_id: UUID
    membership_id: UUID
    identity_provider: str
    external_org_id: str
    external_user_id: str
    role: str
    status: str
    display_name: str | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    tenant_status: str = "active"
    user_status: str = "active"


class TenantBindingError(RuntimeError):
    """Base error for failures while resolving an external tenant binding."""

    code = "tenant_binding_error"

    @property
    def error_code(self) -> str:
        """Compatibility alias for callers that use error_code terminology."""

        return self.code


class TenantNotProvisionedError(TenantBindingError):
    """Raised when an external organization has no pre-provisioned tenant."""

    code = "tenant_not_provisioned"

    def __init__(
        self,
        *,
        identity_provider: str,
        external_org_id: str,
    ) -> None:
        self.identity_provider = identity_provider
        self.external_org_id = external_org_id
        super().__init__(
            f"Tenant is not provisioned for {identity_provider} organization "
            f"{external_org_id!r} (tenant_not_provisioned).",
        )


class TenantIdentityStatusError(TenantBindingError):
    """Raised when a tenant identity dimension is no longer active."""

    code = "tenant_identity_inactive"

    def __init__(self, *, subject: str, status: str) -> None:
        self.subject = subject
        self.status = status
        super().__init__(
            f"The {subject} identity is not active (status={status!r}).",
        )


# These aliases keep the public contract readable for callers that refer to
# the input as an external or tenant principal.
ExternalPrincipal = IdentityPrincipal
TenantPrincipal = IdentityPrincipal


__all__ = [
    "ExternalPrincipal",
    "IdentityPrincipal",
    "LogtoPrincipal",
    "TenantBindingError",
    "TenantIdentity",
    "TenantIdentityStatusError",
    "TenantNotProvisionedError",
    "TenantPrincipal",
]
