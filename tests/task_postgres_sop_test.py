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

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from examples.agent_service.persistence.database import ApplicationDatabase
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    AgentStepConfig,
    CreateTaskRequest,
    InputStepConfig,
    NodeRunStatus,
    RunStatus,
    TaskInputQuestion,
    TaskInputReply,
    TaskNode,
    TaskRunRequest,
)
from examples.agent_service.task._postgres_store import PostgresTaskStore
from examples.agent_service.task._service import TaskService

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
            with self.assertRaises(IntegrityError) as failure:
                await store.update_run(
                    "owner", run.id, status=RunStatus.AWAITING_INPUT
                )
            self.assertIn("task_runs_status_check", str(failure.exception))

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
            await store.update_run("owner", run.id, status=status)

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
