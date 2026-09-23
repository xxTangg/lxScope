# -*- coding: utf-8 -*-
"""MVP Task pipeline tests without a real MCP server."""

import asyncio
from unittest import IsolatedAsyncioTestCase

from examples.agent_service.task._executor import (
    StepExecutionResult,
    TaskExecutor,
)
from examples.agent_service.task._models import (
    AgentStepConfig,
    CreateTaskRequest,
    ExecutionContext,
    PythonStepConfig,
    StepType,
    TaskContext,
    TaskNode,
    TaskRunRequest,
    ToolStepConfig,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore


class _MockToolTaskExecutor(TaskExecutor):
    """Deterministic Task executor that simulates one tool without MCP."""

    def __init__(self) -> None:
        self.calls: list[tuple[StepType, str]] = []

    async def execute_node(
        self,
        *,
        node: TaskNode,
        previous_output: str,
        context: ExecutionContext,
    ) -> StepExecutionResult:
        del context
        self.calls.append((node.type, previous_output))

        if node.type == StepType.AGENT:
            output = f"agent:{previous_output}"
        elif node.type == StepType.TOOL:
            assert isinstance(node.config, ToolStepConfig)
            assert node.config.tool_name == "mock.echo"
            assert node.config.arguments == {"value": "{{previous_output}}"}
            output = f"tool:{previous_output}"
        elif node.type == StepType.PYTHON:
            assert isinstance(node.config, PythonStepConfig)
            output = f"python:{previous_output.upper()}"
        else:  # pragma: no cover - guarded by the typed Task model
            raise AssertionError(f"Unexpected step type: {node.type}")

        return StepExecutionResult(text=output)


class _HangingTaskPlanner:
    """Planner fixture that simulates a model request that never returns."""

    async def plan(self, **_kwargs):
        await asyncio.sleep(3600)


class _BlockingTaskExecutor(TaskExecutor):
    """Executor fixture that leaves a Run active until it is cancelled."""

    async def execute_node(
        self,
        *,
        node: TaskNode,
        previous_output: str,
        context: ExecutionContext,
    ) -> StepExecutionResult:
        del node, previous_output, context
        await asyncio.sleep(3600)
        raise AssertionError("The blocking executor should be cancelled first.")


class _CancellationSwallowingTaskExecutor(TaskExecutor):
    """Simulate an adapter that converts cancellation into a normal result."""

    async def execute_node(
        self,
        *,
        node: TaskNode,
        previous_output: str,
        context: ExecutionContext,
    ) -> StepExecutionResult:
        del node, previous_output, context
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return StepExecutionResult(text="adapter swallowed cancellation")
        raise AssertionError("The executor should be cancelled first.")


class TestTaskMvpPipeline(IsolatedAsyncioTestCase):
    """Verify the typed linear pipeline independently from MCP."""

    async def test_agent_tool_python_agent_pipeline_without_mcp(self) -> None:
        """Run all three step types and pass every output to the next step."""

        executor = _MockToolTaskExecutor()
        service = TaskService(
            TaskStore(),
            agentscope_executor=executor,
            preview_executor=executor,
        )
        nodes = [
            TaskNode(
                name="理解目标",
                type=StepType.AGENT,
                config=AgentStepConfig(prompt="提取任务目标"),
                order=0,
            ),
            TaskNode(
                name="调用模拟工具",
                type=StepType.TOOL,
                config=ToolStepConfig(
                    tool_name="mock.echo",
                    arguments={"value": "{{previous_output}}"},
                ),
                order=1,
            ),
            TaskNode(
                name="处理结果",
                type=StepType.PYTHON,
                config=PythonStepConfig(
                    code="print(previous_output.upper())",
                ),
                order=2,
            ),
            TaskNode(
                name="生成结论",
                type=StepType.AGENT,
                config=AgentStepConfig(prompt="整理最终结果"),
                order=3,
            ),
        ]
        task = await service.create_task(
            "test-user",
            CreateTaskRequest(
                title="无 MCP 的 Task 链路测试",
                goal="验证 Agent、Tool、Python、Agent 线性执行",
                nodes=nodes,
                source_context=TaskContext(
                    agent_id="fake-agent",
                    session_id="fake-session",
                ),
            ),
        )

        run = await service.create_run(
            "test-user",
            task.id,
            TaskRunRequest(input="原始目标"),
        )
        self.assertIsNotNone(run)
        assert run is not None

        execution = service._active_runs[run.id]  # pylint: disable=protected-access
        await execution
        completed = await service.get_run("test-user", run.id)
        self.assertIsNotNone(completed)
        assert completed is not None

        self.assertEqual(completed.status.value, "succeeded")
        self.assertEqual(
            [node_run.status.value for node_run in completed.node_runs],
            ["succeeded", "succeeded", "succeeded", "succeeded"],
        )
        self.assertEqual(
            [node_run.type for node_run in completed.node_runs],
            [
                StepType.AGENT,
                StepType.TOOL,
                StepType.PYTHON,
                StepType.AGENT,
            ],
        )
        self.assertEqual(
            [step_type for step_type, _ in executor.calls],
            [
                StepType.AGENT,
                StepType.TOOL,
                StepType.PYTHON,
                StepType.AGENT,
            ],
        )
        self.assertEqual(
            [previous for _, previous in executor.calls],
            [
                "原始目标",
                "agent:原始目标",
                "tool:agent:原始目标",
                "python:TOOL:AGENT:原始目标",
            ],
        )
        self.assertEqual(
            completed.final_output,
            "agent:python:TOOL:AGENT:原始目标",
        )

    async def test_cancel_updates_task_summary_and_emits_terminal_event(self) -> None:
        """Stopping a Run must not leave the task list at queued/running."""

        service = TaskService(TaskStore(), preview_executor=_BlockingTaskExecutor())
        task = await service.create_task(
            "test-user",
            CreateTaskRequest(
                title="取消状态同步测试",
                goal="验证停止执行后的任务摘要",
                nodes=[
                    TaskNode(
                        name="阻塞步骤",
                        type=StepType.AGENT,
                        config=AgentStepConfig(prompt="等待取消"),
                    ),
                ],
            ),
        )
        run = await service.create_run(
            "test-user",
            task.id,
            TaskRunRequest(input="原始目标"),
        )
        self.assertIsNotNone(run)
        assert run is not None

        # Let the background coroutine claim the Run before issuing Stop.
        await asyncio.sleep(0)
        active = service._active_runs[run.id]  # pylint: disable=protected-access
        canceled = await service.cancel_run("test-user", run.id)
        self.assertIsNotNone(canceled)
        assert canceled is not None
        await active

        stored_task = await service.get_task("test-user", task.id)
        self.assertIsNotNone(stored_task)
        assert stored_task is not None
        self.assertEqual(canceled.status.value, "canceled")
        self.assertEqual(stored_task.last_run_id, run.id)
        self.assertEqual(stored_task.last_run_status.value, "canceled")
        self.assertEqual(
            [event.type for event in await service.list_events("test-user", run.id)],
            ["run.created", "run.started", "step.started", "run.canceled"],
        )

    async def test_cancel_cannot_be_overwritten_by_an_executor_result(self) -> None:
        """A cancellation-swallowing adapter must still leave a canceled Run."""

        service = TaskService(
            TaskStore(),
            preview_executor=_CancellationSwallowingTaskExecutor(),
        )
        task = await service.create_task(
            "test-user",
            CreateTaskRequest(
                title="取消终态保护测试",
                goal="停止后不得改写为失败或成功",
                nodes=[
                    TaskNode(
                        name="可能吞掉取消的步骤",
                        type=StepType.AGENT,
                        config=AgentStepConfig(prompt="等待取消"),
                    ),
                ],
            ),
        )
        run = await service.create_run(
            "test-user",
            task.id,
            TaskRunRequest(input="原始目标"),
        )
        self.assertIsNotNone(run)
        assert run is not None

        await asyncio.sleep(0)
        active = service._active_runs[run.id]  # pylint: disable=protected-access
        await service.cancel_run("test-user", run.id)
        await active

        stored_run = await service.get_run("test-user", run.id)
        stored_task = await service.get_task("test-user", task.id)
        self.assertIsNotNone(stored_run)
        self.assertIsNotNone(stored_task)
        assert stored_run is not None and stored_task is not None
        self.assertEqual(stored_run.status.value, "canceled")
        self.assertEqual(stored_task.last_run_status.value, "canceled")

    async def test_generation_timeout_returns_editable_fallback(self) -> None:
        """A hung planner must not leave a task permanently generating."""

        service = TaskService(
            TaskStore(),
            planner=_HangingTaskPlanner(),
            generation_timeout_seconds=0.01,
        )
        task = await service.create_task(
            "test-user",
            CreateTaskRequest(
                title="超时兜底测试",
                goal="验证任务生成超时后仍可编辑",
                source_context=TaskContext(
                    agent_id="fake-agent",
                    session_id="fake-session",
                ),
            ),
        )

        generated = await service.generate_task(
            "test-user",
            task.id,
            task.source_context,
        )

        self.assertIsNotNone(generated)
        assert generated is not None
        self.assertEqual(generated.generation_status.value, "succeeded")
        self.assertEqual(generated.status.value, "active")
        self.assertEqual(len(generated.nodes), 2)
