# -*- coding: utf-8 -*-
"""Atomic Task DAG store tests using isolated in-memory and FakeRedis stores."""

import asyncio
from datetime import datetime, timedelta, timezone

import fakeredis.aioredis
import pytest

from examples.agent_service.task._models import (
    AgentStepConfig,
    NodeRunRecord,
    NodeRunStatus,
    TaskEventRecord,
    RunStatus,
    TaskEventDraft,
    TaskNode,
    TaskRunRecord,
)
from examples.agent_service.task._redis_store import RedisTaskStore
from examples.agent_service.task._store import (
    TaskStore,
    TaskStoreConflictError,
)


def _run(run_id: str = "dag-run") -> TaskRunRecord:
    nodes = [
        TaskNode(id="A", name="A", config=AgentStepConfig(prompt="A")),
        TaskNode(id="B", name="B", config=AgentStepConfig(prompt="B")),
    ]
    return TaskRunRecord(
        id=run_id,
        task_id="dag-task",
        user_id="owner",
        task_revision=1,
        nodes=nodes,
        node_runs=[
            NodeRunRecord(node_id="A", name="A", prompt="A", order=0),
            NodeRunRecord(node_id="B", name="B", prompt="B", order=1),
        ],
        status=RunStatus.RUNNING,
        engine="dag",
    )


async def _apply_100_concurrent_updates(store, run_id: str) -> None:
    async def update(index: int) -> None:
        for _ in range(500):
            current = await store.get_run("owner", run_id)
            assert current is not None
            metadata = dict(current.node_runs[0].metadata)
            metadata[f"update-{index}"] = index
            try:
                await store.transition_run(
                    "owner",
                    run_id,
                    expected_version=current.run_version,
                    node_patches={"A": {"metadata": metadata}},
                    attempt_events=[
                        TaskEventDraft(
                            type="node.updated",
                            node_id="A",
                            payload={"index": index},
                        )
                    ],
                )
                return
            except TaskStoreConflictError:
                continue
        raise AssertionError("CAS update did not converge after retries")

    await asyncio.gather(*(update(index) for index in range(100)))
    final = await store.get_run("owner", run_id)
    assert final is not None
    assert len(final.node_runs[0].metadata) == 100
    assert final.run_version == 100
    assert final.event_sequence == 101
    events = await store.list_events("owner", run_id)
    assert [event.sequence for event in events] == list(range(1, 102))
    assert len({event.sequence for event in events}) == 101


@pytest.mark.parametrize("store_kind", ["memory", "redis"])
def test_atomic_transitions_keep_100_concurrent_updates_and_events(store_kind: str) -> None:
    async def scenario() -> None:
        redis_client = None
        if store_kind == "memory":
            store = TaskStore()
        else:
            redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
            store = RedisTaskStore(redis_client)
        try:
            await store.save_run(_run())
            await _apply_100_concurrent_updates(store, "dag-run")
        finally:
            if redis_client is not None:
                await redis_client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("store_kind", ["memory", "redis"])
def test_claim_fencing_rejects_second_worker_and_expired_owner(store_kind: str) -> None:
    async def scenario() -> None:
        redis_client = None
        if store_kind == "memory":
            store = TaskStore()
        else:
            redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
            store = RedisTaskStore(redis_client)
        try:
            await store.save_run(_run())
            first = await store.claim_run(
                "owner",
                "dag-run",
                owner_token="worker-a",
                lease_seconds=0.02,
                audit_events=[TaskEventDraft(type="run.claimed")],
            )
            assert first.owner_token == "worker-a"
            assert first.fence_epoch == 1
            with pytest.raises(TaskStoreConflictError, match="another worker"):
                await store.claim_run(
                    "owner", "dag-run", owner_token="worker-b"
                )

            await asyncio.sleep(0.03)
            second = await store.claim_run(
                "owner",
                "dag-run",
                owner_token="worker-b",
                audit_events=[TaskEventDraft(type="run.claimed")],
            )
            assert second.owner_token == "worker-b"
            assert second.fence_epoch == 2
            with pytest.raises(TaskStoreConflictError, match="stale"):
                await store.transition_run(
                    "owner",
                    "dag-run",
                    expected_version=second.run_version,
                    run_patch={"control_mode": "paused"},
                    owner_token="worker-a",
                    expected_fence_epoch=first.fence_epoch,
                )
            events = await store.list_events("owner", "dag-run")
            assert [event.sequence for event in events] == [1, 2, 3]
        finally:
            if redis_client is not None:
                await redis_client.aclose()

    asyncio.run(scenario())


def test_compare_and_set_conflict_and_duplicate_command_are_idempotent() -> None:
    async def scenario() -> None:
        store = TaskStore()
        await store.save_run(_run())
        current = await store.get_run("owner", "dag-run")
        assert current is not None
        paused = await store.transition_run(
            "owner",
            "dag-run",
            expected_version=current.run_version,
            run_patch={"status": RunStatus.RUNNING, "control_mode": "paused"},
            audit_events=[TaskEventDraft(type="run.paused")],
            command_id="pause-1",
        )
        duplicate = await store.transition_run(
            "owner",
            "dag-run",
            expected_version=0,
            run_patch={"status": RunStatus.RUNNING},
            audit_events=[TaskEventDraft(type="run.resumed")],
            command_id="pause-1",
        )
        assert duplicate == paused
        with pytest.raises(TaskStoreConflictError, match="version conflict"):
            await store.transition_run(
                "owner",
                "dag-run",
                expected_version=0,
                run_patch={"status": RunStatus.RUNNING},
            )
        assert [event.sequence for event in await store.list_events("owner", "dag-run")] == [1, 2]

    asyncio.run(scenario())


@pytest.mark.parametrize("store_kind", ["memory", "redis"])
def test_legacy_snapshot_and_event_writes_cannot_bypass_dag_cas(store_kind: str) -> None:
    async def scenario() -> None:
        redis_client = None
        if store_kind == "memory":
            store = TaskStore()
        else:
            redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
            store = RedisTaskStore(redis_client)
        try:
            await store.save_run(_run())
            current = await store.get_run("owner", "dag-run")
            assert current is not None
            with pytest.raises(TaskStoreConflictError, match="transition_run"):
                await store.update_run(
                    "owner", "dag-run", status=RunStatus.FAILED
                )
            with pytest.raises(TaskStoreConflictError, match="transition_run"):
                await store.append_event(
                    TaskEventRecord(
                        type="bypass",
                        task_id=current.task_id,
                        run_id=current.id,
                        sequence=99,
                    )
                )
        finally:
            if redis_client is not None:
                await redis_client.aclose()

    asyncio.run(scenario())
