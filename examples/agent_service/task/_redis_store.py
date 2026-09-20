# -*- coding: utf-8 -*-
"""Redis repository for Task definitions, runs and replayable events."""

from datetime import datetime, timezone
from typing import Any

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

        await self._client.set(
            self._run_key(record.user_id, record.id),
            record.model_dump_json(),
        )
        await self._client.zadd(
            self._run_index_key(record.user_id, record.task_id),
            {record.id: self._score(record.created_at)},
        )
        return record.model_copy(deep=True)

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
