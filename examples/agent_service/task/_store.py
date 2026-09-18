# -*- coding: utf-8 -*-
"""Replaceable in-memory repository for the Task MVP."""

import asyncio
from datetime import datetime, timezone

from ._models import (
    NodeRunRecord,
    TaskEventRecord,
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
            self._runs[(record.user_id, record.id)] = record.model_copy(deep=True)
            return record.model_copy(deep=True)

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
