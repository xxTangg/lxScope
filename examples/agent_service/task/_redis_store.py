# -*- coding: utf-8 -*-
"""Redis repository for Task definitions, runs and replayable events."""

from datetime import datetime, timedelta, timezone
from typing import Any

from ._models import (
    NodeRunRecord,
    TaskEventRecord,
    TaskEventDraft,
    TaskRecord,
    TaskRunRecord,
    TaskStatus,
)
from ._store import (
    TaskStoreConflictError,
    _apply_run_transition,
    _check_expected_version,
    _require_claimable,
    _require_dag_run,
)


def _utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(timezone.utc)


class RedisTaskStore:
    """Persist Task data in the existing AgentScope Redis connection.

    The store deliberately owns a separate ``lxscope:task:`` namespace and
    only relies on the small async Redis client surface. It does not extend
    AgentScope's ``StorageBase`` and can therefore be replaced by SQL later.
    """

    _PREFIX = "lxscope:task"

    def __init__(self, client: Any) -> None:
        """Bind an already-managed async Redis client."""

        self._client = client

    @classmethod
    def _task_key(cls, user_id: str, task_id: str) -> str:
        return f"{cls._PREFIX}:user:{user_id}:task:{task_id}"

    @classmethod
    def _task_index_key(cls, user_id: str) -> str:
        return f"{cls._PREFIX}:user:{user_id}:tasks"

    @classmethod
    def _run_key(cls, user_id: str, run_id: str) -> str:
        return f"{cls._PREFIX}:user:{user_id}:run:{run_id}"

    @classmethod
    def _run_index_key(cls, user_id: str, task_id: str) -> str:
        return f"{cls._PREFIX}:user:{user_id}:task:{task_id}:runs"

    @classmethod
    def _event_key(cls, run_id: str) -> str:
        return f"{cls._PREFIX}:run:{run_id}:events"

    @staticmethod
    def _score(timestamp: datetime) -> float:
        return timestamp.timestamp()

    @staticmethod
    def _decode(value: str | bytes | None) -> str | None:
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else value

    async def list_tasks(self, user_id: str) -> list[TaskRecord]:
        """List non-archived tasks in newest-first order."""

        task_ids = await self._client.zrevrange(self._task_index_key(user_id), 0, -1)
        if not task_ids:
            return []
        raw_records = await self._client.mget(
            [self._task_key(user_id, str(task_id)) for task_id in task_ids],
        )
        records = []
        for raw_value in raw_records:
            raw_json = self._decode(raw_value)
            if raw_json is not None:
                records.append(TaskRecord.model_validate_json(raw_json))
        return [record for record in records if record.status != TaskStatus.ARCHIVED]

    async def get_task(self, user_id: str, task_id: str) -> TaskRecord | None:
        """Return one task owned by the user."""

        raw = self._decode(await self._client.get(self._task_key(user_id, task_id)))
        return TaskRecord.model_validate_json(raw) if raw else None

    async def save_task(self, record: TaskRecord) -> TaskRecord:
        """Create or replace a task definition."""

        stored = record.model_copy(update={"updated_at": _utc_now()}, deep=True)
        await self._client.set(
            self._task_key(stored.user_id, stored.id),
            stored.model_dump_json(),
        )
        await self._client.zadd(
            self._task_index_key(stored.user_id),
            {stored.id: self._score(stored.updated_at)},
        )
        return stored

    async def delete_task(self, user_id: str, task_id: str) -> bool:
        """Archive a task while preserving its runs."""

        record = await self.get_task(user_id, task_id)
        if record is None:
            return False
        await self.save_task(
            record.model_copy(
                update={"status": TaskStatus.ARCHIVED},
                deep=True,
            ),
        )
        return True

    async def save_run(self, record: TaskRunRecord) -> TaskRunRecord:
        """Create or replace one run snapshot."""

        run_key = self._run_key(record.user_id, record.id)
        event_key = self._event_key(record.id)
        async with self._client.pipeline(transaction=True) as pipe:
            await pipe.watch(run_key, event_key)
            existing_raw = self._decode(await pipe.get(run_key))
            if existing_raw is not None:
                existing = TaskRunRecord.model_validate_json(existing_raw)
                if existing.engine in {"dag", "sop"} or record.engine in {"dag", "sop"}:
                    raise TaskStoreConflictError(
                        "Durable Task Run writes must use transition_run()."
                    )
            stored = record.model_copy(deep=True)
            if existing_raw is None and stored.engine in {"dag", "sop"}:
                if stored.run_version or stored.event_sequence or stored.owner_token:
                    raise ValueError("A new durable Task Run must start at version zero without an owner.")
                stored = stored.model_copy(update={"event_sequence": 1})
            pipe.multi()
            pipe.set(run_key, stored.model_dump_json())
            pipe.zadd(
                self._run_index_key(record.user_id, record.task_id),
                {record.id: self._score(record.created_at)},
            )
            pipe.set(self._run_owner_key(record.id), record.user_id)
            if existing_raw is None and stored.engine in {"dag", "sop"}:
                pipe.rpush(
                    event_key,
                    TaskEventRecord(
                        type="run.created",
                        task_id=stored.task_id,
                        run_id=stored.id,
                        sequence=1,
                        payload=(
                            {"task_revision": stored.task_revision}
                            if stored.engine == "sop"
                            else {}
                        ),
                    ).model_dump_json(),
                )
            await pipe.execute()
        return stored.model_copy(deep=True)

    async def get_run(self, user_id: str, run_id: str) -> TaskRunRecord | None:
        """Return one run owned by the user."""

        raw = self._decode(await self._client.get(self._run_key(user_id, run_id)))
        return TaskRunRecord.model_validate_json(raw) if raw else None

    async def list_runs(self, user_id: str, task_id: str) -> list[TaskRunRecord]:
        """List task runs newest-first."""

        run_ids = await self._client.zrevrange(
            self._run_index_key(user_id, task_id),
            0,
            -1,
        )
        if not run_ids:
            return []
        raw_records = await self._client.mget(
            [self._run_key(user_id, str(run_id)) for run_id in run_ids],
        )
        records = []
        for raw_value in raw_records:
            raw_json = self._decode(raw_value)
            if raw_json is not None:
                records.append(TaskRunRecord.model_validate_json(raw_json))
        return records

    async def update_run(
        self,
        user_id: str,
        run_id: str,
        **updates: object,
    ) -> TaskRunRecord:
        """Apply an atomic-enough read/replace update to one run."""

        record = await self.get_run(user_id, run_id)
        if record is None:
            raise KeyError(f"Unknown task run {run_id!r}.")
        if record.engine in {"dag", "sop"}:
            raise TaskStoreConflictError(
                "Durable Task Run writes must use transition_run()."
            )
        return await self.save_run(record.model_copy(update=updates, deep=True))

    async def update_node_run(
        self,
        user_id: str,
        run_id: str,
        node_id: str,
        **updates: object,
    ) -> TaskRunRecord:
        """Apply an update to one node snapshot."""

        record = await self.get_run(user_id, run_id)
        if record is None:
            raise KeyError(f"Unknown task run {run_id!r}.")
        if record.engine in {"dag", "sop"}:
            raise TaskStoreConflictError(
                "Durable Task Run writes must use transition_run()."
            )
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
        return await self.save_run(record.model_copy(update={"node_runs": node_runs}))

    async def append_event(self, event: TaskEventRecord) -> TaskEventRecord:
        """Append one replayable event to its run list."""

        run = await self.get_run_by_id(event.run_id)
        if run is not None and run.engine in {"dag", "sop"}:
            raise TaskStoreConflictError(
                "Durable Task Run events must be appended through transition_run()."
            )
        await self._client.rpush(
            self._event_key(event.run_id),
            event.model_dump_json(),
        )
        return event.model_copy(deep=True)

    async def list_events(self, user_id: str, run_id: str) -> list[TaskEventRecord]:
        """Return events after checking run ownership."""

        if await self.get_run(user_id, run_id) is None:
            return []
        raw_events = await self._client.lrange(self._event_key(run_id), 0, -1)
        events = []
        for raw_value in raw_events:
            raw_json = self._decode(raw_value)
            if raw_json is not None:
                events.append(TaskEventRecord.model_validate_json(raw_json))
        return events

    async def next_event_sequence(self, run_id: str) -> int:
        """Return the next one-based event sequence."""

        return int(await self._client.llen(self._event_key(run_id))) + 1

    async def get_run_by_id(self, run_id: str) -> TaskRunRecord | None:
        """Read a run by globally unique id for event write protection."""

        user_id = self._decode(await self._client.get(self._run_owner_key(run_id)))
        return (
            await self.get_run(user_id, run_id)
            if user_id is not None
            else None
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
        """Apply a versioned run patch and events in one Redis transaction."""

        run_key = self._run_key(user_id, run_id)
        event_key = self._event_key(run_id)
        for _ in range(16):
            try:
                async with self._client.pipeline(transaction=True) as pipe:
                    await pipe.watch(run_key, event_key)
                    raw = self._decode(await pipe.get(run_key))
                    if raw is None:
                        raise KeyError(f"Unknown task run {run_id!r}.")
                    current = TaskRunRecord.model_validate_json(raw)
                    _require_dag_run(current)
                    if release_owner and owner_token is None:
                        raise ValueError(
                            "A current owner token is required to release a lease."
                        )
                    if command_id and command_id in current.processed_command_ids:
                        return current
                    _check_expected_version(current, expected_version)
                    if owner_token is not None:
                        from ._store import _check_owner

                        _check_owner(current, owner_token, expected_fence_epoch)
                    event_count = int(await pipe.llen(event_key))
                    updated, events = _apply_run_transition(
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
                    pipe.multi()
                    pipe.set(run_key, updated.model_dump_json())
                    for event in events:
                        pipe.rpush(event_key, event.model_dump_json())
                    await pipe.execute()
                    return updated
            except Exception as error:
                if error.__class__.__name__ == "WatchError":
                    continue
                raise
        raise TaskStoreConflictError("Redis CAS retry limit was exceeded.")

    async def claim_run(
        self,
        user_id: str,
        run_id: str,
        *,
        owner_token: str,
        lease_seconds: float = 30.0,
        audit_events: list[TaskEventDraft] | None = None,
    ) -> TaskRunRecord:
        """Atomically acquire a lease and advance its fencing epoch."""

        if not owner_token or lease_seconds <= 0:
            raise ValueError("A claim token and positive lease are required.")
        run_key = self._run_key(user_id, run_id)
        event_key = self._event_key(run_id)
        for _ in range(16):
            try:
                async with self._client.pipeline(transaction=True) as pipe:
                    await pipe.watch(run_key, event_key)
                    raw = self._decode(await pipe.get(run_key))
                    if raw is None:
                        raise KeyError(f"Unknown task run {run_id!r}.")
                    current = TaskRunRecord.model_validate_json(raw)
                    _require_dag_run(current)
                    _require_claimable(current)
                    now = _utc_now()
                    lease_active = (
                        current.owner_token is not None
                        and current.lease_expires_at is not None
                        and current.lease_expires_at > now
                    )
                    if lease_active and current.owner_token != owner_token:
                        raise TaskStoreConflictError(
                            "Task Run is owned by another worker."
                        )
                    same_live_owner = (
                        lease_active and current.owner_token == owner_token
                    )
                    event_count = int(await pipe.llen(event_key))
                    updated, events = _apply_run_transition(
                        current,
                        expected_version=current.run_version,
                        run_patch={
                            "owner_token": owner_token,
                            "lease_expires_at": now
                            + timedelta(seconds=lease_seconds),
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
                    pipe.multi()
                    pipe.set(run_key, updated.model_dump_json())
                    for event in events:
                        pipe.rpush(event_key, event.model_dump_json())
                    await pipe.execute()
                    return updated
            except Exception as error:
                if error.__class__.__name__ == "WatchError":
                    continue
                raise
        raise TaskStoreConflictError("Redis claim retry limit was exceeded.")

    @classmethod
    def _run_owner_key(cls, run_id: str) -> str:
        return f"{cls._PREFIX}:run:{run_id}:owner"
