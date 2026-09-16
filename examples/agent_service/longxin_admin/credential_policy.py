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
