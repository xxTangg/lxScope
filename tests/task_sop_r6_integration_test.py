# -*- coding: utf-8 -*-
"""R6 real-store and independent-process acceptance for linear SOP Runs.

These tests use only an explicitly configured disposable PostgreSQL or Redis
service. Redis data is isolated by random owner/Run keys and removed by key;
the suite never flushes a database. PostgreSQL tests create and drop only a
random ``lxscope_task_test_r6_*`` database under the supplied test URL.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import time
from uuid import UUID, uuid4
from urllib.parse import urlsplit

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from examples.agent_service.persistence.database import ApplicationDatabase
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    AgentStepConfig,
    CreateTaskRequest,
    InputStepConfig,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    StepVerdict,
    TaskInputQuestion,
    TaskInputReply,
    TaskEventDraft,
    TaskNode,
    TaskRunControlRequest,
    TaskRunRecord,
    TaskRunRequest,
    TaskWorkflowMode,
    ToolStepConfig,
)
from examples.agent_service.task._postgres_store import PostgresTaskStore
from examples.agent_service.task._redis_store import RedisTaskStore
from examples.agent_service.task._sop import TaskSOPRunState, TaskStepState
from examples.agent_service.task._service import TaskService, TaskWorkflowConflictError
from examples.agent_service.task._store import TaskStoreConflictError
from agentscope.sop import SOPPhase


_PG_URL = os.getenv("LXSCOPE_TASK_TEST_DATABASE_URL", "")
_REDIS_URL = os.getenv("LXSCOPE_TASK_TEST_REDIS_URL", "")
_REDIS_ISOLATED = os.getenv("LXSCOPE_TASK_TEST_REDIS_ISOLATED", "")


class _Backend:
    def __init__(self, kind: str, url: str) -> None:
        self.kind = kind
        self.url = url
        self.store = None
        self.database = None
        self.admin_engine = None
        self.client = None
        self.database_name: str | None = None
        self.owned: list[tuple[str, str, str]] = []

    async def open(self) -> "_Backend":
        if self.kind == "postgres":
            base_url = make_url(self.url)
            if base_url.drivername != "postgresql+asyncpg" or not (
                base_url.database or ""
            ).startswith("lxscope_task_test"):
                raise ValueError(
                    "R6 requires a disposable lxscope_task_test PostgreSQL URL"
                )
            if base_url.host not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("R6 refuses non-loopback PostgreSQL hosts")
            self.database_name = f"lxscope_task_test_r6_{uuid4().hex}"
            self.admin_engine = create_async_engine(
                base_url, isolation_level="AUTOCOMMIT"
            )
            async with self.admin_engine.connect() as connection:
                await connection.exec_driver_sql(
                    f'CREATE DATABASE "{self.database_name}"'
                )
            database_url = base_url.set(database=self.database_name).render_as_string(
                hide_password=False
            )
            self.database = ApplicationDatabase(database_url)
            await self.database.initialize()
            self.store = PostgresTaskStore(self.database.engine)
            await self.store.initialize()
            self.url = database_url
            return self

        if self.kind != "redis":
            raise ValueError(f"Unsupported test backend {self.kind!r}")
        if not _REDIS_URL or _REDIS_ISOLATED != "1":
            raise RuntimeError(
                "Real Redis R6 tests require an isolated disposable Redis URL"
            )
        if not _REDIS_URL.startswith(("redis://", "rediss://")):
            raise ValueError("Redis test URL must use redis:// or rediss://")
        redis_parts = urlsplit(_REDIS_URL)
        if redis_parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("R6 refuses non-loopback Redis hosts")
        if redis_parts.path != "/15":
            raise ValueError("R6 requires the isolated Redis test database /15")
        from redis.asyncio import Redis

        self.client = Redis.from_url(_REDIS_URL, decode_responses=True)
        await self.client.ping()
        self.store = RedisTaskStore(self.client)
        self.url = _REDIS_URL
        return self

    async def new_store(self):
        if self.kind == "postgres":
            store = PostgresTaskStore(self.database.engine)
            await store.initialize()
            return store
        return RedisTaskStore(self.client)

    def own(self, task, run) -> None:
        if self.kind == "redis":
            self.owned.extend(
                [
                    ("task", task.user_id, task.id),
                    ("run", run.user_id, run.id),
                    ("run_index", run.user_id, run.task_id),
                ]
            )

    async def close(self) -> None:
        if self.kind == "redis" and self.client is not None:
            keys: set[str] = set()
            for record_kind, user_id, record_id in self.owned:
                # Random per-test user IDs make the index keys test-owned too.
                if record_kind == "task":
                    keys.add(RedisTaskStore._task_key(user_id, record_id))
                    keys.add(RedisTaskStore._task_index_key(user_id))
                elif record_kind == "run":
                    keys.add(RedisTaskStore._run_key(user_id, record_id))
                    keys.add(RedisTaskStore._run_owner_key(record_id))
                    keys.add(RedisTaskStore._event_key(record_id))
                else:
                    keys.add(RedisTaskStore._run_index_key(user_id, record_id))
            if keys:
                await self.client.unlink(*keys)
            await self.client.aclose()
        if self.database is not None:
            await self.database.close()
        if self.admin_engine is not None and self.database_name:
            async with self.admin_engine.connect() as connection:
                await connection.exec_driver_sql(
                    f'DROP DATABASE "{self.database_name}"'
                )
            await self.admin_engine.dispose()


class _RecordingExecutor:
    def __init__(
        self,
        *,
        fail_once: str | None = None,
        block_node: str | None = None,
        marker_path: str | None = None,
        entered_path: str | None = None,
        release_path: str | None = None,
        crash_node: str | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.fail_once = fail_once
        self.block_node = block_node
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.marker_path = marker_path
        self.entered_path = entered_path
        self.release_path = release_path
        self.crash_node = crash_node

    def _record(self, value: str) -> None:
        if self.marker_path:
            with Path(self.marker_path).open("a", encoding="utf-8") as stream:
                stream.write(value + "\n")

    async def execute_node(self, *, node, previous_output, context):
        del previous_output, context
        self.calls.append(node.id)
        self._record(node.id)
        if node.id == self.block_node:
            self.entered.set()
            if self.entered_path:
                Path(self.entered_path).write_text("entered", encoding="utf-8")
            if self.release_path:
                while not Path(self.release_path).exists():
                    await asyncio.sleep(0.02)
            else:
                await self.release.wait()
        # Simulate an already-performed external effect before the checkpoint.
        if node.id == self.crash_node:
            os._exit(86)
        if node.id == self.fail_once:
            self.fail_once = None
            return StepExecutionResult(
                text="synthetic failure",
                status="failed",
                reason="synthetic deterministic failure",
            )
        return StepExecutionResult(
            text=f"R6-SYNTHETIC-{node.id}",
            result={"node_id": node.id, "synthetic": True},
        )

    async def verify_node(self, **kwargs):
        del kwargs
        return StepVerdict(passed=True, message="R6 deterministic verifier passed.")


class _UnscheduledTaskService(TaskService):
    """Use the real create_run path while deliberately leaving a queued seed."""

    def _schedule_run(self, run, knowledge_gateway) -> None:
        del run, knowledge_gateway


def _agent(node_id: str, order: int) -> TaskNode:
    return TaskNode(
        id=node_id,
        name=node_id,
        order=order,
        config=AgentStepConfig(prompt=f"Synthetic R6 node {node_id}"),
    )


async def _create_run(service, user_id: str, nodes: list[TaskNode]):
    task = await service.create_task(
        user_id,
        CreateTaskRequest(
            title="R6 isolated SOP acceptance",
            goal="Synthetic linear SOP storage and recovery test",
            nodes=nodes,
        ),
    )
    run = await service.create_run(
        user_id, task.id, TaskRunRequest(input="Synthetic R6 input")
    )
    assert run is not None
    return task, run


async def _child_store(kind: str, url: str):
    if kind == "postgres":
        database = ApplicationDatabase(url)
        await database.initialize()
        store = PostgresTaskStore(database.engine)
        await store.initialize()
        return store, database, None
    from redis.asyncio import Redis

    client = Redis.from_url(url, decode_responses=True)
    await client.ping()
    return RedisTaskStore(client), None, client


def _worker_process(
    kind: str,
    url: str,
    user_id: str,
    run_id: str,
    marker_path: str,
    action: str,
    results,
    *,
    gate=None,
    block_node: str | None = None,
    entered_path: str | None = None,
    release_path: str | None = None,
    crash_node: str | None = None,
) -> None:
    if gate is not None:
        gate.wait(30)

    async def work() -> None:
        store, database, client = await _child_store(kind, url)
        executor = _RecordingExecutor(
            marker_path=marker_path,
            block_node=block_node,
            entered_path=entered_path,
            release_path=release_path,
            crash_node=crash_node,
        )
        service = TaskService(store, preview_executor=executor)
        try:
            run = await service.get_run(user_id, run_id)
            if run is None:
                raise AssertionError("recovery worker could not load Run")
            if action == "continue":
                await service.continue_run(
                    user_id,
                    run_id,
                    TaskRunControlRequest(
                        request_id="r6-process-continue",
                        expected_run_version=run.run_version,
                    ),
                )
            elif action == "answer":
                if run.pending_input is None:
                    raise AssertionError("recovered Run is not awaiting input")
                await service.answer_run(
                    user_id,
                    run_id,
                    TaskInputReply(
                        request_id=run.pending_input.id,
                        answers={"destination": "Synthetic City"},
                    ),
                )
            active = service._active_runs.get(run_id)
            if active is not None:
                await asyncio.wait_for(active, timeout=40)
            final = await store.get_run(user_id, run_id)
            if final is None:
                raise AssertionError("Run disappeared after worker execution")
            results.put(
                (
                    "ok",
                    final.status.value,
                    [item.status.value for item in final.node_runs],
                    final.run_version,
                    final.fence_epoch,
                )
            )
        finally:
            await service.shutdown()
            if database is not None:
                await database.close()
            if client is not None:
                await client.aclose()

    try:
        asyncio.run(work())
    except BaseException as error:
        results.put(("error", type(error).__name__, str(error)))
        raise


async def _run_child(
    kind: str,
    url: str,
    user_id: str,
    run_id: str,
    marker_path: str,
    action: str,
    *,
    gate=None,
    block_node: str | None = None,
    entered_path: str | None = None,
    release_path: str | None = None,
    crash_node: str | None = None,
):
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    process = context.Process(
        target=_worker_process,
        args=(kind, url, user_id, run_id, marker_path, action, results),
        kwargs={
            "gate": gate,
            "block_node": block_node,
            "entered_path": entered_path,
            "release_path": release_path,
            "crash_node": crash_node,
        },
    )
    process.start()
    return process, results


async def _expire_own_lease(backend: _Backend, run) -> None:
    current = await backend.store.get_run(run.user_id, run.id)
    assert current is not None and current.owner_token is not None
    expired = current.model_copy(
        update={
            "lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)
        }
    )
    if backend.kind == "redis":
        await backend.client.set(
            RedisTaskStore._run_key(run.user_id, run.id),
            expired.model_dump_json(),
        )
        return
    payload = json.dumps(expired.model_dump(mode="json"), ensure_ascii=False)
    async with backend.database.engine.begin() as connection:
        result = await connection.execute(
            text(
                "UPDATE longxin_app.task_runs "
                "SET snapshot=CAST(:snapshot AS JSON) WHERE id=:run_id"
            ),
            {"snapshot": payload, "run_id": UUID(run.id)},
        )
    assert result.rowcount == 1


def _record_path(label: str) -> str:
    return str(Path(os.getenv("TEMP", ".")) / f"lxscope-r6-{label}-{uuid4().hex}.log")


def _trace_state(label: str, run, *, events=None, executed=None) -> None:
    """Emit sanitized, reproducible Run/Node evidence when tracing is enabled."""

    target = os.getenv("LXSCOPE_TASK_R6_TRACE_PATH")
    if not target:
        return
    record = {
        "label": label,
        "run_id": run.id,
        "engine": run.engine,
        "status": run.status.value,
        "control_mode": run.control_mode,
        "run_version": run.run_version,
        "event_sequence": run.event_sequence,
        "fence_epoch": run.fence_epoch,
        "node_snapshot_fingerprint": run.node_snapshot_fingerprint,
        "nodes": [
            {
                "node_id": item.node_id,
                "status": item.status.value,
                "attempt": item.attempt,
                "attempt_history": [entry.status for entry in item.attempt_history],
                "recovery_required": item.metadata.get("recovery_required", False),
            }
            for item in run.node_runs
        ],
        "event_types": [item.type for item in events] if events is not None else [],
        "executed_node_ids": list(executed or []),
        "execution_counts": {
            node_id: list(executed or []).count(node_id)
            for node_id in sorted(set(executed or []))
        },
    }
    with Path(target).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


@pytest.mark.parametrize("backend_kind", ["postgres", "redis"])
def test_real_store_sop_controls_retry_and_process_resume(backend_kind: str) -> None:
    if backend_kind == "postgres" and not _PG_URL:
        pytest.skip("Set LXSCOPE_TASK_TEST_DATABASE_URL to an isolated PostgreSQL URL")
    if backend_kind == "redis" and not _REDIS_URL:
        pytest.skip("Set LXSCOPE_TASK_TEST_REDIS_URL to an isolated Redis URL")

    async def scenario() -> None:
        backend = await _Backend(
            backend_kind, _PG_URL if backend_kind == "postgres" else _REDIS_URL
        ).open()
        services: list[TaskService] = []
        marker_path = _record_path("controls")
        paths = [marker_path]
        spawned: list[multiprocessing.Process] = []
        try:
            executor = _RecordingExecutor(block_node="A", marker_path=marker_path)
            user_id = f"r6-owner-{uuid4().hex}"
            service = TaskService(backend.store, preview_executor=executor)
            services.append(service)
            task, run = await _create_run(
                service, user_id, [_agent("A", 0), _agent("B", 1), _agent("C", 2)]
            )
            backend.own(task, run)
            assert run.engine == "sop"
            assert run.node_snapshot_fingerprint
            worker = service._active_runs[run.id]
            await asyncio.wait_for(executor.entered.wait(), timeout=15)
            current = await backend.store.get_run(user_id, run.id)
            assert current is not None
            pause_request = TaskRunControlRequest(
                request_id="r6-pause-first",
                expected_run_version=current.run_version,
            )
            await service.pause_run(user_id, run.id, pause_request)
            duplicate_pause = await service.pause_run(user_id, run.id, pause_request)
            assert duplicate_pause is not None
            executor.release.set()
            await asyncio.wait_for(worker, timeout=20)
            paused = await backend.store.get_run(user_id, run.id)
            assert paused is not None
            assert paused.status is RunStatus.QUEUED
            assert paused.control_mode == "paused"
            assert [item.status for item in paused.node_runs] == [
                NodeRunStatus.SUCCEEDED,
                NodeRunStatus.PENDING,
                NodeRunStatus.PENDING,
            ]
            assert paused.node_runs[0].attempt == 1
            assert paused.node_runs[0].output == "R6-SYNTHETIC-A"
            events = await backend.store.list_events(user_id, run.id)
            assert any(event.type == "run.pause_requested" for event in events)
            assert any(event.type == "run.paused" for event in events)
            _trace_state("pause_checkpoint", paused, events=events, executed=executor.calls)

            await service.shutdown()
            service = TaskService(
                await backend.new_store(), preview_executor=executor
            )
            services.append(service)
            restored = await service.get_run(user_id, run.id)
            assert restored is not None and restored.control_mode == "paused"
            assert run.id not in service._active_runs
            step_request = TaskRunControlRequest(
                request_id="r6-step-B",
                expected_run_version=restored.run_version,
            )
            await service.step_run(user_id, run.id, step_request)
            step_worker = service._active_runs[run.id]
            await asyncio.wait_for(step_worker, timeout=20)
            after_step = await backend.store.get_run(user_id, run.id)
            assert after_step is not None
            assert after_step.control_mode == "paused"
            assert [item.status for item in after_step.node_runs] == [
                NodeRunStatus.SUCCEEDED,
                NodeRunStatus.SUCCEEDED,
                NodeRunStatus.PENDING,
            ]
            assert executor.calls == ["A", "B"]
            duplicate_step = await service.step_run(user_id, run.id, step_request)
            assert duplicate_step is not None
            assert duplicate_step.run_version == after_step.run_version
            _trace_state(
                "single_step_checkpoint",
                after_step,
                events=await backend.store.list_events(user_id, run.id),
                executed=executor.calls,
            )

            # Step's checkpoint survives process reconstruction; the child
            # must resume after B and execute only C.
            await service.shutdown()
            process, result_queue = await _run_child(
                backend_kind,
                backend.url,
                user_id,
                run.id,
                marker_path,
                "continue",
            )
            spawned.append(process)
            await asyncio.to_thread(process.join, 50)
            assert process.exitcode == 0
            child_result = result_queue.get(timeout=3)
            assert child_result[0] == "ok", child_result
            completed = await backend.store.get_run(user_id, run.id)
            assert completed is not None
            assert completed.id == run.id
            assert completed.status is RunStatus.SUCCEEDED
            assert [item.status for item in completed.node_runs] == [
                NodeRunStatus.SUCCEEDED,
                NodeRunStatus.SUCCEEDED,
                NodeRunStatus.SUCCEEDED,
            ]
            assert [item.attempt for item in completed.node_runs] == [1, 1, 1]
            assert Path(marker_path).read_text(encoding="utf-8").splitlines() == [
                "A",
                "B",
                "C",
            ]
            final_events = await backend.store.list_events(user_id, run.id)
            assert final_events[-1].sequence == completed.event_sequence
            assert [event.sequence for event in final_events] == list(
                range(1, len(final_events) + 1)
            )
            _trace_state(
                "continued_after_process_restart",
                completed,
                events=final_events,
                executed=Path(marker_path).read_text(encoding="utf-8").splitlines(),
            )

            # A failed SOP node retries in the same durable Run; successful
            # predecessors and their checkpoint outputs remain untouched.
            retry_executor = _RecordingExecutor(fail_once="C")
            retry_service = TaskService(
                await backend.new_store(), preview_executor=retry_executor
            )
            services.append(retry_service)
            retry_user = f"r6-owner-{uuid4().hex}"
            retry_task, failed_seed = await _create_run(
                retry_service,
                retry_user,
                [_agent("A", 0), _agent("B", 1), _agent("C", 2), _agent("D", 3)],
            )
            backend.own(retry_task, failed_seed)
            await asyncio.wait_for(
                retry_service._active_runs[failed_seed.id], timeout=25
            )
            failed = await backend.store.get_run(retry_user, failed_seed.id)
            assert failed is not None and failed.status is RunStatus.FAILED
            assert [item.status for item in failed.node_runs] == [
                NodeRunStatus.SUCCEEDED,
                NodeRunStatus.SUCCEEDED,
                NodeRunStatus.FAILED,
                NodeRunStatus.PENDING,
            ]
            _trace_state(
                "node_failure_checkpoint",
                failed,
                events=await backend.store.list_events(retry_user, failed.id),
                executed=retry_executor.calls,
            )
            retry_request = TaskRunControlRequest(
                request_id="r6-retry-C",
                expected_run_version=failed.run_version,
            )
            retried = await retry_service.retry_node(
                retry_user, failed.id, "C", retry_request
            )
            assert retried is not None and retried.id == failed_seed.id
            assert retried.node_runs[0].output == "R6-SYNTHETIC-A"
            assert retried.node_runs[1].output == "R6-SYNTHETIC-B"
            duplicate_retry = await retry_service.retry_node(
                retry_user, failed.id, "C", retry_request
            )
            assert duplicate_retry is not None
            assert duplicate_retry.run_version == retried.run_version
            await retry_service.continue_run(
                retry_user,
                failed.id,
                TaskRunControlRequest(
                    request_id="r6-continue-after-retry",
                    expected_run_version=retried.run_version,
                ),
            )
            await asyncio.wait_for(
                retry_service._active_runs[failed.id], timeout=25
            )
            retried_final = await backend.store.get_run(retry_user, failed.id)
            assert retried_final is not None
            assert retried_final.id == failed_seed.id
            assert retried_final.status is RunStatus.SUCCEEDED
            assert retry_executor.calls == ["A", "B", "C", "C", "D"]
            assert [item.status for item in retried_final.node_runs[2].attempt_history] == [
                "failed",
                "succeeded",
            ]
            retry_events = await backend.store.list_events(retry_user, failed.id)
            assert sum(event.type == "node.retry_requested" for event in retry_events) == 1
            _trace_state(
                "node_retry_completed",
                retried_final,
                events=retry_events,
                executed=retry_executor.calls,
            )

            risky_user = f"r6-owner-{uuid4().hex}"
            risky_run_id = uuid4().hex
            risky_node = TaskNode(
                id="risky-tool",
                name="risky-tool",
                order=0,
                type="tool",
                config=ToolStepConfig(
                    tool_name="synthetic.write", arguments={"value": "synthetic"}
                ),
            )
            risky_task = await retry_service.create_task(
                risky_user,
                CreateTaskRequest(
                    title="Synthetic side-effect retry guard",
                    goal="Do not replay an unverified synthetic tool side effect",
                    nodes=[risky_node],
                ),
            )
            risky_run = TaskRunRecord(
                id=risky_run_id,
                task_id=risky_task.id,
                user_id=risky_user,
                task_revision=risky_task.revision,
                engine="sop",
                status=RunStatus.FAILED,
                nodes=[risky_node],
                node_runs=[
                    NodeRunRecord(
                        node_id=risky_node.id,
                        name=risky_node.name,
                        prompt=risky_node.prompt,
                        type=risky_node.type,
                        order=0,
                        status=NodeRunStatus.FAILED,
                        attempt=1,
                        error="synthetic uncertain side effect",
                        metadata={"recovery_required": True},
                    )
                ],
                sop_state=TaskSOPRunState(
                    id=risky_run_id,
                    steps=[TaskStepState()],
                    task_node_ids=[risky_node.id],
                ).model_dump(mode="json"),
            )
            await backend.store.save_run(risky_run)
            backend.own(risky_task, risky_run)
            risky_service = TaskService(
                await backend.new_store(), preview_executor=_RecordingExecutor()
            )
            services.append(risky_service)
            with pytest.raises(TaskWorkflowConflictError, match="side effects"):
                await risky_service.retry_node(
                    risky_user,
                    risky_run.id,
                    risky_node.id,
                    TaskRunControlRequest(
                        request_id="r6-risky-retry-unconfirmed",
                        expected_run_version=risky_run.run_version,
                    ),
                )
            confirmed = await risky_service.retry_node(
                risky_user,
                risky_run.id,
                risky_node.id,
                TaskRunControlRequest(
                    request_id="r6-risky-retry-confirmed",
                    expected_run_version=risky_run.run_version,
                    side_effects_verified=True,
                ),
            )
            assert confirmed is not None
            assert confirmed.status is RunStatus.QUEUED
            assert confirmed.node_runs[0].status is NodeRunStatus.PENDING
            assert confirmed.node_runs[0].metadata["side_effects_verified"][
                "confirmed_by"
            ] == risky_user

            # An InputStep remains in the same persisted Run across a real
            # worker-process restart; the answer is accepted before delivery.
            input_marker = _record_path("input-resume")
            paths.append(input_marker)
            input_executor = _RecordingExecutor(marker_path=input_marker)
            input_service = TaskService(
                await backend.new_store(), preview_executor=input_executor
            )
            services.append(input_service)
            input_user = f"r6-owner-{uuid4().hex}"
            input_task, input_seed = await _create_run(
                input_service,
                input_user,
                [
                    TaskNode(
                        id="ask",
                        name="ask",
                        order=0,
                        type="input",
                        config=InputStepConfig(
                            questions=[
                                TaskInputQuestion(
                                    key="destination", label="Synthetic destination?"
                                )
                            ]
                        ),
                    ),
                    _agent("deliver", 1),
                ],
            )
            backend.own(input_task, input_seed)
            await asyncio.wait_for(
                input_service._active_runs[input_seed.id], timeout=20
            )
            waiting = await backend.store.get_run(input_user, input_seed.id)
            assert waiting is not None
            assert waiting.status is RunStatus.AWAITING_INPUT
            assert waiting.pending_input is not None
            assert waiting.node_runs[0].status is NodeRunStatus.AWAITING_INPUT
            assert input_executor.calls == []
            _trace_state(
                "input_waiting_before_restart",
                waiting,
                events=await backend.store.list_events(input_user, input_seed.id),
                executed=input_executor.calls,
            )
            await input_service.shutdown()
            input_process, input_results = await _run_child(
                backend_kind,
                backend.url,
                input_user,
                input_seed.id,
                input_marker,
                "answer",
            )
            spawned.append(input_process)
            await asyncio.to_thread(input_process.join, 50)
            assert input_process.exitcode == 0
            input_result = input_results.get(timeout=3)
            assert input_result[0] == "ok", input_result
            answered = await backend.store.get_run(input_user, input_seed.id)
            assert answered is not None and answered.id == input_seed.id
            assert answered.status is RunStatus.SUCCEEDED
            assert answered.node_runs[0].attempt == 1
            input_checkpoint = answered.sop_state["steps"][0]
            assert input_checkpoint["answers"][0]["answers"]["destination"] == (
                "Synthetic City"
            )
            assert Path(input_marker).read_text(encoding="utf-8").splitlines() == [
                "deliver"
            ]
            _trace_state(
                "input_answered_after_restart",
                answered,
                events=await backend.store.list_events(input_user, input_seed.id),
                executed=Path(input_marker).read_text(encoding="utf-8").splitlines(),
            )

            # A request with an old Run version is rejected, and Stop remains
            # terminal after the active SOP node receives the request.
            with pytest.raises(TaskStoreConflictError):
                await retry_service.pause_run(
                    retry_user,
                    failed.id,
                    TaskRunControlRequest(
                        request_id="r6-stale-version",
                        expected_run_version=failed_seed.run_version,
                    ),
                )
            stop_executor = _RecordingExecutor(block_node="X")
            stop_service = TaskService(
                await backend.new_store(), preview_executor=stop_executor
            )
            services.append(stop_service)
            stop_user = f"r6-owner-{uuid4().hex}"
            stop_task, stop_seed = await _create_run(
                stop_service, stop_user, [_agent("X", 0), _agent("Y", 1)]
            )
            backend.own(stop_task, stop_seed)
            stop_worker = stop_service._active_runs[stop_seed.id]
            await asyncio.wait_for(stop_executor.entered.wait(), timeout=15)
            current = await backend.store.get_run(stop_user, stop_seed.id)
            assert current is not None
            stopped = await stop_service.stop_run(
                stop_user,
                stop_seed.id,
                TaskRunControlRequest(
                    request_id="r6-stop-active",
                    expected_run_version=current.run_version,
                ),
            )
            assert stopped is not None and stopped.status is RunStatus.CANCELED
            stop_executor.release.set()
            await asyncio.wait_for(stop_worker, timeout=15)
            stopped = await backend.store.get_run(stop_user, stop_seed.id)
            assert stopped is not None
            assert stopped.status is RunStatus.CANCELED
            assert stopped.control_mode == "stopped"
            assert [item.status for item in stopped.node_runs] == [
                NodeRunStatus.CANCELED,
                NodeRunStatus.CANCELED,
            ]
            _trace_state(
                "stop_terminal_checkpoint",
                stopped,
                events=await backend.store.list_events(stop_user, stop_seed.id),
                executed=stop_executor.calls,
            )
            with pytest.raises(TaskWorkflowConflictError):
                await stop_service.continue_run(
                    stop_user,
                    stop_seed.id,
                    TaskRunControlRequest(
                        request_id="r6-must-not-continue-stop",
                        expected_run_version=stopped.run_version,
                    ),
                )
        finally:
            for process in spawned:
                if process.is_alive():
                    process.terminate()
                    await asyncio.to_thread(process.join, 5)
            for service in services:
                await service.shutdown()
            for path in paths:
                if Path(path).exists():
                    Path(path).unlink()
            await backend.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("backend_kind", ["postgres", "redis"])
def test_real_store_sop_subprocess_crash_fencing_and_worker_competition(
    backend_kind: str,
) -> None:
    if backend_kind == "postgres" and not _PG_URL:
        pytest.skip("Set LXSCOPE_TASK_TEST_DATABASE_URL to an isolated PostgreSQL URL")
    if backend_kind == "redis" and not _REDIS_URL:
        pytest.skip("Set LXSCOPE_TASK_TEST_REDIS_URL to an isolated Redis URL")

    async def scenario() -> None:
        backend = await _Backend(
            backend_kind, _PG_URL if backend_kind == "postgres" else _REDIS_URL
        ).open()
        services: list[TaskService] = []
        paths: list[str] = []
        processes: list[multiprocessing.Process] = []
        try:
            user_id = f"r6-owner-{uuid4().hex}"
            seed_service = _UnscheduledTaskService(
                backend.store, preview_executor=_RecordingExecutor()
            )
            services.append(seed_service)
            task, run = await _create_run(
                seed_service,
                user_id,
                [_agent("A", 0), _agent("B", 1), _agent("C", 2)],
            )
            backend.own(task, run)
            assert run.status is RunStatus.QUEUED
            crash_path = _record_path("external-effect")
            paths.append(crash_path)
            crashed, crash_results = await _run_child(
                backend_kind,
                backend.url,
                user_id,
                run.id,
                crash_path,
                "recover",
                crash_node="C",
            )
            processes.append(crashed)
            await asyncio.to_thread(crashed.join, 50)
            assert not crashed.is_alive()
            assert crashed.exitcode == 86
            running = await backend.store.get_run(user_id, run.id)
            assert running is not None and running.status is RunStatus.RUNNING
            assert [item.status for item in running.node_runs] == [
                NodeRunStatus.SUCCEEDED,
                NodeRunStatus.SUCCEEDED,
                NodeRunStatus.RUNNING,
            ]
            _trace_state(
                "worker_crash_after_external_effect",
                running,
                events=await backend.store.list_events(user_id, run.id),
                executed=Path(crash_path).read_text(encoding="utf-8").splitlines(),
            )
            assert [item.attempt for item in running.node_runs] == [1, 1, 1]
            assert Path(crash_path).read_text(encoding="utf-8").splitlines() == [
                "A",
                "B",
                "C",
            ]
            await _expire_own_lease(backend, run)
            recovered_process, recovery_results = await _run_child(
                backend_kind,
                backend.url,
                user_id,
                run.id,
                crash_path,
                "recover",
            )
            processes.append(recovered_process)
            await asyncio.to_thread(recovered_process.join, 50)
            assert recovered_process.exitcode == 0
            recovery_result = recovery_results.get(timeout=3)
            assert recovery_result[0] == "ok", recovery_result
            recovered = await backend.store.get_run(user_id, run.id)
            assert recovered is not None and recovered.status is RunStatus.FAILED
            assert recovered.node_runs[0].status is NodeRunStatus.SUCCEEDED
            assert recovered.node_runs[1].status is NodeRunStatus.SUCCEEDED
            assert recovered.node_runs[2].status is NodeRunStatus.FAILED
            assert recovered.node_runs[2].metadata["recovery_required"] is True
            assert (
                recovered.node_runs[2].metadata["recovery_policy"]
                == "manual_verification"
            )
            assert Path(crash_path).read_text(encoding="utf-8").splitlines() == [
                "A",
                "B",
                "C",
            ]
            recovery_events = await backend.store.list_events(user_id, run.id)
            assert any(
                event.type == "step.recovery_required" for event in recovery_events
            )
            assert recovery_events[-1].sequence == recovered.event_sequence
            _trace_state(
                "manual_verification_after_recovery",
                recovered,
                events=recovery_events,
                executed=Path(crash_path).read_text(encoding="utf-8").splitlines(),
            )

            # Two independent processes observe the same queued SOP Run while
            # the winner is held inside its node. Only one claim may dispatch.
            competition_user = f"r6-owner-{uuid4().hex}"
            competition_seed = _UnscheduledTaskService(
                await backend.new_store(), preview_executor=_RecordingExecutor()
            )
            services.append(competition_seed)
            competition_task, competition_run = await _create_run(
                competition_seed,
                competition_user,
                [_agent("Z", 0)],
            )
            backend.own(competition_task, competition_run)
            marker = _record_path("claim-competition")
            entered_path = _record_path("claim-entered")
            release_path = _record_path("claim-release")
            paths.extend([marker, entered_path, release_path])
            process_context = multiprocessing.get_context("spawn")
            gate = process_context.Event()
            queue_a = process_context.Queue()
            queue_b = process_context.Queue()
            child_processes = []
            for queue in (queue_a, queue_b):
                process = process_context.Process(
                    target=_worker_process,
                    args=(
                        backend_kind,
                        backend.url,
                        competition_user,
                        competition_run.id,
                        marker,
                        "recover",
                        queue,
                    ),
                    kwargs={
                        "gate": gate,
                        "block_node": "Z",
                        "entered_path": entered_path,
                        "release_path": release_path,
                    },
                )
                child_processes.append(process)
                processes.append(process)
                process.start()
            gate.set()
            deadline = time.monotonic() + 40
            while not Path(entered_path).exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert Path(entered_path).exists(), "No worker entered the SOP node"
            # The losing worker returns from claim_run while the winner remains
            # blocked. Its durable Run is still RUNNING.
            loser_result = None
            loser_queue = None
            for queue in (queue_a, queue_b):
                try:
                    loser_result = queue.get(timeout=3)
                    loser_queue = queue
                    break
                except Exception:
                    continue
            assert loser_result is not None and loser_result[0] == "ok", loser_result
            Path(release_path).write_text("release", encoding="utf-8")
            for process in child_processes:
                await asyncio.to_thread(process.join, 50)
                assert process.exitcode == 0
            # Read the non-loser's result after releasing the executing worker.
            other_queue = queue_b if loser_queue is queue_a else queue_a
            winner_result = other_queue.get(timeout=3)
            assert winner_result[0] == "ok", winner_result
            competition_final = await backend.store.get_run(
                competition_user, competition_run.id
            )
            assert competition_final is not None
            assert competition_final.status is RunStatus.SUCCEEDED
            assert Path(marker).read_text(encoding="utf-8").splitlines() == ["Z"]
            competition_events = await backend.store.list_events(
                competition_user, competition_run.id
            )
            assert sum(
                event.type == "run.claimed" for event in competition_events
            ) == 1
            assert competition_final.node_runs[0].attempt == 1
            _trace_state(
                "two_worker_claim_competition",
                competition_final,
                events=competition_events,
                executed=Path(marker).read_text(encoding="utf-8").splitlines(),
            )

            # A persisted node-snapshot mismatch must fail closed before any
            # executor call; this is an intentional test-only invalid checkpoint.
            mismatch_user = f"r6-owner-{uuid4().hex}"
            mismatch_executor = _RecordingExecutor()
            mismatch_service = _UnscheduledTaskService(
                await backend.new_store(), preview_executor=mismatch_executor
            )
            services.append(mismatch_service)
            mismatch_task, mismatch_run = await _create_run(
                mismatch_service, mismatch_user, [_agent("M", 0)]
            )
            backend.own(mismatch_task, mismatch_run)
            await backend.store.transition_run(
                mismatch_user,
                mismatch_run.id,
                expected_version=mismatch_run.run_version,
                run_patch={"node_snapshot_fingerprint": "f" * 64},
            )
            recovery_service = TaskService(
                await backend.new_store(), preview_executor=mismatch_executor
            )
            services.append(recovery_service)
            await recovery_service.get_run(mismatch_user, mismatch_run.id)
            active = recovery_service._active_runs.get(mismatch_run.id)
            if active is not None:
                await asyncio.wait_for(active, timeout=20)
            mismatch_final = await backend.store.get_run(
                mismatch_user, mismatch_run.id
            )
            assert mismatch_final is not None
            assert mismatch_final.status is RunStatus.FAILED
            assert mismatch_executor.calls == []
            assert mismatch_final.node_runs[0].attempt == 0
            _trace_state(
                "snapshot_mismatch_fail_closed",
                mismatch_final,
                events=await backend.store.list_events(mismatch_user, mismatch_run.id),
                executed=mismatch_executor.calls,
            )

            # 100 concurrent CAS writers against an actual SOP Run all
            # eventually commit once, with event sequence and snapshot version
            # remaining aligned. Each state change is an atomic Run+event
            # operation in both backing stores.
            cas_user = f"r6-owner-{uuid4().hex}"
            cas_service = _UnscheduledTaskService(
                await backend.new_store(), preview_executor=_RecordingExecutor()
            )
            services.append(cas_service)
            cas_task, cas_run = await _create_run(
                cas_service, cas_user, [_agent("CAS", 0)]
            )
            backend.own(cas_task, cas_run)
            events_before_atomic_probe = await backend.store.list_events(
                cas_user, cas_run.id
            )

            if backend_kind == "postgres":
                def fail_event_insert(
                    connection, cursor, statement, parameters, context, executemany
                ):
                    del connection, cursor, parameters, context, executemany
                    if (
                        statement.lstrip().upper().startswith("INSERT")
                        and "longxin_app.task_events" in statement
                    ):
                        raise RuntimeError("injected SOP event insert failure")

                event.listen(
                    backend.database.engine.sync_engine,
                    "before_cursor_execute",
                    fail_event_insert,
                )
                try:
                    with pytest.raises(RuntimeError, match="SOP event insert"):
                        await backend.store.transition_run(
                            cas_user,
                            cas_run.id,
                            expected_version=cas_run.run_version,
                            run_patch={"control_mode": "paused"},
                            audit_events=[
                                TaskEventDraft(type="r6.sop.atomicity.probe")
                            ],
                        )
                finally:
                    event.remove(
                        backend.database.engine.sync_engine,
                        "before_cursor_execute",
                        fail_event_insert,
                    )
                unchanged = await backend.store.get_run(cas_user, cas_run.id)
                unchanged_events = await backend.store.list_events(
                    cas_user, cas_run.id
                )
                assert unchanged is not None
                assert unchanged.run_version == cas_run.run_version
                assert unchanged.control_mode == cas_run.control_mode
                assert unchanged_events == events_before_atomic_probe
                assert [item.type for item in unchanged_events] == ["run.created"]
                _trace_state("postgres_sop_atomic_rollback", unchanged, events=unchanged_events)

            ready = 0
            start = asyncio.Event()

            async def cas_update(index: int) -> None:
                nonlocal ready
                ready += 1
                if ready == 100:
                    start.set()
                await start.wait()
                for _ in range(500):
                    current = await backend.store.get_run(cas_user, cas_run.id)
                    assert current is not None
                    metadata = dict(current.node_runs[0].metadata)
                    metadata[f"writer-{index}"] = index
                    try:
                        await backend.store.transition_run(
                            cas_user,
                            cas_run.id,
                            expected_version=current.run_version,
                            node_patches={"CAS": {"metadata": metadata}},
                            audit_events=[
                                TaskEventDraft(
                                    type="r6.sop.cas.updated",
                                    payload={"writer": index},
                                )
                            ],
                        )
                        return
                    except TaskStoreConflictError:
                        continue
                raise AssertionError("SOP CAS did not converge within 500 attempts")

            await asyncio.gather(*(cas_update(index) for index in range(100)))
            cas_final = await backend.store.get_run(cas_user, cas_run.id)
            cas_events = await backend.store.list_events(cas_user, cas_run.id)
            assert cas_final is not None
            assert len(cas_final.node_runs[0].metadata) == 100
            assert cas_final.run_version == cas_run.run_version + 100
            assert cas_final.event_sequence == len(cas_events)
            assert [item.sequence for item in cas_events] == list(
                range(1, len(cas_events) + 1)
            )
            assert sum(
                item.type == "r6.sop.cas.updated" for item in cas_events
            ) == 100
            _trace_state("sop_100_cas_updates", cas_final, events=cas_events)

            # Same-owner lease renewal preserves its epoch. Expiry admits one
            # newer epoch, and the stale SOP owner cannot write afterward.
            fence_user = f"r6-owner-{uuid4().hex}"
            fence_seed = _UnscheduledTaskService(
                await backend.new_store(), preview_executor=_RecordingExecutor()
            )
            services.append(fence_seed)
            fence_task, fence_run = await _create_run(
                fence_seed, fence_user, [_agent("FENCE", 0)]
            )
            backend.own(fence_task, fence_run)
            owner_a = await backend.store.claim_run(
                fence_user,
                fence_run.id,
                owner_token="r6-sop-owner-a",
                lease_seconds=0.15,
            )
            await asyncio.sleep(0.02)
            renewed = await backend.store.claim_run(
                fence_user,
                fence_run.id,
                owner_token="r6-sop-owner-a",
                lease_seconds=0.15,
            )
            assert renewed.fence_epoch == owner_a.fence_epoch
            await asyncio.sleep(0.17)
            owner_b = await backend.store.claim_run(
                fence_user,
                fence_run.id,
                owner_token="r6-sop-owner-b",
                lease_seconds=1,
            )
            assert owner_b.fence_epoch == owner_a.fence_epoch + 1
            with pytest.raises(TaskStoreConflictError, match="stale"):
                await backend.store.transition_run(
                    fence_user,
                    fence_run.id,
                    expected_version=owner_b.run_version,
                    run_patch={"control_mode": "paused"},
                    owner_token="r6-sop-owner-a",
                    expected_fence_epoch=owner_a.fence_epoch,
                )
            fenced_final = await backend.store.get_run(fence_user, fence_run.id)
            assert fenced_final is not None
            assert fenced_final.owner_token == "r6-sop-owner-b"
            assert fenced_final.fence_epoch == owner_b.fence_epoch
            assert fenced_final.control_mode == "continuous"
            _trace_state(
                "stale_owner_fenced",
                fenced_final,
                events=await backend.store.list_events(fence_user, fence_run.id),
            )

            # A Task template can be revised while an existing Run remains
            # deterministic because its node snapshot and template version are
            # frozen at Run creation.
            template_user = f"r6-owner-{uuid4().hex}"
            template_service = _UnscheduledTaskService(
                await backend.new_store(), preview_executor=_RecordingExecutor()
            )
            services.append(template_service)
            template_task, template_run_seed = await _create_run(
                template_service,
                template_user,
                [_agent("FROZEN", 0)],
            )
            template_run = await backend.store.transition_run(
                template_user,
                template_run_seed.id,
                expected_version=template_run_seed.run_version,
                run_patch={
                    "source_template_id": "r6_synthetic_template",
                    "source_template_version": "1.0.0",
                },
            )
            backend.own(template_task, template_run)
            assert template_run.source_template_version == "1.0.0"
            frozen_fingerprint = template_run.node_snapshot_fingerprint
            latest_task = await backend.store.get_task(
                template_user, template_task.id
            )
            assert latest_task is not None
            revised_task = latest_task.model_copy(
                update={
                    "revision": latest_task.revision + 1,
                    "workflow_mode": TaskWorkflowMode.TEMPLATE_INSTANCE,
                    "source_template_id": "r6_synthetic_template",
                    "source_template_version": "2.0.0",
                    "nodes": [_agent("EDITED", 0)],
                }
            )
            await backend.store.save_task(revised_task)
            version_marker = _record_path("template-version")
            paths.append(version_marker)
            version_process, version_results = await _run_child(
                backend_kind,
                backend.url,
                template_user,
                template_run.id,
                version_marker,
                "recover",
            )
            processes.append(version_process)
            await asyncio.to_thread(version_process.join, 50)
            assert version_process.exitcode == 0
            version_result = version_results.get(timeout=3)
            assert version_result[0] == "ok", version_result
            version_final = await backend.store.get_run(
                template_user, template_run.id
            )
            assert version_final is not None
            assert version_final.status is RunStatus.SUCCEEDED
            assert version_final.source_template_version == "1.0.0"
            assert version_final.node_snapshot_fingerprint == frozen_fingerprint
            assert [node.id for node in version_final.nodes] == [
                node.id for node in template_run.nodes
            ]
            assert Path(version_marker).read_text(encoding="utf-8").splitlines() == [
                "FROZEN"
            ]
            _trace_state(
                "template_revision_keeps_frozen_run_snapshot",
                version_final,
                events=await backend.store.list_events(template_user, template_run.id),
                executed=Path(version_marker).read_text(encoding="utf-8").splitlines(),
            )
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    await asyncio.to_thread(process.join, 5)
            for service in services:
                await service.shutdown()
            for path in paths:
                if Path(path).exists():
                    Path(path).unlink()
            await backend.close()

    asyncio.run(scenario())
