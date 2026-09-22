"""Administrator-managed model credentials for the Longxin deployment.

The AgentScope core keeps resources owner-scoped by default. This adapter
turns administrator-owned credentials into a read/use-only model catalog for
members without exposing the underlying provider secret. The application
chooses one credential boundary per deployment: ``tenant`` or ``platform``.
The policy never merges those two catalogs.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Literal

from agentscope.app.access import (
    ResourceAccessPolicyBase,
    ResourceKind,
    ResourcePermission,
    ResourceRef,
)
from agentscope.app.storage import StorageBase
from auth import JWTAuthService
from identity.dependencies import get_bound_tenant_id, get_bound_tenant_identity
from identity.permissions import (
    TENANT_MANAGE,
    has_permission,
    is_platform_admin,
)


CredentialScope = Literal["platform", "tenant"]
PLATFORM_CREDENTIAL_OWNER = "__lxscope_platform__"


def configured_credential_scope() -> CredentialScope:
    """Return the single credential boundary configured for this service."""

    value = os.getenv("LONGXIN_CREDENTIAL_SCOPE", "tenant").strip().lower()
    if value not in {"platform", "tenant"}:
        raise ValueError("LONGXIN_CREDENTIAL_SCOPE must be 'platform' or 'tenant'")
    return value  # type: ignore[return-value]


async def credential_manager_allowed(
    auth: JWTAuthService,
    user_id: str,
    *,
    scope: CredentialScope | None = None,
) -> bool:
    """Check credential management at the same identity boundary as reads."""

    selected_scope = scope or configured_credential_scope()
    identity = get_bound_tenant_identity()
    if identity is not None:
        if selected_scope == "platform":
            return is_platform_admin(identity.permissions)
        return has_permission(identity.permissions, TENANT_MANAGE) or is_platform_admin(
            identity.permissions,
        )
    # Local-auth deployments have no external tenant context.  Their admin
    # account is the platform boundary and therefore may manage both modes.
    return await auth.is_admin_user(user_id)


class AdminManagedCredentialPolicy(ResourceAccessPolicyBase):
    """Expose only credentials owned by the selected boundary."""

    def __init__(
        self,
        auth: JWTAuthService,
        *,
        tenant_member_provider: Callable[[], Any] | None = None,
        scope: CredentialScope | None = None,
    ) -> None:
        self._auth = auth
        self._tenant_member_provider = tenant_member_provider
        self._scope = scope or configured_credential_scope()

    async def _admin_ids(self) -> tuple[str, ...]:
        # ``list_accounts`` also sees Redis-backed accounts and keeps this
        # policy correct when the service is configured with more than one
        # administrator.
        return tuple(
            account.id
            for account in await self._auth.list_accounts()
            if account.role == "admin" and account.status == "active"
        )

    async def _tenant_admin_ids(self) -> tuple[str, ...]:
        """Return administrator memberships from the verified tenant only."""

        tenant_id = get_bound_tenant_id()
        provider = self._tenant_member_provider() if self._tenant_member_provider else None
        if tenant_id is None or provider is None:
            return ()
        rows = await provider.list_members(tenant_id)
        return tuple(
            str(row["membership_id"])
            for row in rows
            if has_permission(row.get("permissions", ()), TENANT_MANAGE)
            and str(row.get("membership_status") or "active") == "active"
            and str(row.get("user_status") or "active") == "active"
        )

    async def can_manage(self, user_id: str) -> bool:
        """Compatibility checker used by AgentScope's credential router."""

        return await credential_manager_allowed(
            self._auth,
            user_id,
            scope=self._scope,
        )

    async def _owner_ids(self) -> tuple[str, ...]:
        if self._scope == "platform":
            # Local administrator-owned records are the legacy platform pool.
            # A deployment that provisions a dedicated platform owner can use
            # the stable sentinel below without making it a tenant member.
            return tuple(dict.fromkeys((*await self._admin_ids(), PLATFORM_CREDENTIAL_OWNER)))
        if get_bound_tenant_id() is None:
            return await self._admin_ids()
        return await self._tenant_admin_ids()

    async def can_read_owned(
        self,
        viewer_id: str,
        kind: ResourceKind,
        owner_id: str,
        storage: StorageBase,
    ) -> bool:
        del storage
        if kind is not ResourceKind.CREDENTIAL:
            return True
        if viewer_id != owner_id:
            return False
        return viewer_id in await self._owner_ids() and await self.can_manage(viewer_id)

    async def list_accessible(
        self,
        viewer_id: str,
        kind: ResourceKind,
        storage: StorageBase,
    ) -> list[ResourceRef]:
        if kind is not ResourceKind.CREDENTIAL:
            return []

        refs: list[ResourceRef] = []
        owner_ids = await self._owner_ids()
        for owner_id in owner_ids:
            if owner_id == viewer_id:
                continue
            for credential in await storage.list_credentials(owner_id):
                # Scope is selected at deployment level.  The optional
                # metadata check protects against a migration accidentally
                # leaving a mixed catalog behind.
                record_scope = credential.data.get("lxscope_scope")
                if record_scope is not None and record_scope != self._scope:
                    continue
                refs.append(
                    ResourceRef(
                        kind=ResourceKind.CREDENTIAL,
                        owner_id=owner_id,
                        resource_id=credential.id,
                        permission=ResourcePermission.READ,
                    ),
                )
        return refs
