"""Tenant-aware adapters around AgentScope's existing StorageBase API.

The adapter is used only by the lxScope service. AgentScope's storage
implementation and its public contracts remain untouched.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Any

from .context import (
    current_identity,
    current_tenant_id,
    scoped_user_id,
    split_scoped_user_id,
)


class TenantRedisClient:
    """Prefix lxScope-owned Redis keys with the active Logto organization."""

    def __init__(self, client: Any, tenant_id: str) -> None:
        self._client = client
        digest = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:24]
        self._prefix = f"longxin:tenant:{digest}:"

    def _key(self, key: Any) -> Any:
        if not isinstance(key, str) or not key.startswith("longxin:"):
            return key
        return self._prefix + key.removeprefix("longxin:")

    async def get(self, key: str) -> Any:
        return await self._client.get(self._key(key))

    async def set(self, key: str, value: Any, *args: Any, **kwargs: Any) -> Any:
        return await self._client.set(self._key(key), value, *args, **kwargs)

    async def delete(self, *keys: str) -> Any:
        return await self._client.delete(*(self._key(key) for key in keys))

    async def rpush(self, key: str, *values: Any) -> Any:
        return await self._client.rpush(self._key(key), *values)

    async def lrange(self, key: str, start: int, end: int) -> Any:
        return await self._client.lrange(self._key(key), start, end)

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        rewritten = list(keys_and_args)
        for index in range(min(numkeys, len(rewritten))):
            rewritten[index] = self._key(rewritten[index])
        return await self._client.eval(script, numkeys, *rewritten)

    async def scan_iter(self, *, match: str | None = None, **kwargs: Any) -> AsyncIterator[str]:
        scoped_match = self._key(match) if match else None
        async for key in self._client.scan_iter(match=scoped_match, **kwargs):
            if isinstance(key, str):
                if key.startswith(self._prefix):
                    yield "longxin:" + key.removeprefix(self._prefix)
                elif key.startswith("longxin:tenant:"):
                    continue
                else:
                    yield key
            else:
                yield key

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class TenantScopedStorage:
    """Preserve per-user records and expose explicitly shared tenant records."""

    def __init__(self, storage: Any) -> None:
        self._storage = storage

    async def __aenter__(self) -> "TenantScopedStorage":
        await self._storage.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        await self._storage.__aexit__(exc_type, exc_value, traceback)

    async def aclose(self) -> None:
        await self._storage.aclose()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._storage, name)

    def get_client(self) -> Any:
        client = self._storage.get_client()
        tenant_id = current_tenant_id()
        if client is None or tenant_id is None:
            return client
        return TenantRedisClient(client, tenant_id)

    def get_base_client(self) -> Any:
        """Expose the unscoped client to authentication and core-key readers."""
        return self._storage.get_client()

    @staticmethod
    def _shared_scope(user_id: str) -> str | None:
        tenant_id = current_tenant_id()
        scoped_identity = split_scoped_user_id(user_id)

        # Chat continuations resumed by WakeupDispatcher run outside the
        # originating HTTP request, so request-local ContextVars are absent.
        # The queue carries the authenticated composite user id; use its
        # tenant component only when there is no active request tenant.
        if tenant_id is None and scoped_identity is not None:
            tenant_id = scoped_identity[0]

        # An active request context is authoritative. Never use a composite
        # id from another tenant to fall back across that boundary.
        if (
            tenant_id is None
            or user_id == tenant_id
            or (
                current_tenant_id() is not None
                and scoped_identity is not None
                and scoped_identity[0] != tenant_id
            )
        ):
            return None
        return tenant_id

    @staticmethod
    def _private_scope(user_id: str) -> str:
        """Normalize a request subject to the application composite user key."""
        identity = current_identity()
        tenant_id = getattr(identity, "tenant_id", None)
        subject_id = getattr(identity, "subject_id", None)
        if (
            isinstance(tenant_id, str)
            and isinstance(subject_id, str)
            and user_id in {subject_id, getattr(identity, "id", None)}
        ):
            return scoped_user_id(tenant_id, subject_id)
        return user_id

    @staticmethod
    def _can_manage_shared() -> bool:
        identity = current_identity()
        return getattr(identity, "role", None) == "admin"

    async def list_credentials(self, user_id: str) -> list[Any]:
        user_id = self._private_scope(user_id)
        records = await self._storage.list_credentials(user_id)
        shared_scope = self._shared_scope(user_id)
        if shared_scope is None:
            return records
        shared = await self._storage.list_credentials(shared_scope)
        return records + [item for item in shared if item.id not in {x.id for x in records}]

    async def get_credential(self, user_id: str, credential_id: str) -> Any:
        user_id = self._private_scope(user_id)
        record = await self._storage.get_credential(user_id, credential_id)
        if record is not None:
            return record
        shared_scope = self._shared_scope(user_id)
        if shared_scope is not None:
            return await self._storage.get_credential(shared_scope, credential_id)
        return None

    async def upsert_credential(self, user_id: str, credential_data: Any) -> str:
        user_id = self._private_scope(user_id)
        tenant_id = current_tenant_id()
        owner_id = tenant_id if tenant_id and self._can_manage_shared() else user_id
        return await self._storage.upsert_credential(owner_id, credential_data)

    async def upsert_tenant_credential(self, tenant_id: str, credential_data: Any) -> str:
        return await self._storage.upsert_credential(tenant_id, credential_data)

    async def delete_credential(self, user_id: str, credential_id: str) -> bool:
        user_id = self._private_scope(user_id)
        if await self._storage.get_credential(user_id, credential_id) is not None:
            return await self._storage.delete_credential(user_id, credential_id)
        shared_scope = self._shared_scope(user_id)
        if shared_scope is not None and self._can_manage_shared():
            return await self._storage.delete_credential(shared_scope, credential_id)
        return False

    async def list_skills(self, user_id: str) -> list[Any]:
        user_id = self._private_scope(user_id)
        records = await self._storage.list_skills(user_id)
        shared_scope = self._shared_scope(user_id)
        if shared_scope is None:
            return records
        shared = await self._storage.list_skills(shared_scope)
        return records + [item for item in shared if item.id not in {x.id for x in records}]

    async def get_skill(self, user_id: str, skill_id: str) -> Any:
        user_id = self._private_scope(user_id)
        record = await self._storage.get_skill(user_id, skill_id)
        if record is not None:
            return record
        shared_scope = self._shared_scope(user_id)
        return await self._storage.get_skill(shared_scope, skill_id) if shared_scope else None

    async def get_skill_by_name(self, user_id: str, name: str) -> Any:
        user_id = self._private_scope(user_id)
        record = await self._storage.get_skill_by_name(user_id, name)
        if record is not None:
            return record
        shared_scope = self._shared_scope(user_id)
        return await self._storage.get_skill_by_name(shared_scope, name) if shared_scope else None

    async def upsert_skill(self, user_id: str, skill_record: Any) -> str:
        user_id = self._private_scope(user_id)
        tenant_id = current_tenant_id()
        owner_id = tenant_id if tenant_id and self._can_manage_shared() else user_id
        return await self._storage.upsert_skill(owner_id, skill_record)

    async def delete_skill(self, user_id: str, skill_id: str) -> bool:
        user_id = self._private_scope(user_id)
        if await self._storage.get_skill(user_id, skill_id) is not None:
            return await self._storage.delete_skill(user_id, skill_id)
        shared_scope = self._shared_scope(user_id)
        if shared_scope is not None and self._can_manage_shared():
            return await self._storage.delete_skill(shared_scope, skill_id)
        return False

    async def list_mcps(self, user_id: str) -> list[Any]:
        user_id = self._private_scope(user_id)
        records = await self._storage.list_mcps(user_id)
        shared_scope = self._shared_scope(user_id)
        if shared_scope is None:
            return records
        shared = await self._storage.list_mcps(shared_scope)
        return records + [item for item in shared if item.id not in {x.id for x in records}]

    async def get_mcp(self, user_id: str, mcp_id: str) -> Any:
        user_id = self._private_scope(user_id)
        record = await self._storage.get_mcp(user_id, mcp_id)
        if record is not None:
            return record
        shared_scope = self._shared_scope(user_id)
        return await self._storage.get_mcp(shared_scope, mcp_id) if shared_scope else None

    async def get_mcp_by_name(self, user_id: str, name: str) -> Any:
        user_id = self._private_scope(user_id)
        record = await self._storage.get_mcp_by_name(user_id, name)
        if record is not None:
            return record
        shared_scope = self._shared_scope(user_id)
        return await self._storage.get_mcp_by_name(shared_scope, name) if shared_scope else None

    async def upsert_mcp(self, user_id: str, mcp_record: Any) -> str:
        user_id = self._private_scope(user_id)
        tenant_id = current_tenant_id()
        owner_id = tenant_id if tenant_id and self._can_manage_shared() else user_id
        return await self._storage.upsert_mcp(owner_id, mcp_record)

    async def delete_mcp(self, user_id: str, mcp_id: str) -> bool:
        user_id = self._private_scope(user_id)
        if await self._storage.get_mcp(user_id, mcp_id) is not None:
            return await self._storage.delete_mcp(user_id, mcp_id)
        shared_scope = self._shared_scope(user_id)
        if shared_scope is not None and self._can_manage_shared():
            return await self._storage.delete_mcp(shared_scope, mcp_id)
        return False
