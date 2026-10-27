# -*- coding: utf-8 -*-
"""Real Redis integration tests for Task DAG persistence and fencing.

These tests write only random, test-owned ``lxscope:task`` keys. They never
call FLUSHDB/FLUSHALL and must only be enabled for a disposable Redis service.
Set LXSCOPE_TASK_TEST_REDIS_URL and confirm isolation with
LXSCOPE_TASK_TEST_REDIS_ISOLATED=1.
"""

import asyncio
import multiprocessing
import os
from uuid import uuid4

import pytest

from examples.agent_service.task._models import (
    AgentStepConfig,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    TaskEventDraft,
    TaskEventRecord,
    TaskGraph,
    TaskNode,
    TaskRunRecord,
)
from examples.agent_service.task._redis_store import RedisTaskStore
from examples.agent_service.task._store import TaskStoreConflictError


_TEST_URL = os.getenv("LXSCOPE_TASK_TEST_REDIS_URL", "")
_ISOLATED = os.getenv("LXSCOPE_TASK_TEST_REDIS_ISOLATED", "")
pytestmark = pytest.mark.skipif(
    not _TEST_URL,
    reason="Set LXSCOPE_TASK_TEST_REDIS_URL to run real Redis tests",
)


def _validated_url() -> str:
    if _ISOLATED != "1":
        raise RuntimeError(
            "Refusing Redis integration tests without "
            "LXSCOPE_TASK_TEST_REDIS_ISOLATED=1. Use a disposable test instance."
        )
    if not _TEST_URL.startswith(("redis://", "rediss://")):
        raise RuntimeError("Redis test URL must use redis:// or rediss://.")
    return _TEST_URL


def _new_run() -> TaskRunRecord:
    suffix = uuid4().hex
    user_id = f"p2h-redis-owner-{suffix}"
    task_id = f"p2h-redis-task-{suffix}"
    node = TaskNode(
        id="A",
        name="A",
        config=AgentStepConfig(prompt="Redis integration fixture"),
    )
    return TaskRunRecord(
        id=f"p2h-redis-run-{suffix}",
        task_id=task_id,
        user_id=user_id,
        task_revision=1,
        nodes=[node],
        node_runs=[NodeRunRecord(node_id="A", name="A", prompt=node.prompt, order=0)],
        status=RunStatus.RUNNING,
        engine="dag",
        graph=TaskGraph(max_concurrency=1),
    )


def _owned_keys(run: TaskRunRecord, *extra: str) -> list[str]:
    return [
        RedisTaskStore._run_key(run.user_id, run.id),
        RedisTaskStore._run_index_key(run.user_id, run.task_id),
        RedisTaskStore._run_owner_key(run.id),
        RedisTaskStore._event_key(run.id),
        *extra,
    ]


async def _open_redis():
    from redis.asyncio import Redis

    client = Redis.from_url(_validated_url(), decode_responses=True)
    await client.ping()
    return client


async def _save_fixture(client, run: TaskRunRecord) -> RedisTaskStore:
    store = RedisTaskStore(client)
    await store.save_run(run)
    return store


class _ConflictPipeline:
    """Force real WATCH invalidations before each transaction executes."""

    def __init__(self, pipeline, client, key: str, attempts: list[int]) -> None:
        self._pipeline = pipeline
        self._client = client
        self._key = key
        self._attempts = attempts

    async def __aenter__(self):
        await self._pipeline.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return await self._pipeline.__aexit__(exc_type, exc, traceback)

    def __getattr__(self, name):
        return getattr(self._pipeline, name)

    async def execute(self):
        self._attempts[0] += 1
        raw = await self._client.get(self._key)
        await self._client.set(self._key, raw)
        return await self._pipeline.execute()


class _ConflictInjectingClient:
    def __init__(self, client, key: str, attempts: list[int]) -> None:
        self._client = client
        self._key = key
        self._attempts = attempts

    def pipeline(self, *args, **kwargs):
        return _ConflictPipeline(
            self._client.pipeline(*args, **kwargs),
            self._client,
            self._key,
            self._attempts,
        )


def _claim_process(
    redis_url: str,
    user_id: str,
    run_id: str,
    token: str,
    barrier,
    results,
) -> None:
    async def claim():
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            await asyncio.to_thread(barrier.wait, 15)
            store = RedisTaskStore(client)
            try:
                record = await store.claim_run(
                    user_id,
                    run_id,
                    owner_token=token,
                    lease_seconds=15,
                )
                return "claimed", token, record.fence_epoch
            except TaskStoreConflictError:
                return "conflict", token, None
        finally:
            await client.aclose()

    try:
        results.put(asyncio.run(claim()))
    except BaseException as error:  # surfaced in the parent test process
        results.put(("error", token, repr(error)))
        raise


def test_real_redis_keeps_100_concurrent_cas_updates_and_events() -> None:
    async def scenario() -> None:
        client = await _open_redis()
        run = _new_run()
        try:
            store = await _save_fixture(client, run)
            ready = 0
            start = asyncio.Event()
            conflict_count = 0

            async def update(index: int) -> None:
                nonlocal ready, conflict_count
                ready += 1
                if ready == 100:
                    start.set()
                await start.wait()
                for _ in range(500):
                    current = await store.get_run(run.user_id, run.id)
                    assert current is not None
                    metadata = dict(current.node_runs[0].metadata)
                    metadata[f"update-{index}"] = index
                    try:
                        await store.transition_run(
                            run.user_id,
                            run.id,
                            expected_version=current.run_version,
                            node_patches={"A": {"metadata": metadata}},
                            audit_events=[
                                TaskEventDraft(
                                    type="p2h.cas.updated",
                                    payload={"index": index},
                                )
                            ],
                        )
                        return
                    except TaskStoreConflictError:
                        conflict_count += 1
                raise AssertionError("Redis CAS did not converge")

            await asyncio.gather(*(update(index) for index in range(100)))
            current = await store.get_run(run.user_id, run.id)
            events = await store.list_events(run.user_id, run.id)
            assert current is not None
            assert len(current.node_runs[0].metadata) == 100
            assert current.run_version == 100
            assert current.event_sequence == 101
            assert [event.sequence for event in events] == list(range(1, 102))
            assert conflict_count > 0
        finally:
            await client.unlink(*_owned_keys(run))
            await client.aclose()

    asyncio.run(scenario())


def test_real_redis_cas_retry_exhaustion_is_bounded_and_non_mutating() -> None:
    async def scenario() -> None:
        client = await _open_redis()
        run = _new_run()
        try:
            store = await _save_fixture(client, run)
            attempts = [0]
            key = RedisTaskStore._run_key(run.user_id, run.id)
            conflict_store = RedisTaskStore(
                _ConflictInjectingClient(client, key, attempts)
            )
            with pytest.raises(
                TaskStoreConflictError,
                match="Redis CAS retry limit was exceeded",
            ):
                await conflict_store.transition_run(
                    run.user_id,
                    run.id,
                    expected_version=0,
                    run_patch={"control_mode": "paused"},
                    audit_events=[TaskEventDraft(type="must.not.persist")],
                )
            current = await store.get_run(run.user_id, run.id)
            events = await store.list_events(run.user_id, run.id)
            assert attempts[0] == 16
            assert current is not None
            assert current.run_version == 0
            assert current.control_mode == "continuous"
            assert [event.type for event in events] == ["run.created"]
        finally:
            await client.unlink(*_owned_keys(run))
            await client.aclose()

    asyncio.run(scenario())


def test_real_redis_lease_renewal_expiry_fencing_and_terminal_guard() -> None:
    async def scenario() -> None:
        client = await _open_redis()
        run = _new_run()
        try:
            store = await _save_fixture(client, run)
            first = await store.claim_run(
                run.user_id,
                run.id,
                owner_token="worker-a",
                lease_seconds=0.3,
                audit_events=[TaskEventDraft(type="run.claimed")],
            )
            with pytest.raises(TaskStoreConflictError, match="another worker"):
                await store.claim_run(
                    run.user_id,
                    run.id,
                    owner_token="worker-b",
                )
            await asyncio.sleep(0.05)
            renewed = await store.claim_run(
                run.user_id,
                run.id,
                owner_token="worker-a",
                lease_seconds=0.35,
            )
            assert renewed.fence_epoch == first.fence_epoch == 1
            assert renewed.lease_expires_at > first.lease_expires_at

            await asyncio.sleep(0.4)
            second = await store.claim_run(
                run.user_id,
                run.id,
                owner_token="worker-b",
            )
            assert second.fence_epoch == 2
            with pytest.raises(TaskStoreConflictError, match="stale"):
                await store.transition_run(
                    run.user_id,
                    run.id,
                    expected_version=second.run_version,
                    run_patch={"control_mode": "paused"},
                    owner_token="worker-a",
                    expected_fence_epoch=first.fence_epoch,
                )

            terminal = await store.transition_run(
                run.user_id,
                run.id,
                expected_version=second.run_version,
                run_patch={"status": RunStatus.SUCCEEDED},
                audit_events=[TaskEventDraft(type="run.succeeded")],
                owner_token="worker-b",
                expected_fence_epoch=second.fence_epoch,
                release_owner=True,
            )
            with pytest.raises(TaskStoreConflictError, match="terminal"):
                await store.transition_run(
                    run.user_id,
                    run.id,
                    expected_version=terminal.run_version,
                    run_patch={"status": RunStatus.FAILED},
                    owner_token="worker-b",
                    expected_fence_epoch=second.fence_epoch,
                )
            with pytest.raises(TaskStoreConflictError, match="not eligible"):
                await store.claim_run(
                    run.user_id,
                    run.id,
                    owner_token="late-worker",
                )
        finally:
            await client.unlink(*_owned_keys(run))
            await client.aclose()

    asyncio.run(scenario())


def test_real_redis_command_idempotency_snapshot_events_and_restart() -> None:
    async def scenario() -> None:
        client = await _open_redis()
        run = _new_run()
        try:
            store = await _save_fixture(client, run)
            original = await store.get_run(run.user_id, run.id)
            assert original is not None
            updated = await store.transition_run(
                run.user_id,
                run.id,
                expected_version=original.run_version,
                run_patch={"control_mode": "paused"},
                audit_events=[
                    TaskEventDraft(
                        type="run.paused",
                        payload={"request_id": "pause-once"},
                    )
                ],
                command_id="run.pause:pause-once",
            )
            duplicate = await store.transition_run(
                run.user_id,
                run.id,
                expected_version=original.run_version,
                run_patch={"control_mode": "continuous"},
                audit_events=[TaskEventDraft(type="duplicate.must.not.persist")],
                command_id="run.pause:pause-once",
            )
            assert duplicate == updated
            events = await store.list_events(run.user_id, run.id)
            assert updated.event_sequence == len(events)
            assert [event.sequence for event in events] == list(
                range(1, len(events) + 1)
            )
            with pytest.raises(TaskStoreConflictError, match="transition_run"):
                await store.update_run(
                    run.user_id,
                    run.id,
                    status=RunStatus.FAILED,
                )
            with pytest.raises(TaskStoreConflictError, match="transition_run"):
                await store.append_event(
                    TaskEventRecord(
                        type="bypass",
                        task_id=run.task_id,
                        run_id=run.id,
                        sequence=999,
                    )
                )

            await client.aclose()
            client = await _open_redis()
            restarted = RedisTaskStore(client)
            restored = await restarted.get_run(run.user_id, run.id)
            restored_events = await restarted.list_events(run.user_id, run.id)
            assert restored == updated
            assert restored_events == events
        finally:
            await client.unlink(*_owned_keys(run))
            await client.aclose()

    asyncio.run(scenario())


def test_two_real_redis_worker_processes_compete_for_one_run_claim() -> None:
    async def scenario() -> None:
        client = await _open_redis()
        run = _new_run()
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        results = context.Queue()
        processes = []
        try:
            await _save_fixture(client, run)
            url = _validated_url()
            for token in ("process-a", "process-b"):
                process = context.Process(
                    target=_claim_process,
                    args=(url, run.user_id, run.id, token, barrier, results),
                )
                processes.append(process)
                process.start()
            for process in processes:
                process.join(20)
            assert all(not process.is_alive() for process in processes)
            assert all(process.exitcode == 0 for process in processes)
            outcomes = [results.get(timeout=3) for _ in processes]
            assert all(outcome[0] != "error" for outcome in outcomes), outcomes
            assert sorted(outcome[0] for outcome in outcomes) == [
                "claimed",
                "conflict",
            ]
            winner = next(outcome for outcome in outcomes if outcome[0] == "claimed")
            current = await RedisTaskStore(client).get_run(run.user_id, run.id)
            assert current is not None
            assert current.owner_token == winner[1]
            assert current.fence_epoch == 1
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            await client.unlink(*_owned_keys(run))
            await client.aclose()
            results.close()

    asyncio.run(scenario())
