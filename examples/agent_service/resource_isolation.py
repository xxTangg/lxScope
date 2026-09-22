# -*- coding: utf-8 -*-
"""Application-layer tenant isolation for Knowledge, Skill and MCP resources.

AgentScope keeps its public runtime ``user_id`` contract.  This module adds
the lxScope business boundary around that contract: a resource is resolved by
the verified tenant context first, and only then is the owner membership id
passed to AgentScope Core.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

try:
    from fastapi import HTTPException, status
except ModuleNotFoundError:  # pragma: no cover - lightweight policy tests
    class HTTPException(RuntimeError):
        def __init__(self, status_code: int, detail: Any) -> None:
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    class _Status:
        HTTP_403_FORBIDDEN = 403
        HTTP_404_NOT_FOUND = 404

    status = _Status()

try:
    from identity.dependencies import (
        get_bound_membership_id,
        get_bound_tenant_id,
        get_bound_tenant_identity,
    )
    from identity.permissions import TENANT_MANAGE, has_permission
except ModuleNotFoundError:  # pragma: no cover - package import mode
    try:
        from examples.agent_service.identity.dependencies import (
            get_bound_membership_id,
            get_bound_tenant_id,
            get_bound_tenant_identity,
        )
        from examples.agent_service.identity.permissions import (
            TENANT_MANAGE,
            has_permission,
        )
    except ModuleNotFoundError:
        TENANT_MANAGE = "tenant:manage"

        def get_bound_membership_id() -> None:
            return None

        def get_bound_tenant_id() -> None:
            return None

        def get_bound_tenant_identity() -> None:
            return None

        def has_permission(permissions: Iterable[str], required: str) -> bool:
            return required in set(permissions)

ResourceScope = Literal["platform", "tenant", "personal"]
ResourceKind = Literal["knowledge_base", "skill", "mcp"]
ResourceStatus = Literal["active", "disabled", "removed"]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def resource_is_visible(
    resource: dict[str, Any],
    *,
    tenant_id: str | None,
    membership_id: str | None,
    scope_field: str = "scope",
    visibility_field: str = "visibility",
) -> bool:
    """Return whether one resource belongs to the current application scope.

    ``visibility`` is intentionally separate from ``scope``.  Legacy
    publication records used ``scope`` for ``all/selected/none``; callers
    that read those records should pass ``scope_field='resource_scope'`` and
    ``visibility_field='scope'``.
    """

    if resource.get("status", "active") != "active":
        return False
    if resource.get("enabled", True) is False:
        return False

    scope = resource.get(scope_field, "tenant")
    if scope == "platform":
        visible = True
    elif scope == "tenant":
        visible = (
            tenant_id is not None
            and _text(resource.get("tenant_id")) == _text(tenant_id)
        )
    elif scope == "personal":
        visible = (
            membership_id is not None
            and _text(resource.get("owner_membership_id"))
            == _text(membership_id)
        )
    else:
        return False

    if not visible:
        return False
    visibility = resource.get(visibility_field, "all")
    if visibility == "none":
        return False
    if visibility == "selected":
        return membership_id is not None and membership_id in {
            str(value) for value in resource.get("user_ids", [])
        }
    return visibility == "all" or visibility is None


def filter_visible_resources(
    resources: Iterable[dict[str, Any]],
    *,
    tenant_id: str | None,
    membership_id: str | None,
    scope_field: str = "scope",
    visibility_field: str = "visibility",
) -> list[dict[str, Any]]:
    """Filter a resource catalog without ever broadening tenant scope."""

    return [
        resource
        for resource in resources
        if resource_is_visible(
            resource,
            tenant_id=tenant_id,
            membership_id=membership_id,
            scope_field=scope_field,
            visibility_field=visibility_field,
        )
    ]


@dataclass(frozen=True, slots=True)
class ResourceBinding:
    resource_type: str
    resource_id: str
    scope: ResourceScope
    tenant_id: str | None
    owner_membership_id: str | None
    visibility: str
    status: ResourceStatus


class TenantResourceRegistry:
    """Persist the business ownership of a core resource.

    The registry is deliberately separate from AgentScope Core tables.  Core
    can continue to store records under ``user_id`` while lxScope owns the
    verified ``tenant_id`` boundary and can evolve resource ownership without
    changing AgentScope storage interfaces.
    """

    def __init__(self, database: Any) -> None:
        self._engine = (
            database
            if hasattr(database, "sync_engine")
            else getattr(database, "engine", database)
        )
        self._table: Any | None = None

    def _table_definition(self) -> Any:
        if self._table is not None:
            return self._table
        from sqlalchemy import Column, DateTime, Index, MetaData, String, Table, Uuid, text

        schema = "longxin_app" if self._engine.dialect.name == "postgresql" else None
        metadata = MetaData(schema=schema)
        self._table = Table(
            "resource_registry",
            metadata,
            Column("resource_type", String(32), nullable=False),
            Column("resource_id", String(255), nullable=False),
            Column("scope", String(16), nullable=False),
            Column("tenant_id", Uuid(as_uuid=True)),
            Column("owner_membership_id", String(255)),
            Column("visibility", String(16), nullable=False, server_default=text("'all'")),
            Column("status", String(16), nullable=False, server_default=text("'active'")),
            Column("created_at", DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
            Column("updated_at", DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
            Index("ix_resource_registry_tenant", "tenant_id", "resource_type", "status"),
            Index("ix_resource_registry_owner", "owner_membership_id", "resource_type", "status"),
        )
        return self._table

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _uuid(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            return value

    async def register(
        self,
        resource_type: ResourceKind | str,
        resource_id: str,
        *,
        scope: ResourceScope,
        tenant_id: Any | None,
        owner_membership_id: Any | None,
        visibility: str = "all",
        status_value: ResourceStatus = "active",
    ) -> ResourceBinding:
        table = self._table_definition()
        from sqlalchemy import and_, select, update

        tenant_clause = (
            table.c.tenant_id.is_(None)
            if tenant_id is None
            else table.c.tenant_id == self._uuid(tenant_id)
        )
        owner_value = _text(owner_membership_id)
        owner_clause = (
            table.c.owner_membership_id.is_(None)
            if owner_value is None
            else table.c.owner_membership_id == owner_value
        )
        predicate = and_(
            table.c.resource_type == str(resource_type),
            table.c.resource_id == str(resource_id),
            table.c.scope == scope,
            tenant_clause,
            owner_clause,
        )
        values = {
            "resource_type": str(resource_type),
            "resource_id": str(resource_id),
            "scope": scope,
            "tenant_id": self._uuid(tenant_id),
            "owner_membership_id": owner_value,
            "visibility": visibility,
            "status": status_value,
            "updated_at": self._now(),
        }
        async with self._engine.begin() as connection:
            result = await connection.execute(select(table).where(predicate).limit(1))
            existing = result.mappings().first()
            if existing is None:
                values["created_at"] = self._now()
                await connection.execute(table.insert().values(**values))
            else:
                await connection.execute(update(table).where(predicate).values(**values))
        return ResourceBinding(
            resource_type=str(resource_type),
            resource_id=str(resource_id),
            scope=scope,
            tenant_id=_text(tenant_id),
            owner_membership_id=owner_value,
            visibility=visibility,
            status=status_value,
        )

    async def visible_bindings(
        self,
        resource_type: ResourceKind | str,
        *,
        tenant_id: Any | None,
        membership_id: Any | None,
    ) -> list[ResourceBinding]:
        table = self._table_definition()
        from sqlalchemy import and_, or_, select

        tenant_id_value = self._uuid(tenant_id)
        membership_value = _text(membership_id)
        tenant_visible = (
            and_(
                table.c.scope == "tenant",
                table.c.tenant_id == tenant_id_value,
            )
            if tenant_id_value is not None
            else False
        )
        personal_visible = (
            and_(
                table.c.scope == "personal",
                table.c.owner_membership_id == membership_value,
            )
            if membership_value is not None
            else False
        )

        async with self._engine.connect() as connection:
            result = await connection.execute(
                select(table).where(
                    table.c.resource_type == str(resource_type),
                    table.c.status == "active",
                    or_(
                        table.c.scope == "platform",
                        tenant_visible,
                        personal_visible,
                    ),
                ),
            )
            rows = [dict(row) for row in result.mappings().all()]
        visible = filter_visible_resources(
            rows,
            tenant_id=_text(tenant_id),
            membership_id=_text(membership_id),
        )
        return [
            ResourceBinding(
                resource_type=str(row["resource_type"]),
                resource_id=str(row["resource_id"]),
                scope=row["scope"],
                tenant_id=_text(row.get("tenant_id")),
                owner_membership_id=_text(row.get("owner_membership_id")),
                visibility=str(row.get("visibility") or "all"),
                status=row.get("status", "active"),
            )
            for row in visible
        ]

    async def get_visible(
        self,
        resource_type: ResourceKind | str,
        resource_id: str,
        *,
        tenant_id: Any | None,
        membership_id: Any | None,
    ) -> ResourceBinding | None:
        bindings = await self.visible_bindings(
            resource_type,
            tenant_id=tenant_id,
            membership_id=membership_id,
        )
        return next(
            (binding for binding in bindings if binding.resource_id == str(resource_id)),
            None,
        )

    async def remove(
        self,
        resource_type: ResourceKind | str,
        resource_id: str,
        *,
        tenant_id: Any | None,
    ) -> None:
        table = self._table_definition()
        from sqlalchemy import update

        async with self._engine.begin() as connection:
            await connection.execute(
                update(table)
                .where(
                    table.c.resource_type == str(resource_type),
                    table.c.resource_id == str(resource_id),
                    table.c.tenant_id == self._uuid(tenant_id),
                )
                .values(status="removed", updated_at=self._now()),
            )


def _not_found(resource: str, resource_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{resource} {resource_id!r} is not visible in the current tenant.",
    )


class TenantScopedKnowledgeBaseService:
    """Guard the core KnowledgeBaseService with the current tenant registry."""

    def __init__(self, delegate: Any, registry: TenantResourceRegistry | None) -> None:
        self._delegate = delegate
        self._registry = registry

    @property
    def delegate(self) -> Any:
        return self._delegate

    def _tenant_context(self) -> tuple[Any | None, str | None]:
        return (
            get_bound_tenant_id(),
            _text(get_bound_membership_id()),
        )

    def _is_tenant_request(self) -> bool:
        return get_bound_tenant_id() is not None and self._registry is not None

    async def _binding(self, knowledge_base_id: str, user_id: str) -> ResourceBinding | None:
        if not self._is_tenant_request():
            return None
        tenant_id, membership_id = self._tenant_context()
        binding = await self._registry.get_visible(
            "knowledge_base",
            knowledge_base_id,
            tenant_id=tenant_id,
            membership_id=membership_id,
        )
        if binding is not None:
            return binding

        # Register legacy records only after Core has proved that the current
        # membership owns them. This never imports another user's record.
        views, _ = await self._delegate.list_knowledge_base_views(
            user_id,
            knowledge_base_id=knowledge_base_id,
            page=1,
            page_size=1,
        )
        if views and str(views[0].owner_id) == str(user_id):
            return await self._registry.register(
                "knowledge_base",
                knowledge_base_id,
                scope="tenant",
                tenant_id=tenant_id,
                owner_membership_id=user_id,
            )
        raise _not_found("Knowledge base", knowledge_base_id)

    async def _owner_for_read(self, knowledge_base_id: str, user_id: str) -> str:
        binding = await self._binding(knowledge_base_id, user_id)
        return binding.owner_membership_id if binding and binding.owner_membership_id else user_id

    async def _owner_for_edit(self, knowledge_base_id: str, user_id: str) -> str:
        owner_id = await self._owner_for_read(knowledge_base_id, user_id)
        identity = get_bound_tenant_identity()
        if owner_id != str(user_id) and not (
            identity is not None and has_permission(identity.permissions, TENANT_MANAGE)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The current membership cannot modify this resource.",
            )
        return owner_id

    async def create_knowledge_base(self, user_id: str, *args: Any, **kwargs: Any) -> Any:
        record = await self._delegate.create_knowledge_base(user_id, *args, **kwargs)
        if self._is_tenant_request():
            tenant_id, _ = self._tenant_context()
            await self._registry.register(
                "knowledge_base",
                record.id,
                scope="tenant",
                tenant_id=tenant_id,
                owner_membership_id=user_id,
            )
        return record

    async def list_knowledge_bases(self, user_id: str) -> list[Any]:
        if not self._is_tenant_request():
            return await self._delegate.list_knowledge_bases(user_id)
        views, _ = await self.list_knowledge_base_views(user_id, page_size=10000)
        return views

    async def list_knowledge_base_views(self, user_id: str, **kwargs: Any) -> tuple[list[Any], int]:
        if not self._is_tenant_request():
            return await self._delegate.list_knowledge_base_views(user_id, **kwargs)

        tenant_id, membership_id = self._tenant_context()
        bindings = await self._registry.visible_bindings(
            "knowledge_base",
            tenant_id=tenant_id,
            membership_id=membership_id,
        )
        # Migrate the caller's own legacy records into the explicit registry.
        own_views, _ = await self._delegate.list_knowledge_base_views(
            user_id,
            page=1,
            page_size=10000,
        )
        known = {binding.resource_id for binding in bindings}
        for view in own_views:
            if view.id not in known and str(view.owner_id) == str(user_id):
                await self._registry.register(
                    "knowledge_base",
                    view.id,
                    scope="tenant",
                    tenant_id=tenant_id,
                    owner_membership_id=user_id,
                )
        bindings = await self._registry.visible_bindings(
            "knowledge_base",
            tenant_id=tenant_id,
            membership_id=membership_id,
        )

        views: list[Any] = []
        for binding in bindings:
            if not binding.owner_membership_id:
                continue
            owner_views, _ = await self._delegate.list_knowledge_base_views(
                binding.owner_membership_id,
                knowledge_base_id=binding.resource_id,
                page=1,
                page_size=1,
            )
            if owner_views:
                views.append(owner_views[0])

        knowledge_base_id = kwargs.get("knowledge_base_id")
        name = kwargs.get("name")
        if knowledge_base_id is not None:
            views = [view for view in views if view.id == knowledge_base_id]
        if name is not None:
            needle = name.lower()
            views = [view for view in views if needle in view.name.lower()]
        orderby = kwargs.get("orderby", "create_time")
        desc = kwargs.get("desc", True)
        sort_key = "updated_at" if orderby == "update_time" else "created_at"
        views.sort(key=lambda view: (getattr(view, sort_key), view.id), reverse=desc)
        page = kwargs.get("page", 1)
        page_size = kwargs.get("page_size", 30)
        total = len(views)
        return views[(page - 1) * page_size : page * page_size], total

    async def update_knowledge_base(self, user_id: str, knowledge_base_id: str, *args: Any, **kwargs: Any) -> Any:
        owner_id = await self._owner_for_edit(knowledge_base_id, user_id)
        return await self._delegate.update_knowledge_base(owner_id, knowledge_base_id, *args, **kwargs)

    async def delete_knowledge_base(self, user_id: str, knowledge_base_id: str) -> None:
        owner_id = await self._owner_for_edit(knowledge_base_id, user_id)
        await self._delegate.delete_knowledge_base(owner_id, knowledge_base_id)
        if self._is_tenant_request():
            await self._registry.remove(
                "knowledge_base",
                knowledge_base_id,
                tenant_id=get_bound_tenant_id(),
            )

    async def register_document(self, user_id: str, knowledge_base_id: str, *args: Any, **kwargs: Any) -> Any:
        owner_id = await self._owner_for_edit(knowledge_base_id, user_id)
        return await self._delegate.register_document(owner_id, knowledge_base_id, *args, **kwargs)

    async def list_documents(self, user_id: str, knowledge_base_id: str, *args: Any, **kwargs: Any) -> Any:
        owner_id = await self._owner_for_read(knowledge_base_id, user_id)
        return await self._delegate.list_documents(owner_id, knowledge_base_id, *args, **kwargs)

    async def get_document_status(self, user_id: str, knowledge_base_id: str, *args: Any, **kwargs: Any) -> Any:
        owner_id = await self._owner_for_read(knowledge_base_id, user_id)
        return await self._delegate.get_document_status(owner_id, knowledge_base_id, *args, **kwargs)

    async def get_document(self, user_id: str, knowledge_base_id: str, *args: Any, **kwargs: Any) -> Any:
        owner_id = await self._owner_for_read(knowledge_base_id, user_id)
        return await self._delegate.get_document(owner_id, knowledge_base_id, *args, **kwargs)

    async def list_document_chunks(self, user_id: str, knowledge_base_id: str, *args: Any, **kwargs: Any) -> Any:
        owner_id = await self._owner_for_read(knowledge_base_id, user_id)
        return await self._delegate.list_document_chunks(owner_id, knowledge_base_id, *args, **kwargs)

    async def stream_document_content(self, user_id: str, knowledge_base_id: str, *args: Any, **kwargs: Any) -> Any:
        owner_id = await self._owner_for_read(knowledge_base_id, user_id)
        return await self._delegate.stream_document_content(owner_id, knowledge_base_id, *args, **kwargs)

    async def delete_document(self, user_id: str, knowledge_base_id: str, *args: Any, **kwargs: Any) -> None:
        owner_id = await self._owner_for_edit(knowledge_base_id, user_id)
        await self._delegate.delete_document(owner_id, knowledge_base_id, *args, **kwargs)

    async def get_knowledge_graph(self, user_id: str, knowledge_base_id: str, *args: Any, **kwargs: Any) -> Any:
        owner_id = await self._owner_for_read(knowledge_base_id, user_id)
        return await self._delegate.get_knowledge_graph(owner_id, knowledge_base_id, *args, **kwargs)

    async def rebuild_knowledge_graph(self, user_id: str, knowledge_base_id: str) -> Any:
        owner_id = await self._owner_for_edit(knowledge_base_id, user_id)
        return await self._delegate.rebuild_knowledge_graph(owner_id, knowledge_base_id)

    async def search(self, user_id: str, knowledge_base_id: str, *args: Any, **kwargs: Any) -> Any:
        owner_id = await self._owner_for_read(knowledge_base_id, user_id)
        return await self._delegate.search(owner_id, knowledge_base_id, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class TenantScopedResourceAccessService:
    """Application proxy for runtime resource resolution."""

    def __init__(self, delegate: Any, registry: TenantResourceRegistry | None) -> None:
        self._delegate = delegate
        self._registry = registry

    async def resolve_knowledge_base(self, user_id: str, knowledge_base_id: str) -> Any:
        if self._registry is None or get_bound_tenant_id() is None:
            return await self._delegate.resolve_knowledge_base(user_id, knowledge_base_id)
        binding = await self._registry.get_visible(
            "knowledge_base",
            knowledge_base_id,
            tenant_id=get_bound_tenant_id(),
            membership_id=get_bound_membership_id(),
        )
        if binding is None or not binding.owner_membership_id:
            # Legacy records may predate Phase 3. Core can prove an owner
            # read using the current membership; only that exact owner read
            # may be promoted into the current tenant registry.
            record = await self._delegate.resolve_knowledge_base(
                user_id,
                knowledge_base_id,
            )
            await self._registry.register(
                "knowledge_base",
                knowledge_base_id,
                scope="tenant",
                tenant_id=get_bound_tenant_id(),
                owner_membership_id=getattr(record, "user_id", user_id),
            )
            return record
        return await self._delegate.resolve_knowledge_base(
            binding.owner_membership_id,
            knowledge_base_id,
        )

    async def resolve_for_edit(self, user_id: str, kind: Any, resource_id: str) -> Any:
        if (
            self._registry is not None
            and get_bound_tenant_id() is not None
            and str(getattr(kind, "value", kind)) == "knowledge_base"
        ):
            binding = await self._registry.get_visible(
                "knowledge_base",
                resource_id,
                tenant_id=get_bound_tenant_id(),
                membership_id=get_bound_membership_id(),
            )
            if binding is None or not binding.owner_membership_id:
                raise _not_found("Knowledge base", resource_id)
            identity = get_bound_tenant_identity()
            if binding.owner_membership_id != str(user_id) and not (
                identity is not None and has_permission(identity.permissions, TENANT_MANAGE)
            ):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Resource edit permission required.")
            return await self._delegate.resolve_for_edit(
                binding.owner_membership_id,
                kind,
                resource_id,
            )
        return await self._delegate.resolve_for_edit(user_id, kind, resource_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


__all__ = [
    "ResourceBinding",
    "ResourceKind",
    "ResourceScope",
    "TenantResourceRegistry",
    "TenantScopedKnowledgeBaseService",
    "TenantScopedResourceAccessService",
    "filter_visible_resources",
    "resource_is_visible",
]
