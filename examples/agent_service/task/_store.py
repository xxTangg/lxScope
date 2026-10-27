# -*- coding: utf-8 -*-
"""Replaceable in-memory repository for the Task MVP."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Protocol

from ._models import (
    NodeRunRecord,
    RunStatus,
    TaskEventRecord,
    TaskEventDraft,
    TaskRecord,
    TaskRunRecord,
    TaskStatus,
)


def _utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(timezone.utc)


class TaskStore:
    """User-scoped repository isolated from AgentScope StorageBase.

    This first slice keeps persistence in process so it does not require a
    change to the core ``StorageBase`` contract.  The public methods are the
    seam for a Redis/SQL implementation in a later phase.
    """

    def __init__(self) -> None:
        """Create an empty store."""

        self._tasks: dict[tuple[str, str], TaskRecord] = {}
        self._runs: dict[tuple[str, str], TaskRunRecord] = {}
        self._events: dict[str, list[TaskEventRecord]] = {}
        self._lock = asyncio.Lock()

    async def list_tasks(self, user_id: str) -> list[TaskRecord]:
        """List active tasks owned by ``user_id`` in newest-first order."""

        async with self._lock:
            records = [
                record.model_copy(deep=True)
                for (owner, _), record in self._tasks.items()
                if owner == user_id and record.status != TaskStatus.ARCHIVED
            ]
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    async def get_task(self, user_id: str, task_id: str) -> TaskRecord | None:
        """Return one owned task."""

        async with self._lock:
            record = self._tasks.get((user_id, task_id))
            return record.model_copy(deep=True) if record else None

    async def save_task(self, record: TaskRecord) -> TaskRecord:
        """Create or replace one task record."""

        async with self._lock:
            stored = record.model_copy(
                update={"updated_at": _utc_now()},
                deep=True,
            )
            self._tasks[(stored.user_id, stored.id)] = stored
            return stored.model_copy(deep=True)

    async def delete_task(self, user_id: str, task_id: str) -> bool:
        """Archive a task without deleting its run history."""

        async with self._lock:
            key = (user_id, task_id)
            record = self._tasks.get(key)
            if record is None:
                return False
            self._tasks[key] = record.model_copy(
                update={
                    "status": TaskStatus.ARCHIVED,
                    "updated_at": _utc_now(),
                },
                deep=True,
            )
            return True

    async def save_run(self, record: TaskRunRecord) -> TaskRunRecord:
        """Create or replace one run record."""

        async with self._lock:
            key = (record.user_id, record.id)
            existing = self._runs.get(key)
            if existing is not None and (
                existing.engine in {"dag", "sop"}
                or record.engine in {"dag", "sop"}
            ):
                raise TaskStoreConflictError(
                    "Durable Task Run writes must use transition_run()."
                )
            stored = record.model_copy(deep=True)
            if existing is None and stored.engine in {"dag", "sop"}:
                if stored.run_version or stored.event_sequence or stored.owner_token:
                    raise ValueError(
                        "A new durable Task Run must start at version zero without an owner."
                    )
                stored = stored.model_copy(update={"event_sequence": 1})
                self._events.setdefault(stored.id, []).append(
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
                    )
                )
            self._runs[(record.user_id, record.id)] = stored
            return stored.model_copy(deep=True)

    async def get_run(self, user_id: str, run_id: str) -> TaskRunRecord | None:
        """Return one owned run."""

        async with self._lock:
            record = self._runs.get((user_id, run_id))
            return record.model_copy(deep=True) if record else None

    async def list_runs(self, user_id: str, task_id: str) -> list[TaskRunRecord]:
        """List runs for one owned task."""

        async with self._lock:
            records = [
                record.model_copy(deep=True)
                for (owner, _), record in self._runs.items()
                if owner == user_id and record.task_id == task_id
            ]
        return sorted(records, key=lambda record: record.created_at, reverse=True)

    async def update_run(self, user_id: str, run_id: str, **updates: object) -> TaskRunRecord:
        """Apply a small atomic update to one run."""

        async with self._lock:
            key = (user_id, run_id)
            record = self._runs[key]
            if record.engine in {"dag", "sop"}:
                raise TaskStoreConflictError(
                    "Durable Task Run writes must use transition_run()."
                )
            updated = record.model_copy(update=updates, deep=True)
            self._runs[key] = updated
            return updated.model_copy(deep=True)

    async def update_node_run(
        self,
        user_id: str,
        run_id: str,
        node_id: str,
        **updates: object,
    ) -> TaskRunRecord:
        """Apply an atomic update to one node result."""

        async with self._lock:
            key = (user_id, run_id)
            record = self._runs[key]
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
            updated = record.model_copy(update={"node_runs": node_runs}, deep=True)
            self._runs[key] = updated
            return updated.model_copy(deep=True)

    async def append_event(self, event: TaskEventRecord) -> TaskEventRecord:
        """Append a replayable event."""

        async with self._lock:
            run = next(
                (
                    record
                    for (_, run_id), record in self._runs.items()
                    if run_id == event.run_id
                ),
                None,
            )
            if run is not None and run.engine in {"dag", "sop"}:
                raise TaskStoreConflictError(
                    "Durable Task Run events must be appended through transition_run()."
                )
            events = self._events.setdefault(event.run_id, [])
            events.append(event.model_copy(deep=True))
            return event.model_copy(deep=True)

    async def list_events(self, user_id: str, run_id: str) -> list[TaskEventRecord]:
        """Return events after validating that the run belongs to the user."""

        async with self._lock:
            if (user_id, run_id) not in self._runs:
                return []
            return [event.model_copy(deep=True) for event in self._events.get(run_id, [])]

    async def next_event_sequence(self, run_id: str) -> int:
        """Return the next one-based event sequence for a run."""

        async with self._lock:
            return len(self._events.get(run_id, [])) + 1

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
        """Atomically compare, patch, version and append events for one run."""

        async with self._lock:
            key = (user_id, run_id)
            current = self._runs.get(key)
            if current is None:
                raise KeyError(f"Unknown task run {run_id!r}.")
            _require_dag_run(current)
            if release_owner and owner_token is None:
                raise ValueError("A current owner token is required to release a lease.")
            if command_id and command_id in current.processed_command_ids:
                return current.model_copy(deep=True)
            _check_expected_version(current, expected_version)
            _check_owner(current, owner_token, expected_fence_epoch)
            updated, events = _apply_run_transition(
                current,
                expected_version=expected_version,
                run_patch=run_patch or {},
                node_patches=node_patches or {},
                attempt_events=attempt_events or [],
                audit_events=audit_events or [],
                command_id=command_id,
                release_owner=release_owner,
                persisted_event_count=len(self._events.get(run_id, [])),
            )
            self._runs[key] = updated
            self._events.setdefault(run_id, []).extend(events)
            return updated.model_copy(deep=True)

    async def claim_run(
        self,
        user_id: str,
        run_id: str,
        *,
        owner_token: str,
        lease_seconds: float = 30.0,
        audit_events: list[TaskEventDraft] | None = None,
    ) -> TaskRunRecord:
        """Claim one run or advance its fencing epoch after lease expiry."""

        if not owner_token or lease_seconds <= 0:
            raise ValueError("A claim token and positive lease are required.")
        now = _utc_now()
        async with self._lock:
            key = (user_id, run_id)
            current = self._runs.get(key)
            if current is None:
                raise KeyError(f"Unknown task run {run_id!r}.")
            _require_dag_run(current)
            _require_claimable(current)
            lease_active = (
                current.owner_token is not None
                and current.lease_expires_at is not None
                and current.lease_expires_at > now
            )
            if lease_active and current.owner_token != owner_token:
                raise TaskStoreConflictError("Task Run is owned by another worker.")
            same_live_owner = lease_active and current.owner_token == owner_token
            updated, events = _apply_run_transition(
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
                persisted_event_count=len(self._events.get(run_id, [])),
                allow_owner_patch=True,
            )
            self._runs[key] = updated
            self._events.setdefault(run_id, []).extend(events)
            return updated.model_copy(deep=True)


class TaskStoreProtocol(Protocol):
    """Persistence boundary shared by in-memory and Redis repositories."""

    async def list_tasks(self, user_id: str) -> list[TaskRecord]: ...

    async def get_task(self, user_id: str, task_id: str) -> TaskRecord | None: ...

    async def save_task(self, record: TaskRecord) -> TaskRecord: ...

    async def delete_task(self, user_id: str, task_id: str) -> bool: ...

    async def save_run(self, record: TaskRunRecord) -> TaskRunRecord: ...

    async def get_run(self, user_id: str, run_id: str) -> TaskRunRecord | None: ...

    async def list_runs(self, user_id: str, task_id: str) -> list[TaskRunRecord]: ...

    async def update_run(
        self,
        user_id: str,
        run_id: str,
        **updates: object,
    ) -> TaskRunRecord: ...

    async def update_node_run(
        self,
        user_id: str,
        run_id: str,
        node_id: str,
        **updates: object,
    ) -> TaskRunRecord: ...

    async def append_event(self, event: TaskEventRecord) -> TaskEventRecord: ...

    async def list_events(self, user_id: str, run_id: str) -> list[TaskEventRecord]: ...

    async def next_event_sequence(self, run_id: str) -> int: ...

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
    ) -> TaskRunRecord: ...

    async def claim_run(
        self,
        user_id: str,
        run_id: str,
        *,
        owner_token: str,
        lease_seconds: float = 30.0,
        audit_events: list[TaskEventDraft] | None = None,
    ) -> TaskRunRecord: ...


class TaskStoreConflictError(RuntimeError):
    """A compare-and-set or owner check rejected a stale state transition."""


_PROTECTED_RUN_FIELDS = {
    "run_version",
    "event_sequence",
    "owner_token",
    "lease_expires_at",
    "fence_epoch",
    "processed_command_ids",
}


def _check_expected_version(
    current: TaskRunRecord,
    expected_version: int,
) -> None:
    if current.run_version != expected_version:
        raise TaskStoreConflictError(
            f"Task Run version conflict: expected {expected_version}, "
            f"found {current.run_version}."
        )


def _require_dag_run(current: TaskRunRecord) -> None:
    if current.engine not in {"dag", "sop", "legacy"}:
        raise ValueError(
            "Atomic transitions are only valid for durable Task Runs."
        )


def _require_claimable(current: TaskRunRecord) -> None:
    if current.status in {
        RunStatus.AWAITING_INPUT,
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELED,
        RunStatus.TIMED_OUT,
    } or current.control_mode in {"stop_requested", "stopped"}:
        raise TaskStoreConflictError("Task Run is not eligible for worker claim.")


def _check_owner(
    current: TaskRunRecord,
    owner_token: str | None,
    expected_fence_epoch: int | None,
) -> None:
    if owner_token is None:
        return
    if current.status in {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELED,
        RunStatus.TIMED_OUT,
    }:
        raise TaskStoreConflictError("Task Run is already terminal.")
    if (
        current.owner_token != owner_token
        or expected_fence_epoch is None
        or current.fence_epoch != expected_fence_epoch
        or current.lease_expires_at is None
        or current.lease_expires_at <= _utc_now()
    ):
        raise TaskStoreConflictError("Task Run owner lease or fencing epoch is stale.")


def _apply_run_transition(
    current: TaskRunRecord,
    *,
    expected_version: int,
    run_patch: dict[str, object],
    node_patches: dict[str, dict[str, object]],
    attempt_events: list[TaskEventDraft],
    audit_events: list[TaskEventDraft],
    command_id: str | None,
    release_owner: bool,
    persisted_event_count: int,
    allow_owner_patch: bool = False,
) -> tuple[TaskRunRecord, list[TaskEventRecord]]:
    _check_expected_version(current, expected_version)
    protected = _PROTECTED_RUN_FIELDS - (
        {"owner_token", "lease_expires_at", "fence_epoch"}
        if allow_owner_patch
        else set()
    )
    if protected.intersection(run_patch):
        raise ValueError("Run version, event sequence, and command history are store-owned.")
    if not allow_owner_patch and {
        "owner_token",
        "lease_expires_at",
        "fence_epoch",
    }.intersection(run_patch):
        raise ValueError("Owner fields may only change through claim_run().")
    if command_id and len(current.processed_command_ids) >= 512:
        raise TaskStoreConflictError("Task Run command idempotency history is full.")

    data = current.model_dump()
    patched_node_ids: set[str] = set()
    new_node_runs: list[NodeRunRecord] = []
    for node_run in current.node_runs:
        updates = node_patches.get(node_run.node_id)
        if updates is None:
            new_node_runs.append(node_run.model_copy(deep=True))
            continue
        patched_node_ids.add(node_run.node_id)
        new_node_runs.append(
            NodeRunRecord.model_validate(
                {**node_run.model_dump(), **updates}
            )
        )
    unknown_node_ids = set(node_patches) - patched_node_ids
    if unknown_node_ids:
        raise KeyError(
            "Unknown task node(s) in run: " + ", ".join(sorted(unknown_node_ids))
        )

    data.update(run_patch)
    data["node_runs"] = new_node_runs
    data["run_version"] = current.run_version + 1
    if command_id:
        data["processed_command_ids"] = [
            *current.processed_command_ids,
            command_id,
        ]
    if release_owner:
        if current.owner_token is None:
            raise TaskStoreConflictError("Task Run has no active owner to release.")
        data["owner_token"] = None
        data["lease_expires_at"] = None

    sequence = max(current.event_sequence, persisted_event_count)
    drafts = [*attempt_events, *audit_events]
    if not drafts:
        drafts = [TaskEventDraft(type="run.state_changed")]
    data["event_sequence"] = sequence + len(drafts)
    updated = TaskRunRecord.model_validate(data)
    events = [
        TaskEventRecord(
            type=draft.type,
            task_id=current.task_id,
            run_id=current.id,
            node_id=draft.node_id,
            sequence=sequence + index,
            payload=draft.payload,
        )
        for index, draft in enumerate(drafts, start=1)
    ]
    return updated, events
