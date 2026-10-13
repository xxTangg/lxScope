# -*- coding: utf-8 -*-
"""Task business behavior around the official SOP engine, without model calls."""

import asyncio
from unittest import IsolatedAsyncioTestCase

from agentscope.sop import SOPRunState
from agentscope.app.message_bus import InMemoryMessageBus

from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    AgentStepConfig,
    ArtifactConfig,
    ArtifactFormat,
    CreateTaskRequest,
    InputStepConfig,
    RunStatus,
    StepVerdict,
    TaskInputQuestion,
    TaskInputReply,
    TaskNode,
    TaskRunRecord,
    TaskRunRequest,
    UpdateTaskRequest,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore


class _Executor:
    def __init__(self, *, reject=0, ask=False, fail=False):
        self.calls = []
        self.reject = reject
        self.ask = ask
        self.fail = fail
        self.files = {}

    async def execute_node(self, *, node, previous_output, context):
        self.calls.append((node.name, previous_output, context))
        if self.fail:
            return StepExecutionResult(
                text="", status="failed", reason="Data source unavailable"
            )
        if self.ask and not context.user_answers:
            return StepExecutionResult(
                text="",
                status="needs_input",
                reason="缺少出差日期",
                questions=[TaskInputQuestion(key="date", label="哪天出差？")],
            )
        return StepExecutionResult(text=f"{node.name}:" + previous_output)

    async def verify_node(self, **kwargs):
        del kwargs
        if self.reject:
            self.reject -= 1
            return StepVerdict(passed=False, message="请补充数据来源")
        return StepVerdict(passed=True)

    async def write_artifact(self, *, path, data, context):
        del context
        self.files[path] = data
        return len(data)

    async def read_artifact(self, *, path, context):
        del context
        return self.files[path]

    async def preflight_artifact_writer(self, *, context):
        del context
        return True, "Isolated Task test writer is ready."


def _agent(name="生成方案", **kwargs):
    return TaskNode(name=name, config=AgentStepConfig(prompt=name), **kwargs)


def _input():
    return TaskNode(
        name="确认出差需求",
        type="input",
        config=InputStepConfig(
            questions=[
                TaskInputQuestion(key="destination", label="目的地？"),
                TaskInputQuestion(key="date", label="出差日期？"),
            ]
        ),
    )


class TaskSOPTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = TaskStore()
        self.executor = _Executor()
        self.service = TaskService(
            self.store,
            preview_executor=self.executor,
            artifact_writer=self.executor,
        )
        self.services = [self.service]

    async def asyncTearDown(self):
        for service in self.services:
            await service.shutdown()

    async def _create(self, nodes):
        task = await self.service.create_task(
            "owner", CreateTaskRequest(goal="出差规划", nodes=nodes)
        )
        run = await self.service.create_run(
            "owner", task.id, TaskRunRequest(input="上海出发")
        )
        await self.service._active_runs[run.id]
        return await self.service.get_run("owner", run.id)

    async def _join(self, service, run_id):
        active = service._active_runs.get(run_id)
        if active:
            await active
        return await service.get_run("owner", run_id)

    async def test_input_parks_without_running_later_steps_or_creating_artifacts(
        self,
    ):
        run = await self._create(
            [
                _input(),
                _agent(
                    artifact=ArtifactConfig(format=ArtifactFormat.MARKDOWN)
                ),
            ]
        )
        self.assertEqual(run.status, RunStatus.AWAITING_INPUT)
        self.assertEqual(run.engine, "sop")
        self.assertEqual(
            [node.status.value for node in run.node_runs],
            ["awaiting_input", "pending"],
        )
        self.assertEqual(self.executor.calls, [])

        self.assertEqual(run.artifacts, [])
        self.assertEqual(self.executor.files, {})
        self.assertEqual(
            SOPRunState.model_validate(run.sop_state).phase.value, "awaiting"
        )
        self.assertEqual(
            (
                await self.service.get_task("owner", run.task_id)
            ).last_run_status,
            run.status,
        )

    async def test_old_run_cancellation_does_not_replace_latest_run_summary(
        self,
    ) -> None:
        old = await self._create([_input(), _agent()])
        latest = await self.service.create_run(
            "owner", old.task_id, TaskRunRequest()
        )
        await self.service._active_runs[latest.id]
        await self.service.cancel_run("owner", old.id)
        task = await self.service.get_task("owner", old.task_id)
        self.assertEqual(task.last_run_id, latest.id)
        self.assertEqual(task.last_run_status, RunStatus.AWAITING_INPUT)

    async def test_answer_after_service_restart_uses_frozen_definition_and_same_run(
        self,
    ):
        run = await self._create(
            [_agent("前置步骤"), _input(), _agent("原始交付")]
        )
        # A JSON round trip and a new service simulate releasing all in-memory state.
        await self.store.save_run(
            TaskRunRecord.model_validate_json(run.model_dump_json())
        )
        resumed = TaskService(self.store, preview_executor=self.executor)
        self.services.append(resumed)
        await self.service.update_task(
            "owner", run.task_id, UpdateTaskRequest(nodes=[_agent("新定义")])
        )
        accepted = await resumed.answer_run(
            "owner",
            run.id,
            TaskInputReply(
                request_id=run.pending_input.id,
                answers={"destination": "北京", "date": "2026-10-12"},
            ),
        )
        self.assertEqual(accepted.id, run.id)
        completed = await self._join(resumed, run.id)
        self.assertEqual(completed.status, RunStatus.SUCCEEDED)
        self.assertEqual(
            [call[0] for call in self.executor.calls], ["前置步骤", "原始交付"]
        )
        context = self.executor.calls[-1][2]
        self.assertEqual(context.initial_input, "上海出发")
        self.assertEqual(
            context.user_answers[-1].answers["destination"], "北京"
        )
        self.assertIsNone(completed.pending_input)

    async def test_agent_missing_data_asks_and_resumes_current_step(self):
        self.executor.ask = True
        run = await self._create([_agent("收集需求"), _agent("交付")])
        self.assertEqual(run.status, RunStatus.AWAITING_INPUT)
        self.assertEqual(
            [call[0] for call in self.executor.calls], ["收集需求"]
        )
        self.assertEqual(run.sop_state["steps"][0]["verifications"], [])
        await self.service.answer_run(
            "owner",
            run.id,
            TaskInputReply(
                request_id=run.pending_input.id,
                answers={"date": "2026-10-12"},
            ),
        )
        completed = await self._join(self.service, run.id)
        self.assertEqual(completed.status, RunStatus.SUCCEEDED)
        self.assertEqual(
            [call[0] for call in self.executor.calls],
            ["收集需求", "收集需求", "交付"],
        )

    async def test_invalid_and_foreign_answers_cannot_advance(self):
        run = await self._create([_input(), _agent()])
        for reply in (
            TaskInputReply(request_id="stale", answers={"date": "明天"}),
            TaskInputReply(
                request_id=run.pending_input.id,
                answers={"destination": "北京"},
            ),
            TaskInputReply(
                request_id=run.pending_input.id,
                answers={
                    "destination": "北京",
                    "date": "明天",
                    "unknown": "x",
                },
            ),
        ):
            with self.assertRaises(ValueError):
                await self.service.answer_run("owner", run.id, reply)
        foreign = await self.service.answer_run(
            "other",
            run.id,
            TaskInputReply(
                request_id=run.pending_input.id,
                answers={"destination": "北京", "date": "明天"},
            ),
        )
        self.assertIsNone(foreign)
        self.assertEqual(
            (await self.service.get_run("owner", run.id)).status,
            RunStatus.AWAITING_INPUT,
        )
        self.assertEqual(self.executor.calls, [])

    async def test_concurrent_duplicate_answers_schedule_only_one_execution(
        self,
    ):
        run = await self._create([_input(), _agent()])
        reply = TaskInputReply(
            request_id=run.pending_input.id,
            answers={"destination": "北京", "date": "明天"},
        )
        replies = await asyncio.gather(
            *[
                self.service.answer_run("owner", run.id, reply)
                for _ in range(5)
            ]
        )
        self.assertTrue(all(result.id == run.id for result in replies))
        completed = await self._join(self.service, run.id)
        self.assertEqual(completed.status, RunStatus.SUCCEEDED)
        self.assertEqual(len(self.executor.calls), 1)
        with self.assertRaises(ValueError):
            await self.service.answer_run(
                "owner",
                run.id,
                reply.model_copy(
                    update={"answers": {"destination": "天津", "date": "明天"}}
                ),
            )

    async def test_verifier_retries_current_step_with_feedback(self):
        self.executor.reject = 1
        run = await self._create(
            [
                _agent("验收步骤", acceptance_criteria="有数据来源"),
                _agent("交付"),
            ]
        )
        self.assertEqual(run.status, RunStatus.SUCCEEDED)
        self.assertEqual(
            [call[0] for call in self.executor.calls],
            ["验收步骤", "验收步骤", "交付"],
        )
        self.assertEqual(
            self.executor.calls[1][2].verification_feedback, "请补充数据来源"
        )
        self.assertEqual(len(run.sop_state["steps"][0]["verifications"]), 2)
        events = await self.service.list_events("owner", run.id)
        self.assertIn("step.rejected", [event.type for event in events])

    async def test_attempt_limit_and_explicit_failure_stop_later_steps(self):
        self.executor.reject = 20
        run = await self._create(
            [
                _agent("验收步骤", acceptance_criteria="完整", max_attempts=2),
                _agent("不可执行"),
            ]
        )
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(len(self.executor.calls), 2)
        self.assertEqual(run.sop_state["steps"][0]["phase"], "failed")
        self.executor.calls.clear()
        self.executor.fail = True
        run = await self._create([_agent("失败步骤"), _agent("不可执行")])
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(len(self.executor.calls), 1)
        self.assertIn("Data source unavailable", run.error)

    async def test_cancel_parked_run_prevents_answer_and_retains_history(self):
        run = await self._create([_input(), _agent()])
        canceled = await self.service.cancel_run("owner", run.id)
        self.assertEqual(canceled.status, RunStatus.CANCELED)
        self.assertIsNone(canceled.pending_input)
        self.assertTrue(
            all(node.status.value == "canceled" for node in canceled.node_runs)
        )
        with self.assertRaises(ValueError):
            await self.service.answer_run(
                "owner",
                run.id,
                TaskInputReply(
                    request_id=run.pending_input.id,
                    answers={"destination": "北京", "date": "明天"},
                ),
            )
        events = await self.service.list_events("owner", run.id)
        self.assertIn("input.requested", [event.type for event in events])
        self.assertEqual(events[-1].type, "run.canceled")

    async def test_cancel_from_another_worker_stops_the_executor(self):
        bus = InMemoryMessageBus()
        started = asyncio.Event()
        stopped = asyncio.Event()

        class BlockingExecutor:
            async def execute_node(self, **kwargs):
                del kwargs
                started.set()
                try:
                    await asyncio.sleep(3600)
                finally:
                    stopped.set()

        worker = TaskService(
            self.store, preview_executor=BlockingExecutor(), message_bus=bus
        )
        controller = TaskService(self.store, message_bus=bus)
        self.services.extend([worker, controller])
        task = await worker.create_task(
            "owner", CreateTaskRequest(goal="取消测试", nodes=[_agent()])
        )
        run = await worker.create_run("owner", task.id, TaskRunRequest())
        await asyncio.wait_for(started.wait(), 2)
        await controller.cancel_run("owner", run.id)
        await asyncio.wait_for(stopped.wait(), 2)
        await self._join(worker, run.id)
        self.assertEqual(
            (await worker.get_run("owner", run.id)).status, RunStatus.CANCELED
        )
        await bus.aclose()

    async def test_executor_exception_keeps_official_and_task_states_consistent(
        self,
    ):
        class BrokenExecutor:
            async def execute_node(self, **kwargs):
                del kwargs
                raise RuntimeError("model unavailable")

        service = TaskService(self.store, preview_executor=BrokenExecutor())
        self.services.append(service)
        task = await service.create_task(
            "owner", CreateTaskRequest(goal="失败测试", nodes=[_agent()])
        )
        run = await service.create_run("owner", task.id, TaskRunRequest())
        await service._active_runs[run.id]
        failed = await service.get_run("owner", run.id)
        self.assertEqual(failed.status, RunStatus.FAILED)
        self.assertEqual(failed.node_runs[0].status.value, "failed")
        self.assertEqual(
            SOPRunState.model_validate(failed.sop_state).phase.value, "failed"
        )

    async def test_old_duplicate_reply_cannot_answer_a_new_question(self):
        run = await self._create([_input(), _input(), _agent()])
        reply = TaskInputReply(
            request_id=run.pending_input.id,
            answers={"destination": "北京", "date": "明天"},
        )
        await self.service.answer_run("owner", run.id, reply)
        parked = await self._join(self.service, run.id)
        self.assertEqual(parked.status, RunStatus.AWAITING_INPUT)
        self.assertNotEqual(parked.pending_input.id, reply.request_id)
        duplicate = await self.service.answer_run("owner", run.id, reply)
        self.assertEqual(duplicate.pending_input.id, parked.pending_input.id)
        self.assertEqual(self.executor.calls, [])
