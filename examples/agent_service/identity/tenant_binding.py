# -*- coding: utf-8 -*-
"""Repository for binding trusted external principals to lxScope identities."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .models import (
    IdentityPrincipal,
    TenantIdentity,
    TenantIdentityStatusError,
    TenantNotProvisionedError,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TenantBindingRepository:
    """Resolve external organization/user IDs through the app database.

    ``database`` is the existing :class:`ApplicationDatabase` (or its
    initialized SQLAlchemy engine).  The repository owns no engine lifecycle
    and never creates a tenant when an external organization is unknown.
    """

    def __init__(
        self,
        database: Any,
        *,
        identity_provider: str = "logto",
    ) -> None:
        provider = identity_provider.strip().lower()
        if not provider:
            raise ValueError("identity_provider must be a non-empty string")
        self._database = database
        # AsyncEngine exposes ``engine`` as a synchronous proxy, so detect it
        # before unwrapping ApplicationDatabase.engine.
        self._engine = (
            database
            if hasattr(database, "sync_engine")
            else getattr(database, "engine", database)
        )
        self._identity_provider = provider
        self._tables: dict[str, Any] | None = None

    def _table_definitions(self) -> dict[str, Any]:
        if self._tables is not None:
            return self._tables

        from sqlalchemy import (
            Column,
            DateTime,
            Index,
            MetaData,
            String,
            Table,
            Uuid,
            UniqueConstraint,
            text,
        )

        schema = (
            "longxin_app"
            if self._engine.dialect.name == "postgresql"
            else None
        )
        metadata = MetaData(schema=schema)
        uuid_type = Uuid(as_uuid=True)
        self._tables = {
            "tenants": Table(
                "tenants",
                metadata,
                Column("id", uuid_type, primary_key=True),
                Column("code", String(64), nullable=False),
                Column("name", String(128), nullable=False),
                Column(
                    "status",
                    String(32),
                    nullable=False,
                    server_default=text("'active'"),
                ),
                Column(
                    "identity_provider",
                    String(32),
                    nullable=False,
                    server_default=text("'local'"),
                ),
                Column("external_org_id", String(255)),
                Column(
                    "created_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                Column(
                    "updated_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                Index(
                    "uq_tenants_identity_provider_external_org",
                    "identity_provider",
                    "external_org_id",
                    unique=True,
                    postgresql_where=text("external_org_id IS NOT NULL"),
                    sqlite_where=text("external_org_id IS NOT NULL"),
                ),
            ),
            "users": Table(
                "users",
                metadata,
                Column("id", uuid_type, primary_key=True),
                Column("username", String(128), nullable=False),
                Column("external_user_id", String(255)),
                Column(
                    "email",
                    String(255),
                ),
                Column("password_hash", String),
                Column(
                    "status",
                    String(32),
                    nullable=False,
                    server_default=text("'active'"),
                ),
                Column(
                    "system_role",
                    String(32),
                    nullable=False,
                    server_default=text("'user'"),
                ),
                Column(
                    "created_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                Column(
                    "updated_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                UniqueConstraint(
                    "external_user_id",
                    name="uq_users_external_user_id",
                ),
            ),
            "memberships": Table(
                "tenant_memberships",
                metadata,
                Column("id", uuid_type, primary_key=True),
                Column("tenant_id", uuid_type, nullable=False),
                Column("user_id", uuid_type, nullable=False),
                Column(
                    "role",
                    String(32),
                    nullable=False,
                    server_default=text("'member'"),
                ),
                Column(
                    "status",
                    String(32),
                    nullable=False,
                    server_default=text("'active'"),
                ),
                Column("display_name", String(128)),
                Column(
                    "joined_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                Column(
                    "created_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                Column(
                    "updated_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                UniqueConstraint(
                    "tenant_id",
                    "user_id",
                    name="uq_tenant_memberships_tenant_user",
                ),
            ),
            "role_permissions": Table(
                "role_permission_mapping",
                metadata,
                Column("role", String(32), nullable=False),
                Column("permission", String(128), nullable=False),
                UniqueConstraint(
                    "role",
                    "permission",
                    name="uq_role_permission_mapping_role_permission",
                ),
            ),
        }
        return self._tables

    @staticmethod
    def _insert_ignore_conflicts(
        table: Any,
        values: dict[str, Any],
        connection: Any,
    ) -> Any:
        """Build a dialect-aware idempotent insert using bound values."""

        dialect_name = connection.dialect.name
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy import insert

        statement = insert(table).values(**values)
        if hasattr(statement, "on_conflict_do_nothing"):
            statement = statement.on_conflict_do_nothing()
        return statement

    @staticmethod
    def _principal(
        principal: IdentityPrincipal | Mapping[str, Any],
        *,
        default_provider: str,
    ) -> IdentityPrincipal:
        if isinstance(principal, IdentityPrincipal):
            return principal
        if isinstance(principal, Mapping):
            return IdentityPrincipal.from_mapping(
                principal,
                identity_provider=default_provider,
            )
        raise TypeError("principal must be an IdentityPrincipal or mapping")

    async def resolve(
        self,
        principal: IdentityPrincipal | Mapping[str, Any],
    ) -> TenantIdentity:
        """Resolve a principal to internal tenant, user, and membership IDs."""

        normalized = self._principal(
            principal,
            default_provider=self._identity_provider,
        )
        tables = self._table_definitions()
        tenants = tables["tenants"]
        users = tables["users"]
        memberships = tables["memberships"]
        role_permissions = tables["role_permissions"]

        from sqlalchemy import select, update

        async with self._engine.begin() as connection:
            tenant_result = await connection.execute(
                select(
                    tenants.c.id,
                    tenants.c.identity_provider,
                    tenants.c.external_org_id,
                    tenants.c.status,
                ).where(
                    tenants.c.identity_provider
                    == normalized.identity_provider,
                    tenants.c.external_org_id == normalized.external_org_id,
                ),
            )
            tenant_row = tenant_result.mappings().first()
            if tenant_row is None:
                raise TenantNotProvisionedError(
                    identity_provider=normalized.identity_provider,
                    external_org_id=normalized.external_org_id,
                )
            if tenant_row["status"] != "active":
                raise TenantIdentityStatusError(
                    subject="tenant",
                    status=tenant_row["status"],
                )

            user_id = uuid5(
                NAMESPACE_URL,
                "lxscope:shadow-user:"
                f"{normalized.identity_provider}:"
                f"{normalized.external_user_id}",
            )
            username = (
                normalized.username
                or normalized.email
                or f"{normalized.identity_provider}-{user_id.hex}"
            )
            user_values = {
                "id": user_id,
                "username": username,
                "external_user_id": normalized.external_user_id,
                "email": normalized.email,
                "system_role": "user",
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
            }
            await connection.execute(
                self._insert_ignore_conflicts(users, user_values, connection),
            )
            user_updates: dict[str, Any] = {"updated_at": _utc_now()}
            if normalized.username:
                user_updates["username"] = normalized.username
            if normalized.email:
                user_updates["email"] = normalized.email
            await connection.execute(
                update(users)
                .where(users.c.external_user_id == normalized.external_user_id)
                .values(**user_updates),
            )
            user_result = await connection.execute(
                select(
                    users.c.id,
                    users.c.status,
                    users.c.email,
                ).where(
                    users.c.external_user_id == normalized.external_user_id,
                ),
            )
            user_row = user_result.mappings().first()
            if user_row is None:
                raise RuntimeError(
                    "Shadow user insert did not produce a user row",
                )
            if user_row["status"] != "active":
                raise TenantIdentityStatusError(
                    subject="user",
                    status=user_row["status"],
                )

            actual_user_id = user_row["id"]
            tenant_id = tenant_row["id"]
            membership_id = uuid5(
                NAMESPACE_URL,
                f"lxscope:membership:{tenant_id}:{actual_user_id}",
            )
            display_name = (
                normalized.display_name
                or normalized.username
                or normalized.email
                or normalized.external_user_id
            )
            membership_values = {
                "id": membership_id,
                "tenant_id": tenant_id,
                "user_id": actual_user_id,
                "role": self._initial_role(normalized),
                "status": "active",
                "display_name": display_name,
                "joined_at": _utc_now(),
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
            }
            await connection.execute(
                self._insert_ignore_conflicts(
                    memberships,
                    membership_values,
                    connection,
                ),
            )
            if normalized.display_name or normalized.username or normalized.email:
                await connection.execute(
                    update(memberships)
                    .where(
                        memberships.c.tenant_id == tenant_id,
                        memberships.c.user_id == actual_user_id,
                    )
                    .values(
                        display_name=display_name,
                        updated_at=_utc_now(),
                    ),
                )
            membership_result = await connection.execute(
                select(
                    memberships.c.id,
                    memberships.c.role,
                    memberships.c.status,
                    memberships.c.display_name,
                ).where(
                    memberships.c.tenant_id == tenant_id,
                    memberships.c.user_id == actual_user_id,
                ),
            )
            membership_row = membership_result.mappings().first()
            if membership_row is None:
                raise RuntimeError(
                    "Membership insert did not produce a membership row",
                )
            if membership_row["status"] != "active":
                raise TenantIdentityStatusError(
                    subject="membership",
                    status=membership_row["status"],
                )

            permission_result = await connection.execute(
                select(role_permissions.c.permission).where(
                    role_permissions.c.role == membership_row["role"],
                ),
            )
            permissions = frozenset(
                str(row[0])
                for row in permission_result
                if row[0]
            )

            return TenantIdentity(
                tenant_id=tenant_id,
                user_id=actual_user_id,
                membership_id=membership_row["id"],
                identity_provider=tenant_row["identity_provider"],
                external_org_id=tenant_row["external_org_id"],
                external_user_id=normalized.external_user_id,
                role=membership_row["role"],
                status=membership_row["status"],
                display_name=membership_row["display_name"],
                tenant_status=tenant_row["status"],
                user_status=user_row["status"],
                permissions=permissions,
                scopes=permissions,
            )

    @staticmethod
    def _initial_role(principal: IdentityPrincipal) -> str:
        """Select the bootstrap role only when the membership is first created.

        Subsequent authorization is driven by the persisted membership role and
        ``role_permission_mapping``.  Logto organization roles are therefore
        used only to bootstrap an unbound user into a tenant-admin membership.
        """

        organization_id = principal.external_org_id.strip().lower()
        for value in principal.organization_roles:
            role = value.strip().lower()
            if role in {"admin", "owner"}:
                return "tenant_admin"
            prefix = f"{organization_id}:"
            if role.startswith(prefix) and role.removeprefix(prefix) in {
                "admin",
                "owner",
            }:
                return "tenant_admin"
        return "member"

    async def list_members(self, tenant_id: Any) -> list[dict[str, Any]]:
        """List application-bound members for one trusted tenant.

        This is deliberately backed by the lxScope binding tables rather than
        the legacy local-auth Redis account index.  It returns only members
        that have crossed the verified Logto identity boundary; it does not
        invent or import accounts from another tenant.
        """

        tables = self._table_definitions()
        users = tables["users"]
        memberships = tables["memberships"]
        role_permissions = tables["role_permissions"]

        from sqlalchemy import select

        statement = (
            select(
                memberships.c.id.label("membership_id"),
                memberships.c.role.label("membership_role"),
                memberships.c.status.label("membership_status"),
                memberships.c.display_name,
                users.c.id.label("user_id"),
                users.c.username,
                users.c.external_user_id,
                users.c.email,
                users.c.status.label("user_status"),
                memberships.c.created_at,
                memberships.c.updated_at,
            )
            .select_from(
                memberships.join(
                    users,
                    memberships.c.user_id == users.c.id,
                ),
            )
            .where(memberships.c.tenant_id == tenant_id)
            .order_by(
                memberships.c.display_name,
                users.c.username,
            )
        )

        async with self._engine.connect() as connection:
            result = await connection.execute(statement)
            rows = [dict(row) for row in result.mappings().all()]
            permission_result = await connection.execute(
                select(
                    role_permissions.c.role,
                    role_permissions.c.permission,
                ),
            )
            permission_map: dict[str, list[str]] = {}
            for role, permission in permission_result:
                permission_map.setdefault(str(role), []).append(str(permission))
            for row in rows:
                row["permissions"] = sorted(
                    permission_map.get(str(row["membership_role"]), []),
                )
            return rows

    async def list_all_members(self) -> list[dict[str, Any]]:
        """Return active-tenant memberships for Platform Admin views.

        This is the only repository method that intentionally omits a tenant
        predicate.  Callers must already have passed the explicit
        ``is_platform_admin`` authorization check.  The returned rows retain
        ``tenant_id`` so downstream profile, quota, and audit operations can
        keep their original tenant namespace instead of collapsing all
        tenants into the requester's current tenant.
        """

        tables = self._table_definitions()
        tenants = tables["tenants"]
        users = tables["users"]
        memberships = tables["memberships"]
        role_permissions = tables["role_permissions"]

        from sqlalchemy import select

        statement = (
            select(
                memberships.c.id.label("membership_id"),
                memberships.c.tenant_id.label("tenant_id"),
                memberships.c.role.label("membership_role"),
                memberships.c.status.label("membership_status"),
                memberships.c.display_name,
                users.c.id.label("user_id"),
                users.c.username,
                users.c.external_user_id,
                users.c.email,
                users.c.status.label("user_status"),
                memberships.c.created_at,
                memberships.c.updated_at,
            )
            .select_from(
                memberships.join(users, memberships.c.user_id == users.c.id).join(
                    tenants,
                    memberships.c.tenant_id == tenants.c.id,
                ),
            )
            .where(tenants.c.status == "active")
            .order_by(memberships.c.tenant_id, memberships.c.display_name, users.c.username)
        )

        async with self._engine.connect() as connection:
            result = await connection.execute(statement)
            rows = [dict(row) for row in result.mappings().all()]
            permission_result = await connection.execute(
                select(role_permissions.c.role, role_permissions.c.permission),
            )
            permission_map: dict[str, list[str]] = {}
            for role, permission in permission_result:
                permission_map.setdefault(str(role), []).append(str(permission))
            for row in rows:
                row["permissions"] = sorted(
                    permission_map.get(str(row["membership_role"]), []),
                )
            return rows

    async def get_member(
        self,
        tenant_id: Any,
        membership_id: Any,
    ) -> dict[str, Any] | None:
        """Return one membership, always constrained by both tenant and ID."""

        return next(
            (
                row
                for row in await self.list_members(tenant_id)
                if str(row["membership_id"]) == str(membership_id)
            ),
            None,
        )

    async def _role_exists(self, connection: Any, role: str) -> bool:
        from sqlalchemy import select

        mapping = self._table_definitions()["role_permissions"]
        result = await connection.execute(
            select(mapping.c.role).where(mapping.c.role == role).limit(1),
        )
        return result.first() is not None

    async def permissions_for_role(self, role: str) -> frozenset[str]:
        """Return the database-defined permissions for one tenant role."""

        mapping = self._table_definitions()["role_permissions"]
        from sqlalchemy import select

        async with self._engine.connect() as connection:
            result = await connection.execute(
                select(mapping.c.permission).where(mapping.c.role == role),
            )
            return frozenset(str(row[0]) for row in result if row[0])

    async def add_member(
        self,
        tenant_id: Any,
        *,
        external_user_id: str,
        role: str = "member",
        username: str | None = None,
        display_name: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any]:
        """Bind an existing Logto subject to the current tenant."""

        external_user_id = external_user_id.strip()
        role = role.strip().lower()
        if not external_user_id:
            raise ValueError("external_user_id must be a non-empty string")
        if not role:
            raise ValueError("role must be a non-empty string")

        tables = self._table_definitions()
        tenants = tables["tenants"]
        users = tables["users"]
        memberships = tables["memberships"]
        from sqlalchemy import select, update

        async with self._engine.begin() as connection:
            tenant = await connection.execute(
                select(tenants.c.id, tenants.c.status).where(
                    tenants.c.id == tenant_id,
                ),
            )
            tenant_row = tenant.mappings().first()
            if tenant_row is None:
                raise ValueError("Tenant not found")
            if tenant_row["status"] != "active":
                raise TenantIdentityStatusError(
                    subject="tenant",
                    status=tenant_row["status"],
                )
            if not await self._role_exists(connection, role):
                raise ValueError(f"Unknown tenant role: {role}")

            user_id = uuid5(
                NAMESPACE_URL,
                f"lxscope:shadow-user:{self._identity_provider}:{external_user_id}",
            )
            safe_username = (username or email or external_user_id).strip()
            await connection.execute(
                self._insert_ignore_conflicts(
                    users,
                    {
                        "id": user_id,
                        "username": safe_username,
                        "external_user_id": external_user_id,
                        "email": email,
                        "system_role": "user",
                        "created_at": _utc_now(),
                        "updated_at": _utc_now(),
                    },
                    connection,
                ),
            )
            updates: dict[str, Any] = {"updated_at": _utc_now()}
            if username:
                updates["username"] = username.strip()
            if email:
                updates["email"] = email.strip()
            if len(updates) > 1:
                await connection.execute(
                    update(users)
                    .where(users.c.external_user_id == external_user_id)
                    .values(**updates),
                )

            user_result = await connection.execute(
                select(users.c.id, users.c.status).where(
                    users.c.external_user_id == external_user_id,
                ),
            )
            user_row = user_result.mappings().first()
            if user_row is None:
                raise RuntimeError("Member mapping was not created")
            if user_row["status"] != "active":
                raise TenantIdentityStatusError(
                    subject="user",
                    status=user_row["status"],
                )

            membership_id = uuid5(
                NAMESPACE_URL,
                f"lxscope:membership:{tenant_id}:{user_row['id']}",
            )
            await connection.execute(
                self._insert_ignore_conflicts(
                    memberships,
                    {
                        "id": membership_id,
                        "tenant_id": tenant_id,
                        "user_id": user_row["id"],
                        "role": role,
                        "status": "active",
                        "display_name": display_name or username or email or external_user_id,
                        "joined_at": _utc_now(),
                        "created_at": _utc_now(),
                        "updated_at": _utc_now(),
                    },
                    connection,
                ),
            )
            await connection.execute(
                update(memberships)
                .where(
                    memberships.c.tenant_id == tenant_id,
                    memberships.c.user_id == user_row["id"],
                )
                .values(
                    role=role,
                    status="active",
                    display_name=display_name or username or email or external_user_id,
                    updated_at=_utc_now(),
                ),
            )

        member = await self.get_member(tenant_id, membership_id)
        if member is None:
            raise RuntimeError("Membership was not created")
        return member

    async def update_member(
        self,
        tenant_id: Any,
        membership_id: Any,
        *,
        role: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        """Update one member using a tenant-qualified membership key."""

        allowed_statuses = {"active", "disabled", "removed"}
        if status is not None and status not in allowed_statuses:
            raise ValueError(f"Unknown membership status: {status}")
        if role is not None:
            role = role.strip().lower()

        tables = self._table_definitions()
        memberships = tables["memberships"]
        from sqlalchemy import select, update

        async with self._engine.begin() as connection:
            if role is not None and not await self._role_exists(connection, role):
                raise ValueError(f"Unknown tenant role: {role}")
            values: dict[str, Any] = {"updated_at": _utc_now()}
            if role is not None:
                values["role"] = role
            if status is not None:
                values["status"] = status
            result = await connection.execute(
                update(memberships)
                .where(
                    memberships.c.tenant_id == tenant_id,
                    memberships.c.id == membership_id,
                )
                .values(**values),
            )
            if result.rowcount == 0:
                return None

        return await self.get_member(tenant_id, membership_id)

__all__ = ["TenantBindingRepository"]
