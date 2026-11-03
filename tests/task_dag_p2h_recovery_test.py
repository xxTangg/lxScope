# -*- coding: utf-8 -*-
"""Real multi-process Task DAG recovery checks backed by isolated Redis.

All Redis keys are unique to each test. The lease-expiry helper advances only
the exact test Run's timestamp to avoid sleeping for the production 45-second
lease; it does not delete keys or touch other records.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import multiprocessing
import os
from uuid import uuid4

import pytest

from examples.agent_service.task._models import (
    AgentStepConfig,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    StepVerdict,
    TaskGraph,
    TaskNode,
    TaskRecord,
    TaskRunRecord,
    TaskStatus,
)
from examples.agent_service.task._redis_store import RedisTaskStore
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStoreConflictError


_TEST_URL = os.getenv("LXSCOPE_TASK_TEST_REDIS_URL", "")
_ISOLATED = os.getenv("LXSCOPE_TASK_TEST_REDIS_ISOLATED", "")
pytestmark = pytest.mark.skipif(
    not _TEST_URL,
    reason="Set LXSCOPE_TASK_TEST_REDIS_URL to run real recovery tests",
)


def _validated_url() -> str:
    if _ISOLATED != "1":
        raise RuntimeError(
            "Refusing recovery tests without "
            "LXSCOPE_TASK_TEST_REDIS_ISOLATED=1. Use a disposable test instance."
        )
    if not _TEST_URL.startswith(("redis://", "rediss://")):
        raise RuntimeError("Redis test URL must use redis:// or rediss://.")
    return _TEST_URL


def _records(*, status: RunStatus = RunStatus.QUEUED) -> tuple[TaskRecord, TaskRunRecord]:
    suffix = uuid4().hex
    user_id = f"p2h-recovery-owner-{suffix}"
    task_id = f"p2h-recovery-task-{suffix}"
    node = TaskNode(
        id="A",
        name="A",
        config=AgentStepConfig(prompt="Deterministic recovery node"),
    )
    graph = TaskGraph(max_concurrency=1)
    task = TaskRecord(
        id=task_id,
        user_id=user_id,
        title="P2-H isolated recovery fixture",
        goal="Exercise persisted Task DAG recovery without external services.",
        nodes=[node],
        graph=graph,
        status=TaskStatus.ACTIVE,
    )
    run = TaskRunRecord(
        id=f"p2h-recovery-run-{suffix}",
        task_id=task_id,
        user_id=user_id,
        task_revision=task.revision,
        input="isolated recovery input",
        nodes=[node],
        node_runs=[
            NodeRunRecord(
                node_id=node.id,
                name=node.name,
                prompt=node.prompt,
                order=0,
                status=NodeRunStatus.PENDING,
            )
        ],
        status=status,
        engine="dag",
        graph=graph,
    )
    task = task.model_copy(
        update={"last_run_id": run.id, "last_run_status": status}
    )
    return task, run


def _keys(task: TaskRecord, run: TaskRunRecord, *extra: str) -> list[str]:
    return [
        RedisTaskStore._task_key(task.user_id, task.id),
        RedisTaskStore._task_index_key(task.user_id),
        RedisTaskStore._run_key(run.user_id, run.id),
        RedisTaskStore._run_index_key(run.user_id, run.task_id),
        RedisTaskStore._run_owner_key(run.id),
        RedisTaskStore._event_key(run.id),
        *extra,
    ]


def _save_fixture_process(redis_url: str, task_json: str, run_json: str) -> None:
    async def save() -> None:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            store = RedisTaskStore(client)
            task = TaskRecord.model_validate_json(task_json)
            run = TaskRunRecord.model_validate_json(run_json)
            await store.save_task(task)
            await store.save_run(run)
        finally:
            await client.aclose()

    asyncio.run(save())


class _RecoveryExecutor:
    def __init__(self, client, marker_key: str, entered_key: str, mode: str) -> None:
        self.client = client
        self.marker_key = marker_key
        self.entered_key = entered_key
        self.mode = mode

    async def execute_node(self, *, node, previous_output, context):
        from examples.agent_service.task._executor import StepExecutionResult

        del previous_output, context
        await self.client.incr(self.marker_key)
        if self.mode == "block_in_node":
            await self.client.set(self.entered_key, "1")
            while True:
                await asyncio.sleep(0.02)
        return StepExecutionResult(
            text=f"P2-H recovered {node.id}",
            result={"node_id": node.id, "recovery_test": True},
        )

    async def verify_node(self, *, node, output, context):
        del node, output, context
        return StepVerdict(passed=True, message="Deterministic recovery test verifier passed.")


class _CrashBeforeCheckpointService(TaskService):
    async def _dag_transition(self, run_id: str, **kwargs):
        from examples.agent_service.task._models import NodeRunStatus

        patches = kwargs.get("node_patches") or {}
        if any(
            patch.get("status") is NodeRunStatus.SUCCEEDED
            or patch.get("status") == NodeRunStatus.SUCCEEDED.value
            for patch in patches.values()
        ):
            os._exit(86)
        return await super()._dag_transition(run_id, **kwargs)


def _recovery_process(
    redis_url: str,
    user_id: str,
    run_id: str,
    marker_key: str,
    entered_key: str,
    mode: str,
    start_gate,
) -> None:
    if start_gate is not None:
        start_gate.wait(15)

    async def recover() -> None:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        service = None
        try:
            store = RedisTaskStore(client)
            executor = _RecoveryExecutor(client, marker_key, entered_key, mode)
            service_class = (
                _CrashBeforeCheckpointService
                if mode == "crash_before_success_checkpoint"
                else TaskService
            )
            service = service_class(
                store,
                preview_executor=executor,
                enable_dag_scheduler=True,
            )
            await service.get_run(user_id, run_id)
            active = service._active_runs.get(run_id)
            if active is not None:
                await asyncio.wait_for(active, timeout=15)
        finally:
            if service is not None:
                await service.shutdown()
            await client.aclose()

    asyncio.run(recover())


def _stale_writer_process(
    redis_url: str,
    user_id: str,
    run_id: str,
    stale_token: str,
    stale_epoch: int,
    go,
    results,
) -> None:
    async def write_late() -> None:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            store = RedisTaskStore(client)
            await asyncio.to_thread(go.wait, 15)
            current = await store.get_run(user_id, run_id)
            if current is None:
                results.put("missing")
                return
            try:
                await store.transition_run(
                    user_id,
                    run_id,
                    expected_version=current.run_version,
                    run_patch={"control_mode": "paused"},
                    owner_token=stale_token,
                    expected_fence_epoch=stale_epoch,
                )
            except TaskStoreConflictError:
                results.put("fenced")
            else:
                results.put("overwrote")
        finally:
            await client.aclose()

    asyncio.run(write_late())


async def _open_client():
    from redis.asyncio import Redis

    client = Redis.from_url(_validated_url(), decode_responses=True)
    await client.ping()
    return client


async def _seed_in_process(client, task: TaskRecord, run: TaskRunRecord) -> None:
    store = RedisTaskStore(client)
    await store.save_task(task)
    await store.save_run(run)


async def _wait_for_key(client, key: str, timeout: float = 12) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await client.get(key):
            return True
        await asyncio.sleep(0.03)
    return False


async def _expire_only_this_test_lease(client, run: TaskRunRecord) -> None:
    """Fast-forward the owned test Run's lease after its worker has exited."""

    store = RedisTaskStore(client)
    current = await store.get_run(run.user_id, run.id)
    assert current is not None and current.owner_token is not None
    expired = current.model_copy(
        update={
            "lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)
        }
    )
    await client.set(
        RedisTaskStore._run_key(run.user_id, run.id),
        expired.model_dump_json(),
    )


async def _join(process, timeout: float = 20) -> None:
    await asyncio.to_thread(process.join, timeout)
    assert not process.is_alive(), f"child process {process.name} did not exit"
    assert process.exitcode == 0, f"child process exited with {process.exitcode}"


@pytest.mark.parametrize("initial_status", [RunStatus.QUEUED, RunStatus.RUNNING])
def test_process_restart_recovers_queued_or_pre_node_run(initial_status) -> None:
    async def scenario() -> None:
        client = await _open_client()
        task, run = _records(status=initial_status)
        marker = f"lxscope:task:p2h:{run.id}:node-executions"
        entered = f"lxscope:task:p2h:{run.id}:node-entered"
        context = multiprocessing.get_context("spawn")
        seed = context.Process(
            target=_save_fixture_process,
            args=(_validated_url(), task.model_dump_json(), run.model_dump_json()),
        )
        recovery = context.Process(
            target=_recovery_process,
            args=(
                _validated_url(),
                run.user_id,
                run.id,
                marker,
                entered,
                "normal",
                None,
            ),
        )
        try:
            seed.start()
            await _join(seed)
            # The seeding process exits after its durable commit; the next
            # process performs TaskService's lazy recovery on get_run().
            recovery.start()
            await _join(recovery)
            stored = await RedisTaskStore(client).get_run(run.user_id, run.id)
            assert stored is not None
            assert stored.status is RunStatus.SUCCEEDED
            assert await client.get(marker) == "1"
        finally:
            for process in (seed, recovery):
                if process.is_alive():
                    process.terminate()
                    await asyncio.to_thread(process.join, 5)
            await client.unlink(*_keys(task, run, marker, entered))
            await client.aclose()

    asyncio.run(scenario())


def test_node_success_before_checkpoint_requires_manual_verification() -> None:
    async def scenario() -> None:
        client = await _open_client()
        task, run = _records()
        marker = f"lxscope:task:p2h:{run.id}:node-executions"
        entered = f"lxscope:task:p2h:{run.id}:node-entered"
        crash = multiprocessing.get_context("spawn").Process(
            target=_recovery_process,
            args=(
                _validated_url(),
                run.user_id,
                run.id,
                marker,
                entered,
                "crash_before_success_checkpoint",
                None,
            ),
        )
        recovery = None
        try:
            await _seed_in_process(client, task, run)
            crash.start()
            await asyncio.to_thread(crash.join, 20)
            assert not crash.is_alive()
            assert crash.exitcode == 86
            assert await client.get(marker) == "1"
            current = await RedisTaskStore(client).get_run(run.user_id, run.id)
            assert current is not None
            assert current.node_runs[0].status is NodeRunStatus.RUNNING
            await _expire_only_this_test_lease(client, run)

            recovery = multiprocessing.get_context("spawn").Process(
                target=_recovery_process,
                args=(
                    _validated_url(),
                    run.user_id,
                    run.id,
                    marker,
                    entered,
                    "normal",
                    None,
                ),
            )
            recovery.start()
            await _join(recovery)
            recovered = await RedisTaskStore(client).get_run(run.user_id, run.id)
            events = await RedisTaskStore(client).list_events(run.user_id, run.id)
            assert recovered is not None
            assert recovered.status is RunStatus.FAILED
            assert recovered.node_runs[0].metadata["recovery_required"] is True
            assert (
                recovered.node_runs[0].metadata["recovery_policy"]
                == "manual_verification"
            )
            assert await client.get(marker) == "1"
            assert any(event.type == "step.recovery_required" for event in events)
        finally:
            for process in (crash, recovery):
                if process is not None and process.is_alive():
                    process.terminate()
                    await asyncio.to_thread(process.join, 5)
            await client.unlink(*_keys(task, run, marker, entered))
            await client.aclose()

    asyncio.run(scenario())


def test_worker_killed_during_node_is_not_automatically_replayed() -> None:
    async def scenario() -> None:
        client = await _open_client()
        task, run = _records()
        marker = f"lxscope:task:p2h:{run.id}:node-executions"
        entered = f"lxscope:task:p2h:{run.id}:node-entered"
        worker = multiprocessing.get_context("spawn").Process(
            target=_recovery_process,
            args=(
                _validated_url(),
                run.user_id,
                run.id,
                marker,
                entered,
                "block_in_node",
                None,
            ),
        )
        recovery = None
        try:
            await _seed_in_process(client, task, run)
            worker.start()
            assert await _wait_for_key(client, entered)
            worker.terminate()
            await asyncio.to_thread(worker.join, 8)
            assert not worker.is_alive()
            assert await client.get(marker) == "1"
            current = await RedisTaskStore(client).get_run(run.user_id, run.id)
            assert current is not None
            assert current.node_runs[0].status is NodeRunStatus.RUNNING
            await _expire_only_this_test_lease(client, run)

            recovery = multiprocessing.get_context("spawn").Process(
                target=_recovery_process,
                args=(
                    _validated_url(),
                    run.user_id,
                    run.id,
                    marker,
                    entered,
                    "normal",
                    None,
                ),
            )
            recovery.start()
            await _join(recovery)
            recovered = await RedisTaskStore(client).get_run(run.user_id, run.id)
            assert recovered is not None
            assert recovered.status is RunStatus.FAILED
            assert recovered.node_runs[0].metadata["recovery_policy"] == "manual_verification"
            assert await client.get(marker) == "1"
        finally:
            for process in (worker, recovery):
                if process is not None and process.is_alive():
                    process.terminate()
                    await asyncio.to_thread(process.join, 5)
            await client.unlink(*_keys(task, run, marker, entered))
            await client.aclose()

    asyncio.run(scenario())


def test_two_process_recovery_requests_execute_only_one_worker() -> None:
    async def scenario() -> None:
        client = await _open_client()
        task, run = _records()
        marker = f"lxscope:task:p2h:{run.id}:node-executions"
        entered = f"lxscope:task:p2h:{run.id}:node-entered"
        context = multiprocessing.get_context("spawn")
        gate = context.Event()
        processes = [
            context.Process(
                target=_recovery_process,
                args=(
                    _validated_url(),
                    run.user_id,
                    run.id,
                    marker,
                    entered,
                    "normal",
                    gate,
                ),
            )
            for _ in range(2)
        ]
        try:
            await _seed_in_process(client, task, run)
            for process in processes:
                process.start()
            gate.set()
            for process in processes:
                await _join(process)
            stored = await RedisTaskStore(client).get_run(run.user_id, run.id)
            assert stored is not None and stored.status is RunStatus.SUCCEEDED
            assert await client.get(marker) == "1"
            events = await RedisTaskStore(client).list_events(run.user_id, run.id)
            assert sum(event.type == "run.claimed" for event in events) == 1
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    await asyncio.to_thread(process.join, 5)
            await client.unlink(*_keys(task, run, marker, entered))
            await client.aclose()

    asyncio.run(scenario())


def test_expired_lease_fences_a_late_worker_process_write() -> None:
    async def scenario() -> None:
        client = await _open_client()
        task, run = _records()
        context = multiprocessing.get_context("spawn")
        go = context.Event()
        results = context.Queue()
        stale_process = None
        try:
            store = RedisTaskStore(client)
            await store.save_task(task)
            await store.save_run(run)
            first = await store.claim_run(
                run.user_id,
                run.id,
                owner_token="old-process-worker",
                lease_seconds=30,
            )
            stale_process = context.Process(
                target=_stale_writer_process,
                args=(
                    _validated_url(),
                    run.user_id,
                    run.id,
                    first.owner_token,
                    first.fence_epoch,
                    go,
                    results,
                ),
            )
            stale_process.start()
            await _expire_only_this_test_lease(client, run)
            second = await store.claim_run(
                run.user_id,
                run.id,
                owner_token="new-process-worker",
                lease_seconds=30,
            )
            assert second.fence_epoch == first.fence_epoch + 1
            go.set()
            await _join(stale_process)
            assert results.get(timeout=3) == "fenced"
            latest = await store.get_run(run.user_id, run.id)
            assert latest is not None
            assert latest.owner_token == "new-process-worker"
            assert latest.control_mode == "continuous"
        finally:
            if stale_process is not None and stale_process.is_alive():
                stale_process.terminate()
                await asyncio.to_thread(stale_process.join, 5)
            await client.unlink(*_keys(task, run))
            await client.aclose()
            results.close()

    asyncio.run(scenario())
