# -*- coding: utf-8 -*-
"""PostgreSQL TaskStore for the lxScope application layer.

The store implements the existing TaskStoreProtocol and never touches
AgentScope's StorageBase.  Task definitions and run snapshots are kept as
JSONB for compatibility with the current Pydantic contracts, while the
database also maintains tenant-aware query dimensions and replayable events.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from ._models import (
    NodeRunRecord,
    TaskEventRecord,
    TaskEventDraft,
    TaskRecord,
    TaskRunRecord,
    TaskStatus,
)
from ._store import TaskStoreProtocol
from ._store import (
    TaskStoreConflictError,
    _apply_run_transition,
    _check_expected_version,
    _check_owner,
    _require_claimable,
    _require_dag_run,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stable_uuid(value: str, *, namespace: UUID = NAMESPACE_URL) -> UUID:
    """Accept UUID/hex IDs and deterministically map legacy string IDs."""

    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return uuid5(namespace, f"lxscope:{value}")


class PostgresTaskStore(TaskStoreProtocol):
    """Persist Task records without changing the TaskService contract."""

    def __init__(
        self,
        engine: Any,
        *,
        tenant_code: str = "default",
        tenant_name: str = "Default Tenant",
        tenant_id: str | UUID | None = None,
    ) -> None:
        self._engine = engine
        self._tenant_code = tenant_code.strip() or "default"
        self._tenant_name = tenant_name.strip() or self._tenant_code
        self._tenant_id = (
            UUID(str(tenant_id))
            if tenant_id is not None
            else uuid5(NAMESPACE_URL, f"lxscope:tenant:{self._tenant_code}")
        )
        self._tables: dict[str, Any] | None = None

    def _table_definitions(self) -> dict[str, Any]:
        if self._tables is not None:
            return self._tables

        from sqlalchemy import (
            JSON,
            BigInteger,
            DateTime,
            Integer,
            MetaData,
            String,
            Table,
            Text,
            Uuid,
        )

        metadata = MetaData(schema="longxin_app")
        uuid_type = Uuid(as_uuid=True)
        self._tables = {
            "tenants": Table(
                "tenants",
                metadata,
                # Only columns used by this transitional store are declared.
                # The schema itself is owned by the SQL migration.
                __import__("sqlalchemy").Column("id", uuid_type, primary_key=True),
                __import__("sqlalchemy").Column("code", String(64), nullable=False),
                __import__("sqlalchemy").Column("name", String(128), nullable=False),
                __import__("sqlalchemy").Column("status", String(32), nullable=False),
                __import__("sqlalchemy").Column("created_at", DateTime(timezone=True)),
                __import__("sqlalchemy").Column("updated_at", DateTime(timezone=True)),
            ),
            "users": Table(
                "users",
                metadata,
                __import__("sqlalchemy").Column("id", uuid_type, primary_key=True),
                __import__("sqlalchemy").Column("username", String(128), nullable=False),
                __import__("sqlalchemy").Column("external_user_id", String(255)),
                __import__("sqlalchemy").Column("system_role", String(32), nullable=False),
                __import__("sqlalchemy").Column("created_at", DateTime(timezone=True)),
                __import__("sqlalchemy").Column("updated_at", DateTime(timezone=True)),
            ),
            "memberships": Table(
                "tenant_memberships",
                metadata,
                __import__("sqlalchemy").Column("id", uuid_type, primary_key=True),
                __import__("sqlalchemy").Column("tenant_id", uuid_type, nullable=False),
                __import__("sqlalchemy").Column("user_id", uuid_type, nullable=False),
                __import__("sqlalchemy").Column("role", String(32), nullable=False),
                __import__("sqlalchemy").Column("status", String(32), nullable=False),
                __import__("sqlalchemy").Column("display_name", String(128)),
                __import__("sqlalchemy").Column("joined_at", DateTime(timezone=True)),
                __import__("sqlalchemy").Column("created_at", DateTime(timezone=True)),
                __import__("sqlalchemy").Column("updated_at", DateTime(timezone=True)),
            ),
            "tasks": Table(
                "tasks",
                metadata,
                __import__("sqlalchemy").Column("id", uuid_type, primary_key=True),
                __import__("sqlalchemy").Column("tenant_id", uuid_type, nullable=False),
                __import__("sqlalchemy").Column("owner_membership_id", uuid_type, nullable=False),
                __import__("sqlalchemy").Column("name", String(255), nullable=False),
                __import__("sqlalchemy").Column("description", Text),
                __import__("sqlalchemy").Column("status", String(32), nullable=False),
                __import__("sqlalchemy").Column("revision", Integer, nullable=False),
                __import__("sqlalchemy").Column("generation_status", String(32), nullable=False),
                __import__("sqlalchemy").Column("definition", JSON, nullable=False),
                __import__("sqlalchemy").Column("last_run_id", uuid_type),
                __import__("sqlalchemy").Column("created_at", DateTime(timezone=True)),
                __import__("sqlalchemy").Column("updated_at", DateTime(timezone=True)),
                __import__("sqlalchemy").Column("archived_at", DateTime(timezone=True)),
            ),
            "runs": Table(
                "task_runs",
                metadata,
                __import__("sqlalchemy").Column("id", uuid_type, primary_key=True),
                __import__("sqlalchemy").Column("tenant_id", uuid_type, nullable=False),
                __import__("sqlalchemy").Column("task_id", uuid_type, nullable=False),
                __import__("sqlalchemy").Column("triggered_by_membership_id", uuid_type),
                __import__("sqlalchemy").Column("task_revision", Integer, nullable=False),
                __import__("sqlalchemy").Column("status", String(32), nullable=False),
                __import__("sqlalchemy").Column("input", JSON),
                __import__("sqlalchemy").Column("output", JSON),
                __import__("sqlalchemy").Column("snapshot", JSON, nullable=False),
                __import__("sqlalchemy").Column("error_message", Text),
                __import__("sqlalchemy").Column("started_at", DateTime(timezone=True)),
                __import__("sqlalchemy").Column("finished_at", DateTime(timezone=True)),
                __import__("sqlalchemy").Column("created_at", DateTime(timezone=True)),
            ),
            "events": Table(
                "task_events",
                metadata,
                __import__("sqlalchemy").Column("id", BigInteger, primary_key=True),
                __import__("sqlalchemy").Column("tenant_id", uuid_type, nullable=False),
                __import__("sqlalchemy").Column("run_id", uuid_type, nullable=False),
                __import__("sqlalchemy").Column("sequence", BigInteger, nullable=False),
                __import__("sqlalchemy").Column("event_type", String(64), nullable=False),
                __import__("sqlalchemy").Column("payload", JSON, nullable=False),
                __import__("sqlalchemy").Column("occurred_at", DateTime(timezone=True)),
            ),
            "artifacts": Table(
                "task_artifacts",
                metadata,
                __import__("sqlalchemy").Column("id", uuid_type, primary_key=True),
                __import__("sqlalchemy").Column("tenant_id", uuid_type, nullable=False),
                __import__("sqlalchemy").Column("run_id", uuid_type, nullable=False),
                __import__("sqlalchemy").Column("artifact_type", String(64), nullable=False),
                __import__("sqlalchemy").Column("file_name", String(255)),
                __import__("sqlalchemy").Column("storage_uri", Text, nullable=False),
                __import__("sqlalchemy").Column("mime_type", String(128)),
                __import__("sqlalchemy").Column("size_bytes", BigInteger),
                __import__("sqlalchemy").Column("metadata", JSON, nullable=False),
                __import__("sqlalchemy").Column("created_at", DateTime(timezone=True)),
            ),
        }
        return self._tables

    async def initialize(self) -> None:
        """Ensure the configured default tenant exists."""

        from sqlalchemy import func
        from sqlalchemy.dialects.postgresql import insert

        tables = self._table_definitions()
        tenants = tables["tenants"]
        async with self._engine.begin() as connection:
            await connection.execute(
                insert(tenants)
                .values(
                    id=self._tenant_id,
                    code=self._tenant_code,
                    name=self._tenant_name,
                    status="active",
                    created_at=_utc_now(),
                    updated_at=_utc_now(),
                )
                .on_conflict_do_update(
                    index_elements=[tenants.c.id],
                    set_={"name": self._tenant_name, "updated_at": func.now()},
                ),
            )

    async def _ensure_identity(self, connection: Any, user_id: str) -> UUID:
        """Map a legacy string user ID to a tenant membership UUID."""

        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert

        tables = self._table_definitions()
        users = tables["users"]
        memberships = tables["memberships"]
        user_uuid = uuid5(NAMESPACE_URL, f"lxscope:user:{user_id}")
        membership_uuid = uuid5(
            NAMESPACE_URL,
            f"lxscope:membership:{self._tenant_id}:{user_id}",
        )
        username = f"legacy-{user_uuid.hex}"

        await connection.execute(
            insert(users)
            .values(
                id=user_uuid,
                username=username,
                external_user_id=user_id,
                system_role="user",
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
            .on_conflict_do_nothing(),
        )
        user_row = (
            await connection.execute(
                select(users.c.id).where(users.c.external_user_id == user_id),
            )
        ).first()
        actual_user_uuid = user_row[0] if user_row else user_uuid
        await connection.execute(
            insert(memberships)
            .values(
                id=membership_uuid,
                tenant_id=self._tenant_id,
                user_id=actual_user_uuid,
                role="member",
                status="active",
                display_name=user_id,
                joined_at=_utc_now(),
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
            .on_conflict_do_nothing(),
        )
        return membership_uuid

    @staticmethod
    def _task_payload(record: TaskRecord) -> dict[str, Any]:
        return record.model_dump(mode="json")

    @staticmethod
    def _task_from_payload(payload: Any) -> TaskRecord:
        return TaskRecord.model_validate(payload)

    @staticmethod
    def _run_payload(record: TaskRunRecord) -> dict[str, Any]:
        return record.model_dump(mode="json")

    @staticmethod
    def _run_from_payload(payload: Any) -> TaskRunRecord:
        return TaskRunRecord.model_validate(payload)

    async def list_tasks(self, user_id: str) -> list[TaskRecord]:
        from sqlalchemy import select

        tables = self._table_definitions()
        tasks = tables["tasks"]
        async with self._engine.begin() as connection:
            membership_id = await self._ensure_identity(connection, user_id)
            result = await connection.execute(
                select(tasks.c.definition)
                .where(
                    tasks.c.tenant_id == self._tenant_id,
                    tasks.c.owner_membership_id == membership_id,
                    tasks.c.status != TaskStatus.ARCHIVED.value,
                )
                .order_by(tasks.c.updated_at.desc(), tasks.c.id.desc()),
            )
            return [self._task_from_payload(value) for value in result.scalars()]

    async def get_task(self, user_id: str, task_id: str) -> TaskRecord | None:
        from sqlalchemy import select

        tables = self._table_definitions()
        tasks = tables["tasks"]
        async with self._engine.begin() as connection:
            membership_id = await self._ensure_identity(connection, user_id)
            value = (
                await connection.execute(
                    select(tasks.c.definition).where(
                        tasks.c.tenant_id == self._tenant_id,
                        tasks.c.id == _stable_uuid(task_id),
                        tasks.c.owner_membership_id == membership_id,
                    ),
                )
            ).scalar_one_or_none()
            return self._task_from_payload(value) if value is not None else None

    async def save_task(self, record: TaskRecord) -> TaskRecord:
        from sqlalchemy.dialects.postgresql import insert

        tables = self._table_definitions()
        tasks = tables["tasks"]
        stored = record.model_copy(update={"updated_at": _utc_now()}, deep=True)
        payload = self._task_payload(stored)
        async with self._engine.begin() as connection:
            membership_id = await self._ensure_identity(connection, stored.user_id)
            await connection.execute(
                insert(tasks)
                .values(
                    id=_stable_uuid(stored.id),
                    tenant_id=self._tenant_id,
                    owner_membership_id=membership_id,
                    name=stored.title,
                    description=stored.goal,
                    status=stored.status.value,
                    revision=stored.revision,
                    generation_status=stored.generation_status.value,
                    definition=payload,
                    last_run_id=(
                        _stable_uuid(stored.last_run_id)
                        if stored.last_run_id
                        else None
                    ),
                    created_at=stored.created_at,
                    updated_at=stored.updated_at,
                    archived_at=(
                        stored.updated_at
                        if stored.status == TaskStatus.ARCHIVED
                        else None
                    ),
                )
                .on_conflict_do_update(
                    index_elements=[tasks.c.id],
                    set_={
                        "owner_membership_id": membership_id,
                        "name": stored.title,
                        "description": stored.goal,
                        "status": stored.status.value,
                        "revision": stored.revision,
                        "generation_status": stored.generation_status.value,
                        "definition": payload,
                        "last_run_id": (
                            _stable_uuid(stored.last_run_id)
                            if stored.last_run_id
                            else None
                        ),
                        "updated_at": stored.updated_at,
                        "archived_at": (
                            stored.updated_at
                            if stored.status == TaskStatus.ARCHIVED
                            else None
                        ),
                    },
                ),
            )
        return stored

    async def delete_task(self, user_id: str, task_id: str) -> bool:
        record = await self.get_task(user_id, task_id)
        if record is None:
            return False
        await self.save_task(
            record.model_copy(update={"status": TaskStatus.ARCHIVED}, deep=True),
        )
        return True

    async def save_run(self, record: TaskRunRecord) -> TaskRunRecord:
        from sqlalchemy import delete, select
        from sqlalchemy.dialects.postgresql import insert

        tables = self._table_definitions()
        runs = tables["runs"]
        artifacts = tables["artifacts"]
        payload = self._run_payload(record)
        async with self._engine.begin() as connection:
            membership_id = await self._ensure_identity(connection, record.user_id)
            existing_payload = (
                await connection.execute(
                    select(runs.c.snapshot)
                    .where(
                        runs.c.tenant_id == self._tenant_id,
                        runs.c.id == _stable_uuid(record.id),
                    )
                    .with_for_update(),
                )
            ).scalar_one_or_none()
            if (
                existing_payload is not None
                and (
                    self._run_from_payload(existing_payload).engine in {"dag", "sop"}
                    or record.engine in {"dag", "sop"}
                )
            ):
                raise TaskStoreConflictError(
                    "Durable Task Run writes must use transition_run()."
                )
            is_new_durable_run = existing_payload is None and record.engine in {"dag", "sop"}
            if is_new_durable_run:
                if record.run_version or record.event_sequence or record.owner_token:
                    raise ValueError(
                        "A new durable Task Run must start at version zero without an owner."
                    )
                record = record.model_copy(update={"event_sequence": 1})
                payload = self._run_payload(record)
            await connection.execute(
                insert(runs)
                .values(
                    id=_stable_uuid(record.id),
                    tenant_id=self._tenant_id,
                    task_id=_stable_uuid(record.task_id),
                    triggered_by_membership_id=membership_id,
                    task_revision=record.task_revision,
                    status=record.status.value,
                    input={"text": record.input},
                    output={
                        "text": record.final_output,
                        "summary": record.final_summary,
                    },
                    snapshot=payload,
                    error_message=record.error,
                    started_at=record.started_at,
                    finished_at=record.finished_at,
                    created_at=record.created_at,
                )
                .on_conflict_do_update(
                    index_elements=[runs.c.id],
                    set_={
                        "task_id": _stable_uuid(record.task_id),
                        "triggered_by_membership_id": membership_id,
                        "task_revision": record.task_revision,
                        "status": record.status.value,
                        "input": {"text": record.input},
                        "output": {
                            "text": record.final_output,
                            "summary": record.final_summary,
                        },
                        "snapshot": payload,
                        "error_message": record.error,
                        "started_at": record.started_at,
                        "finished_at": record.finished_at,
                    },
                ),
            )
            await connection.execute(
                delete(artifacts).where(
                    artifacts.c.tenant_id == self._tenant_id,
                    artifacts.c.run_id == _stable_uuid(record.id),
                ),
            )
            if is_new_durable_run:
                await connection.execute(
                    insert(tables["events"]).values(
                        tenant_id=self._tenant_id,
                        run_id=_stable_uuid(record.id),
                        sequence=1,
                        event_type="run.created",
                        payload=(
                            {"task_revision": record.task_revision}
                            if record.engine == "sop"
                            else {}
                        ),
                        occurred_at=_utc_now(),
                    ),
                )
            for artifact in record.artifacts:
                await connection.execute(
                    insert(artifacts).values(
                        id=_stable_uuid(artifact.id),
                        tenant_id=self._tenant_id,
                        run_id=_stable_uuid(record.id),
                        artifact_type=artifact.format.value,
                        file_name=artifact.name,
                        storage_uri=artifact.path,
                        mime_type=artifact.media_type,
                        size_bytes=artifact.size_bytes,
                        metadata={
                            "node_id": artifact.node_id,
                            "delivery_status": artifact.delivery_status,
                        },
                        created_at=artifact.created_at,
                    ).on_conflict_do_nothing(),
                )
        return record.model_copy(deep=True)

    async def _run_payload_for_user(
        self,
        connection: Any,
        user_id: str,
        run_id: str,
        task_id: str | None = None,
    ) -> Any | None:
        from sqlalchemy import and_, select

        tables = self._table_definitions()
        tasks = tables["tasks"]
        runs = tables["runs"]
        membership_id = await self._ensure_identity(connection, user_id)
        conditions = [
            runs.c.tenant_id == self._tenant_id,
            runs.c.id == _stable_uuid(run_id),
            tasks.c.tenant_id == self._tenant_id,
            tasks.c.id == runs.c.task_id,
            tasks.c.owner_membership_id == membership_id,
        ]
        if task_id is not None:
            conditions.append(tasks.c.id == _stable_uuid(task_id))
        return (
            await connection.execute(
                select(runs.c.snapshot)
                .select_from(runs.join(tasks, and_(runs.c.task_id == tasks.c.id)))
                .where(*conditions),
            )
        ).scalar_one_or_none()

    async def get_run(self, user_id: str, run_id: str) -> TaskRunRecord | None:
        async with self._engine.begin() as connection:
            payload = await self._run_payload_for_user(connection, user_id, run_id)
            return self._run_from_payload(payload) if payload is not None else None

    async def list_runs(self, user_id: str, task_id: str) -> list[TaskRunRecord]:
        from sqlalchemy import and_, select

        tables = self._table_definitions()
        tasks = tables["tasks"]
        runs = tables["runs"]
        async with self._engine.begin() as connection:
            membership_id = await self._ensure_identity(connection, user_id)
            result = await connection.execute(
                select(runs.c.snapshot)
                .select_from(runs.join(tasks, and_(runs.c.task_id == tasks.c.id)))
                .where(
                    runs.c.tenant_id == self._tenant_id,
                    tasks.c.tenant_id == self._tenant_id,
                    tasks.c.id == _stable_uuid(task_id),
                    tasks.c.owner_membership_id == membership_id,
                )
                .order_by(runs.c.created_at.desc(), runs.c.id.desc()),
            )
            return [self._run_from_payload(value) for value in result.scalars()]

    async def update_run(
        self,
        user_id: str,
        run_id: str,
        **updates: object,
    ) -> TaskRunRecord:
        record = await self.get_run(user_id, run_id)
        if record is None:
            raise KeyError(f"Unknown task run {run_id!r}.")
        return await self.save_run(record.model_copy(update=updates, deep=True))

    async def update_node_run(
        self,
        user_id: str,
        run_id: str,
        node_id: str,
        **updates: object,
    ) -> TaskRunRecord:
        record = await self.get_run(user_id, run_id)
        if record is None:
            raise KeyError(f"Unknown task run {run_id!r}.")
        node_runs: list[NodeRunRecord] = []
        found = False
        for node_run in record.node_runs:
            if node_run.node_id == node_id:
                node_runs.append(node_run.model_copy(update=updates))
                found = True
            else:
                node_runs.append(node_run)
        if not found:
            raise KeyError(f"Unknown task node {node_id!r} in run {run_id!r}.")
        return await self.save_run(
            record.model_copy(update={"node_runs": node_runs}, deep=True),
        )

    async def _lock_run_for_update(
        self,
        connection: Any,
        user_id: str,
        run_id: str,
    ) -> TaskRunRecord | None:
        from sqlalchemy import and_, select

        tables = self._table_definitions()
        tasks = tables["tasks"]
        runs = tables["runs"]
        membership_id = await self._ensure_identity(connection, user_id)
        payload = (
            await connection.execute(
                select(runs.c.snapshot)
                .select_from(runs.join(tasks, runs.c.task_id == tasks.c.id))
                .where(
                    runs.c.tenant_id == self._tenant_id,
                    runs.c.id == _stable_uuid(run_id),
                    tasks.c.tenant_id == self._tenant_id,
                    tasks.c.owner_membership_id == membership_id,
                )
                .with_for_update(of=runs),
            )
        ).scalar_one_or_none()
        return self._run_from_payload(payload) if payload is not None else None

    async def _persist_run_transition(
        self,
        connection: Any,
        current: TaskRunRecord,
        updated: TaskRunRecord,
        events_to_append: list[TaskEventRecord],
    ) -> None:
        from sqlalchemy import delete, update
        from sqlalchemy.dialects.postgresql import insert

        tables = self._table_definitions()
        runs = tables["runs"]
        events = tables["events"]
        artifacts = tables["artifacts"]
        result = await connection.execute(
            update(runs)
            .where(
                runs.c.tenant_id == self._tenant_id,
                runs.c.id == _stable_uuid(updated.id),
            )
            .values(
                task_revision=updated.task_revision,
                status=updated.status.value,
                input={"text": updated.input},
                output={"text": updated.final_output, "summary": updated.final_summary},
                snapshot=self._run_payload(updated),
                error_message=updated.error,
                started_at=updated.started_at,
                finished_at=updated.finished_at,
            )
        )
        if result.rowcount != 1:
            raise TaskStoreConflictError("Task Run disappeared during transition.")

        if updated.artifacts != current.artifacts:
            await connection.execute(
                delete(artifacts).where(
                    artifacts.c.tenant_id == self._tenant_id,
                    artifacts.c.run_id == _stable_uuid(updated.id),
                ),
            )
            for artifact in updated.artifacts:
                await connection.execute(
                    insert(artifacts).values(
                        id=_stable_uuid(artifact.id),
                        tenant_id=self._tenant_id,
                        run_id=_stable_uuid(updated.id),
                        artifact_type=artifact.format.value,
                        file_name=artifact.name,
                        storage_uri=artifact.path,
                        mime_type=artifact.media_type,
                        size_bytes=artifact.size_bytes,
                        metadata={
                            "node_id": artifact.node_id,
                            "delivery_status": artifact.delivery_status,
                        },
                        created_at=artifact.created_at,
                    ).on_conflict_do_nothing(),
                )
        for event in events_to_append:
            await connection.execute(
                insert(events).values(
                    tenant_id=self._tenant_id,
                    run_id=_stable_uuid(updated.id),
                    sequence=event.sequence,
                    event_type=event.type,
                    payload=event.payload,
                    occurred_at=event.created_at,
                ),
            )

    async def transition_run(
        self,
        user_id: str,
        run_id: str,
        *,
        expected_version: int,
        run_patch: dict[str, object] | None = None,
        node_patches: dict[str, dict[str, object]] | None = None,
        attempt_events: list[TaskEventDraft] | None = None,
        audit_events: list[TaskEventDraft] | None = None,
        owner_token: str | None = None,
        expected_fence_epoch: int | None = None,
        command_id: str | None = None,
        release_owner: bool = False,
    ) -> TaskRunRecord:
        """Lock one owned row and atomically persist its CAS transition/events."""

        from sqlalchemy import func, select

        tables = self._table_definitions()
        events_table = tables["events"]
        runs = tables["runs"]
        async with self._engine.begin() as connection:
            current = await self._lock_run_for_update(connection, user_id, run_id)
            if current is None:
                raise KeyError(f"Unknown task run {run_id!r}.")
            _require_dag_run(current)
            if release_owner and owner_token is None:
                raise ValueError(
                    "A current owner token is required to release a lease."
                )
            if command_id and command_id in current.processed_command_ids:
                return current
            _check_expected_version(current, expected_version)
            if owner_token is not None:
                _check_owner(current, owner_token, expected_fence_epoch)
            event_count = int(
                (
                    await connection.execute(
                        select(func.coalesce(func.max(events_table.c.sequence), 0)).where(
                            events_table.c.tenant_id == self._tenant_id,
                            events_table.c.run_id == _stable_uuid(run_id),
                        )
                    )
                ).scalar_one()
            )
            updated, event_records = _apply_run_transition(
                current,
                expected_version=expected_version,
                run_patch=run_patch or {},
                node_patches=node_patches or {},
                attempt_events=attempt_events or [],
                audit_events=audit_events or [],
                command_id=command_id,
                release_owner=release_owner,
                persisted_event_count=event_count,
            )
            await self._persist_run_transition(
                connection,
                current,
                updated,
                event_records,
            )
            return updated

    async def claim_run(
        self,
        user_id: str,
        run_id: str,
        *,
        owner_token: str,
        lease_seconds: float = 30.0,
        audit_events: list[TaskEventDraft] | None = None,
    ) -> TaskRunRecord:
        """Claim a run while holding a PostgreSQL row lock."""

        from sqlalchemy import func, select

        if not owner_token or lease_seconds <= 0:
            raise ValueError("A claim token and positive lease are required.")
        tables = self._table_definitions()
        events_table = tables["events"]
        async with self._engine.begin() as connection:
            current = await self._lock_run_for_update(connection, user_id, run_id)
            if current is None:
                raise KeyError(f"Unknown task run {run_id!r}.")
            _require_dag_run(current)
            _require_claimable(current)
            now = _utc_now()
            lease_active = (
                current.owner_token is not None
                and current.lease_expires_at is not None
                and current.lease_expires_at > now
            )
            if lease_active and current.owner_token != owner_token:
                raise TaskStoreConflictError("Task Run is owned by another worker.")
            same_live_owner = lease_active and current.owner_token == owner_token
            event_count = int(
                (
                    await connection.execute(
                        select(func.coalesce(func.max(events_table.c.sequence), 0)).where(
                            events_table.c.tenant_id == self._tenant_id,
                            events_table.c.run_id == _stable_uuid(run_id),
                        )
                    )
                ).scalar_one()
            )
            updated, event_records = _apply_run_transition(
                current,
                expected_version=current.run_version,
                run_patch={
                    "owner_token": owner_token,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "fence_epoch": (
                        current.fence_epoch
                        if same_live_owner
                        else current.fence_epoch + 1
                    ),
                },
                node_patches={},
                attempt_events=[],
                audit_events=audit_events or [],
                command_id=None,
                release_owner=False,
                persisted_event_count=event_count,
                allow_owner_patch=True,
            )
            await self._persist_run_transition(
                connection,
                current,
                updated,
                event_records,
            )
            return updated

    async def append_event(self, event: TaskEventRecord) -> TaskEventRecord:
        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert

        tables = self._table_definitions()
        events = tables["events"]
        runs = tables["runs"]
        async with self._engine.begin() as connection:
            raw_run = (
                await connection.execute(
                    select(runs.c.snapshot)
                    .where(
                        runs.c.tenant_id == self._tenant_id,
                        runs.c.id == _stable_uuid(event.run_id),
                    )
                    .with_for_update(),
                )
            ).scalar_one_or_none()
            if (
                raw_run is not None
                and self._run_from_payload(raw_run).engine in {"dag", "sop"}
            ):
                raise TaskStoreConflictError(
                    "Durable Task Run events must be appended through transition_run()."
                )
            await connection.execute(
                insert(events).values(
                    tenant_id=self._tenant_id,
                    run_id=_stable_uuid(event.run_id),
                    sequence=event.sequence,
                    event_type=event.type,
                    payload=event.payload,
                    occurred_at=event.created_at,
                ),
            )
        return event.model_copy(deep=True)

    async def list_events(self, user_id: str, run_id: str) -> list[TaskEventRecord]:
        from sqlalchemy import and_, select

        tables = self._table_definitions()
        tasks = tables["tasks"]
        runs = tables["runs"]
        events = tables["events"]
        async with self._engine.begin() as connection:
            membership_id = await self._ensure_identity(connection, user_id)
            result = await connection.execute(
                select(
                    events.c.id,
                    events.c.sequence,
                    events.c.event_type,
                    events.c.payload,
                    events.c.occurred_at,
                    runs.c.task_id,
                )
                .select_from(
                    events.join(runs, events.c.run_id == runs.c.id).join(
                        tasks,
                        and_(runs.c.task_id == tasks.c.id),
                    ),
                )
                .where(
                    events.c.tenant_id == self._tenant_id,
                    events.c.run_id == _stable_uuid(run_id),
                    tasks.c.tenant_id == self._tenant_id,
                    tasks.c.owner_membership_id == membership_id,
                )
                .order_by(events.c.sequence),
            )
            return [
                TaskEventRecord(
                    id=str(row.id),
                    type=row.event_type,
                    task_id=str(row.task_id),
                    run_id=run_id,
                    sequence=row.sequence,
                    payload=row.payload or {},
                    created_at=row.occurred_at,
                )
                for row in result
            ]

    async def next_event_sequence(self, run_id: str) -> int:
        from sqlalchemy import func, select

        events = self._table_definitions()["events"]
        async with self._engine.begin() as connection:
            value = (
                await connection.execute(
                    select(func.coalesce(func.max(events.c.sequence), 0) + 1).where(
                        events.c.tenant_id == self._tenant_id,
                        events.c.run_id == _stable_uuid(run_id),
                    ),
                )
            ).scalar_one()
            return int(value)


__all__ = ["PostgresTaskStore"]
