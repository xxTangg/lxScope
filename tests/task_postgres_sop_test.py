# -*- coding: utf-8 -*-
"""Real PostgreSQL coverage for Task's SOP checkpoints and schema upgrade.

Run against a disposable database named lxscope_task_test*, using the explicit
LXSCOPE_TASK_TEST_DATABASE_URL variable. Never fall back to application data.
"""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, skipUnless
from uuid import UUID, uuid4

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
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
    TaskNode,
    TaskEventDraft,
    TaskGraph,
    TaskGraphEdge,
    TaskRunRecord,
    TaskRunControlRequest,
    TaskRunRequest,
)
from examples.agent_service.task._postgres_store import PostgresTaskStore
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStoreConflictError

_TEST_URL = os.getenv("LXSCOPE_TASK_TEST_DATABASE_URL", "")
_MIGRATIONS = (
    Path(__file__).resolve().parents[1] / "examples/agent_service/migrations"
)


def _agent(name: str) -> TaskNode:
    return TaskNode(name=name, config=AgentStepConfig(prompt=name))


class _Executor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_node(self, *, node, previous_output, context):
        self.calls.append(node.name)
        if node.name == "collect" and not context.user_answers:
            return StepExecutionResult(
                text="",
                status="needs_input",
                reason="Destination required",
                questions=[
                    TaskInputQuestion(key="city", label="Destination?")
                ],
            )
        return StepExecutionResult(
            text=f"{node.name}: {previous_output} "
            + json.dumps(
                [answer.answers for answer in context.user_answers],
                ensure_ascii=False,
            ),
        )

    async def verify_node(self, *, node, output, context):
        del node, output, context
        return StepVerdict(passed=True, message="Deterministic test verifier passed.")


@skipUnless(_TEST_URL, "Set a disposable PostgreSQL Task test URL")
class TaskPostgresSOPTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        url = make_url(_TEST_URL)
        if url.drivername != "postgresql+asyncpg" or not (
            url.database or ""
        ).startswith("lxscope_task_test"):
            raise ValueError(
                "Task tests require a disposable lxscope_task_test database"
            )
        # Each test owns a fresh database; parallel tests never share a schema.
        self.database_name = f"lxscope_task_test_{uuid4().hex}"
        self.admin_engine = create_async_engine(
            url, isolation_level="AUTOCOMMIT"
        )
        async with self.admin_engine.connect() as connection:
            await connection.exec_driver_sql(
                f'CREATE DATABASE "{self.database_name}"'
            )
        self.url = url.set(database=self.database_name).render_as_string(
            hide_password=False
        )
        self.databases: list[ApplicationDatabase] = []
        self.services: list[TaskService] = []

    async def asyncTearDown(self) -> None:
        for service in self.services:
            await service.shutdown()
        for database in self.databases:
            await database.close()
        async with self.admin_engine.connect() as connection:
            await connection.exec_driver_sql(
                f'DROP DATABASE "{self.database_name}"'
            )
        await self.admin_engine.dispose()

    async def _database(self, migration_dir=None):
        database = ApplicationDatabase(self.url, migration_dir=migration_dir)
        self.databases.append(database)
        await database.initialize()
        return database

    async def _store(self, database):
        store = PostgresTaskStore(database.engine)
        await store.initialize()
        return store

    async def test_existing_database_upgrade_preserves_data_and_constraints(
        self,
    ):
        # Exercise the actual numbered migration runner starting with 0002's
        # old CHECK constraints, rather than creating a permissive test table.
        with tempfile.TemporaryDirectory() as directory:
            for migration in _MIGRATIONS.glob("*.sql"):
                if migration.name >= "0004_task_awaiting_input.sql":
                    continue
                shutil.copyfile(migration, Path(directory) / migration.name)
            database = await self._database(Path(directory))
            store = await self._store(database)
            service = TaskService(store, preview_executor=_Executor())
            self.services.append(service)
            task = await service.create_task(
                "owner",
                CreateTaskRequest(goal="Keep history", nodes=[_agent("done")]),
            )
            run = await service.create_run(
                "owner", task.id, TaskRunRequest(input="original")
            )
            await service._active_runs[run.id]
            original = await store.get_run("owner", run.id)
            self.assertEqual(original.status, RunStatus.SUCCEEDED)
            with self.assertRaises(TaskStoreConflictError) as failure:
                await store.update_run(
                    "owner", run.id, status=RunStatus.AWAITING_INPUT
                )
            self.assertIn("transition_run", str(failure.exception))

        database.migration_dir = _MIGRATIONS
        await database.initialize()
        await database.initialize()  # Repeat startup must not repeat migrations.
        retained = await store.get_run("owner", run.id)
        self.assertEqual(retained.model_dump(), original.model_dump())
        async with database.engine.connect() as connection:
            versions = (
                (
                    await connection.execute(
                        text(
                            "SELECT version FROM longxin_app.schema_migrations"
                        )
                    )
                )
                .scalars()
                .all()
            )
        self.assertEqual(versions.count("0004_task_awaiting_input.sql"), 1)
        for status in RunStatus:
            async with database.engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE longxin_app.task_runs SET status=:status WHERE id=:run_id"
                    ),
                    {"status": status.value, "run_id": UUID(run.id)},
                )

        node_id = uuid4()
        async with database.engine.begin() as connection:
            await connection.execute(
                text("""INSERT INTO longxin_app.task_run_nodes
                    (id, tenant_id, run_id, node_key, node_type, status)
                    SELECT :id, tenant_id, id, 'test-node', 'agent', 'pending'
                    FROM longxin_app.task_runs WHERE id = :run_id"""),
                {"id": node_id, "run_id": UUID(run.id)},
            )
        for status in NodeRunStatus:
            async with database.engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE longxin_app.task_run_nodes SET status=:status WHERE id=:id"
                    ),
                    {"status": status.value, "id": node_id},
                )
        for table in ("task_runs", "task_run_nodes"):
            with self.assertRaises(IntegrityError):
                async with database.engine.begin() as connection:
                    await connection.execute(
                        text(
                            f"UPDATE longxin_app.{table} SET status='invalid-status'"
                        )
                    )

    async def test_wait_answer_restart_resume_on_real_postgres(self):
        database = await self._database()
        store = await self._store(database)
        for explicit in (False, True):
            with self.subTest(explicit_input=explicit):
                executor = _Executor()
                collect = (
                    TaskNode(
                        name="collect",
                        type="input",
                        config=InputStepConfig(
                            questions=[
                                TaskInputQuestion(
                                    key="city", label="Destination?"
                                )
                            ]
                        ),
                    )
                    if explicit
                    else _agent("collect")
                )
                service = TaskService(store, preview_executor=executor)
                self.services.append(service)
                task = await service.create_task(
                    "owner",
                    CreateTaskRequest(
                        goal="Travel",
                        nodes=[_agent("prefix"), collect, _agent("deliver")],
                    ),
                )
                run = await service.create_run(
                    "owner", task.id, TaskRunRequest(input="Plan travel")
                )
                await service._active_runs[run.id]
                waiting = await store.get_run("owner", run.id)
                self.assertEqual(waiting.status, RunStatus.AWAITING_INPUT)
                self.assertEqual(
                    waiting.node_runs[1].status, NodeRunStatus.AWAITING_INPUT
                )
                self.assertEqual(
                    waiting.sop_state["steps"][1]["phase"], "awaiting"
                )
                self.assertIsNotNone(waiting.pending_input)
                self.assertNotIn("deliver", executor.calls)
                self.assertIsNone(await store.get_run("another-user", run.id))
                async with database.engine.connect() as connection:
                    stored_status = await connection.scalar(
                        text(
                            "SELECT status FROM longxin_app.task_runs WHERE id=:id"
                        ),
                        {"id": UUID(run.id)},
                    )
                self.assertEqual(stored_status, "awaiting_input")
                await service.shutdown()
                resumed = TaskService(
                    await self._store(database), preview_executor=executor
                )
                self.services.append(resumed)
                reply = TaskInputReply(
                    request_id=waiting.pending_input.id,
                    answers={"city": "北京"},
                )
                replies = await asyncio.gather(
                    *(
                        resumed.answer_run("owner", run.id, reply)
                        for _ in range(3)
                    )
                )
                self.assertTrue(all(item.id == run.id for item in replies))
                active = resumed._active_runs.get(run.id)
                if active:
                    await active
                finished = await store.get_run("owner", run.id)
                self.assertEqual(finished.status, RunStatus.SUCCEEDED)
                self.assertIsNone(finished.pending_input)
                self.assertEqual(executor.calls.count("prefix"), 1)
                self.assertEqual(executor.calls.count("deliver"), 1)
                self.assertIn("北京", finished.final_output)
                self.assertEqual(
                    (await store.get_task("owner", task.id)).last_run_status,
                    RunStatus.SUCCEEDED,
                )
                events = await store.list_events("owner", run.id)
                self.assertEqual(
                    sum(event.type == "input.answered" for event in events), 1
                )
                self.assertEqual(
                    len({event.sequence for event in events}), len(events)
                )

    async def test_dag_cas_parallel_updates_claim_fencing_and_restart(self):
        database = await self._database()
        store = await self._store(database)
        dag_service = TaskService(store)
        self.services.append(dag_service)
        task = await dag_service.create_task(
            "dag-owner",
            CreateTaskRequest(
                goal="Synthetic atomic DAG store test",
                nodes=[_agent("A"), _agent("B")],
            ),
        )
        run = TaskRunRecord(
            task_id=task.id,
            user_id="dag-owner",
            task_revision=task.revision,
            nodes=task.nodes,
            node_runs=[
                NodeRunRecord(node_id=node.id, name=node.name, prompt=node.prompt, order=i)
                for i, node in enumerate(task.nodes)
            ],
            status=RunStatus.RUNNING,
            engine="dag",
            graph=TaskGraph(
                max_concurrency=2,
                edges=[
                    TaskGraphEdge(
                        source_node_id=task.nodes[0].id,
                        target_node_id=task.nodes[1].id,
                    )
                ],
            ),
        )
        await store.save_run(run)

        # Submit 100 concurrent callers against the same captured CAS version.
        # Exactly one can commit that version; all others must be rejected.
        initial = await store.get_run("dag-owner", run.id)
        self.assertIsNotNone(initial)
        ready = 0
        start = asyncio.Event()

        async def contend(index: int):
            nonlocal ready
            ready += 1
            if ready == 100:
                start.set()
            await start.wait()
            metadata = dict(initial.node_runs[0].metadata)
            metadata[f"update-{index}"] = index
            try:
                return await store.transition_run(
                    "dag-owner",
                    run.id,
                    expected_version=initial.run_version,
                    node_patches={
                        task.nodes[0].id: {"metadata": metadata}
                    },
                    attempt_events=[
                        TaskEventDraft(
                            type="node.updated",
                            node_id=task.nodes[0].id,
                            payload={"index": index},
                        )
                    ],
                )
            except TaskStoreConflictError:
                return None

        results = await asyncio.gather(
            *(contend(index) for index in range(100)),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise failures[0]
        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(sum(result is None for result in results), 99)
        current = winners[0]

        # Complete 100 durable state transitions in total using the version
        # returned by each commit. The simultaneous wave above proves conflict
        # behavior without spending the test on repeated stale retries.
        winner_index = next(
            index for index, result in enumerate(results) if result is not None
        )
        for index in range(100):
            if index == winner_index:
                continue
            metadata = dict(current.node_runs[0].metadata)
            metadata[f"update-{index}"] = index
            current = await store.transition_run(
                "dag-owner",
                run.id,
                expected_version=current.run_version,
                node_patches={task.nodes[0].id: {"metadata": metadata}},
                attempt_events=[
                    TaskEventDraft(
                        type="node.updated",
                        node_id=task.nodes[0].id,
                        payload={"index": index},
                    )
                ],
            )
        self.assertEqual(len(current.node_runs[0].metadata), 100)
        self.assertEqual(current.run_version, 100)
        self.assertEqual(current.event_sequence, 101)
        events = await store.list_events("dag-owner", run.id)
        self.assertEqual(
            [event.sequence for event in events], list(range(1, 102))
        )

        # Force the event INSERT to fail after the snapshot UPDATE has run.
        # The surrounding PostgreSQL transaction must roll both writes back.
        def fail_event_insert(connection, cursor, statement, parameters, context, executemany):
            del connection, cursor, parameters, context, executemany
            if (
                statement.lstrip().upper().startswith("INSERT")
                and "longxin_app.task_events" in statement
            ):
                raise RuntimeError("injected task event write failure")

        event.listen(database.engine.sync_engine, "before_cursor_execute", fail_event_insert)
        try:
            with self.assertRaisesRegex(RuntimeError, "injected task event"):
                await store.transition_run(
                    "dag-owner",
                    run.id,
                    expected_version=current.run_version,
                    run_patch={"control_mode": "paused"},
                    audit_events=[TaskEventDraft(type="must.rollback")],
                )
        finally:
            event.remove(
                database.engine.sync_engine,
                "before_cursor_execute",
                fail_event_insert,
            )
        after_rollback = await store.get_run("dag-owner", run.id)
        self.assertEqual(after_rollback.run_version, current.run_version)
        self.assertEqual(after_rollback.control_mode, "continuous")
        self.assertEqual(await store.list_events("dag-owner", run.id), events)

        current = await store.get_run("dag-owner", run.id)
        commanded = await store.transition_run(
            "dag-owner",
            run.id,
            expected_version=current.run_version,
            run_patch={"control_mode": "continuous"},
            audit_events=[
                TaskEventDraft(
                    type="p2h.command.accepted",
                    payload={"request_id": "p2h-idempotent-command"},
                )
            ],
            command_id="p2h-command:p2h-idempotent-command",
        )
        duplicate = await store.transition_run(
            "dag-owner",
            run.id,
            expected_version=current.run_version,
            run_patch={"status": RunStatus.FAILED},
            audit_events=[TaskEventDraft(type="duplicate.must.not.persist")],
            command_id="p2h-command:p2h-idempotent-command",
        )
        self.assertEqual(duplicate, commanded)
        events_after_command = await store.list_events("dag-owner", run.id)
        self.assertEqual(len(events_after_command), 102)
        self.assertEqual(commanded.event_sequence, len(events_after_command))

        second_store = await self._store(database)

        async def compete(worker_store, token):
            try:
                return await worker_store.claim_run(
                    "dag-owner", run.id, owner_token=token
                )
            except TaskStoreConflictError:
                return None

        claims = await asyncio.gather(
            compete(store, "worker-a"), compete(second_store, "worker-b")
        )
        winners = [claim for claim in claims if claim is not None]
        self.assertEqual(len(winners), 1)
        claimed = winners[0]
        winning_token = claimed.owner_token
        losing_token = "worker-b" if winning_token == "worker-a" else "worker-a"
        self.assertEqual(claimed.fence_epoch, 1)
        renewed = await store.claim_run(
            "dag-owner", run.id, owner_token=winning_token, lease_seconds=30
        )
        self.assertEqual(renewed.fence_epoch, claimed.fence_epoch)
        with self.assertRaises(TaskStoreConflictError):
            await store.transition_run(
                "dag-owner",
                run.id,
                expected_version=renewed.run_version,
                run_patch={"control_mode": "paused"},
                owner_token=losing_token,
                expected_fence_epoch=claimed.fence_epoch,
            )

        terminal = await store.transition_run(
            "dag-owner",
            run.id,
            expected_version=renewed.run_version,
            run_patch={"status": RunStatus.SUCCEEDED},
            audit_events=[TaskEventDraft(type="run.succeeded")],
            owner_token=winning_token,
            expected_fence_epoch=claimed.fence_epoch,
            release_owner=True,
        )
        with self.assertRaisesRegex(TaskStoreConflictError, "terminal"):
            await store.transition_run(
                "dag-owner",
                run.id,
                expected_version=terminal.run_version,
                run_patch={"status": RunStatus.FAILED},
                owner_token=winning_token,
                expected_fence_epoch=claimed.fence_epoch,
            )

        # Expiry assigns a new fencing epoch, and the former owner cannot write.
        expiring_run = run.model_copy(
            update={
                "id": f"{run.id}-expiring",
                "run_version": 0,
                "event_sequence": 0,
                "owner_token": None,
                "lease_expires_at": None,
                "fence_epoch": 0,
                "processed_command_ids": [],
                "status": RunStatus.RUNNING,
            },
            deep=True,
        )
        await store.save_run(expiring_run)
        old_owner = await store.claim_run(
            "dag-owner",
            expiring_run.id,
            owner_token="expiring-worker",
            lease_seconds=2.0,
        )
        with self.assertRaises(TaskStoreConflictError):
            await second_store.claim_run(
                "dag-owner", expiring_run.id, owner_token="replacement-worker"
            )
        await asyncio.sleep(2.1)
        new_owner = await second_store.claim_run(
            "dag-owner", expiring_run.id, owner_token="replacement-worker"
        )
        self.assertEqual(new_owner.fence_epoch, old_owner.fence_epoch + 1)
        with self.assertRaisesRegex(TaskStoreConflictError, "stale"):
            await store.transition_run(
                "dag-owner",
                expiring_run.id,
                expected_version=new_owner.run_version,
                run_patch={"control_mode": "paused"},
                owner_token="expiring-worker",
                expected_fence_epoch=old_owner.fence_epoch,
            )

        restarted_store = PostgresTaskStore(database.engine)
        restored = await restarted_store.get_run("dag-owner", run.id)
        self.assertEqual(restored.status, RunStatus.SUCCEEDED)
        self.assertEqual(len(restored.node_runs[0].metadata), 100)
        restored_events = await restarted_store.list_events("dag-owner", run.id)
        self.assertEqual(restored.event_sequence, len(restored_events))

        # A fresh service instance can read a paused checkpoint and continue
        # only the still-pending dependent node.
        resume_run = TaskRunRecord(
            task_id=task.id,
            user_id="dag-owner",
            task_revision=task.revision,
            nodes=task.nodes,
            node_runs=[
                NodeRunRecord(
                    node_id=task.nodes[0].id,
                    name=task.nodes[0].name,
                    prompt=task.nodes[0].prompt,
                    order=0,
                    status=NodeRunStatus.SUCCEEDED,
                    output="A checkpoint",
                ),
                NodeRunRecord(
                    node_id=task.nodes[1].id,
                    name=task.nodes[1].name,
                    prompt=task.nodes[1].prompt,
                    order=1,
                ),
            ],
            status=RunStatus.QUEUED,
            control_mode="paused",
            engine="dag",
            graph=run.graph,
        )
        await store.save_run(resume_run)
        resume_executor = _Executor()
        resumed_service = TaskService(
            restarted_store,
            preview_executor=resume_executor,
            enable_dag_scheduler=True,
        )
        self.services.append(resumed_service)
        visible = await resumed_service.get_run("dag-owner", resume_run.id)
        self.assertEqual(visible.control_mode, "paused")
        continued = await resumed_service.continue_run(
            "dag-owner",
            resume_run.id,
            TaskRunControlRequest(
                request_id="postgres-restart-continue",
                expected_run_version=visible.run_version,
            ),
        )
        self.assertEqual(continued.id, resume_run.id)
        await resumed_service._active_runs[resume_run.id]
        finished = await restarted_store.get_run("dag-owner", resume_run.id)
        self.assertEqual(finished.status, RunStatus.SUCCEEDED)
        self.assertEqual(finished.node_runs[0].status, NodeRunStatus.SUCCEEDED)
        self.assertEqual(finished.node_runs[1].status, NodeRunStatus.SUCCEEDED)
        self.assertEqual(resume_executor.calls, [task.nodes[1].name])
