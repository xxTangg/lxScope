"""Administrator-managed model credentials for the Longxin deployment.

The AgentScope core keeps resources owner-scoped by default. This adapter
turns administrator-owned credentials into a read/use-only model catalog for
members without exposing the underlying provider secret.
"""
from __future__ import annotations

from agentscope.app.access import (
    ResourceAccessPolicyBase,
    ResourceKind,
    ResourcePermission,
    ResourceRef,
)
from agentscope.app.storage import StorageBase
from auth import JWTAuthService
from identity.context import current_identity


class AdminManagedCredentialPolicy(ResourceAccessPolicyBase):
    """Expose all administrator credentials as read-only shared resources."""

    def __init__(self, auth: JWTAuthService) -> None:
        self._auth = auth

    async def _admin_ids(self) -> tuple[str, ...]:
        # ``list_accounts`` also sees Redis-backed accounts and keeps this
        # policy correct when the service is configured with more than one
        # administrator.
        return tuple(
            account.id
            for account in await self._auth.list_accounts()
            if account.role == "admin" and account.status == "active"
        )

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
        identity = current_identity()
        if (
            identity is not None
            and identity.id == viewer_id
            and identity.status == "active"
            and identity.tenant_id == owner_id
        ):
            return True
        return (
            viewer_id == owner_id
            and await self._auth.is_admin_user(viewer_id)
        )

    async def list_accessible(
        self,
        viewer_id: str,
        kind: ResourceKind,
        storage: StorageBase,
    ) -> list[ResourceRef]:
        if kind is not ResourceKind.CREDENTIAL:
            return []

        identity = current_identity()
        tenant_id = None
        if (
            identity is not None
            and identity.id == viewer_id
            and identity.status == "active"
        ):
            tenant_id = identity.tenant_id
        elif identity is None:
            # Queued chat resumes retain the authenticated tenant::subject
            # id, but execute in a dispatcher task without the HTTP
            # ContextVar. Restore only the tenant read scope from that stable
            # id so shared credentials remain resolvable on continuation.
            account = await self._auth._account_by_id(viewer_id)
            if account is not None and account.status == "active":
                tenant_id = account.tenant_id

        if tenant_id:
            return [
                ResourceRef(
                    kind=ResourceKind.CREDENTIAL,
                    owner_id=tenant_id,
                    resource_id=credential.id,
                    permission=ResourcePermission.READ,
                )
                for credential in await storage.list_credentials(tenant_id)
            ]

        refs: list[ResourceRef] = []
        for owner_id in await self._admin_ids():
            if owner_id == viewer_id:
                continue
            for credential in await storage.list_credentials(owner_id):
                refs.append(
                    ResourceRef(
                        kind=ResourceKind.CREDENTIAL,
                        owner_id=owner_id,
                        resource_id=credential.id,
                        permission=ResourcePermission.READ,
                    ),
                )
        return refs
