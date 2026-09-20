# -*- coding: utf-8 -*-
"""MVP Task pipeline tests without a real MCP server."""

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
