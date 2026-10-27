"""Task P2 planning confirmation and Run snapshot integration tests."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from examples.agent_service.task._executor import TaskExecutor
from examples.agent_service.task._models import (
    AgentStepConfig,
    CreateTaskRequest,
    ExecutionContext,
    StepType,
    TaskContext,
    TaskGraphEdge,
    TaskGraphInputBinding,
    TaskNode,
    TaskPreflightRequest,
    TaskRunRequest,
    TaskToolSchema,
    ToolStepConfig,
    UpdateTaskRequest,
)
from examples.agent_service.task._planning_contract import (
    TaskPlanningCandidate,
    TaskPlanningConfirmRequest,
    TaskPlanningNode,
    TaskPlanningRequest,
    TaskPlanningRuntimePolicy,
)
from examples.agent_service.task._planning_decision import decide_task_plan
from examples.agent_service.task._service import TaskService, TaskWorkflowConflictError
from examples.agent_service.task._store import TaskStore


def _agent(node_id: str, name: str, prompt: str, order: int) -> TaskPlanningNode:
    return TaskPlanningNode(
        id=node_id,
        name=name,
        type=StepType.AGENT,
        config=AgentStepConfig(prompt=prompt),
        order=order,
    )


def _linear_candidate() -> TaskPlanningCandidate:
    return TaskPlanningCandidate(
        suggested_title="线性目标处理",
        intent="先分析目标，再形成结果。",
        recommended_mode="dag",
        reason="模型建议按顺序完成。",
        nodes=[
            _agent("analyze", "分析目标", "提取目标与约束。", 0),
            _agent("deliver", "形成结果", "结合分析结论完成交付。", 1),
        ],
        edges=[TaskGraphEdge(source_node_id="analyze", target_node_id="deliver")],
    )


def _retrieval_tools() -> list[TaskToolSchema]:
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    return [
        TaskToolSchema(
            name=name,
            description="只读检索",
            input_schema=schema,
            is_read_only=True,
        )
        for name in ("project.search", "policy.search")
    ]


def _retrieval_candidate() -> TaskPlanningCandidate:
    return TaskPlanningCandidate(
        suggested_title="并行检索并汇总",
        intent="检索项目和规则资料并形成汇总。",
        recommended_mode="sop",
        reason="模型建议先收集目标，再并行检索。",
        nodes=[
            TaskPlanningNode(
                id="projects",
                name="检索项目资料",
                type=StepType.TOOL,
                config=ToolStepConfig(
                    tool_name="project.search",
                    arguments={"query": "{{user_input}}"},
                ),
                order=0,
            ),
            TaskPlanningNode(
                id="policies",
                name="检索规则资料",
                type=StepType.TOOL,
                config=ToolStepConfig(
                    tool_name="policy.search",
                    arguments={"query": "{{user_input}}"},
                ),
                order=1,
            ),
            _agent("merge", "汇总结果", "综合绑定的项目和规则来源。", 2),
        ],
        edges=[
            TaskGraphEdge(source_node_id="projects", target_node_id="merge"),
            TaskGraphEdge(source_node_id="policies", target_node_id="merge"),
        ],
        input_bindings={
            "merge": {
                "project_records": TaskGraphInputBinding(
                    source_node_id="projects",
                    source_value="output",
                ),
                "policy_records": TaskGraphInputBinding(
                    source_node_id="policies",
                    source_value="output",
                ),
            },
        },
    )


class _PermissionExecutor(TaskExecutor):
    async def list_tools(self, context: ExecutionContext) -> list[TaskToolSchema]:
        del context
        return _retrieval_tools()

    async def preflight_tool_permission(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: ExecutionContext,
        arguments_resolved: bool,
    ) -> tuple[str, str]:
        del tool_name, arguments, context, arguments_resolved
        return "allowed", "server permission fixture allowed the read-only tool"


class _DeterministicPlanningTestDouble:
    """Replace only the model call while keeping P1 validation real."""

    def __init__(self, candidate: TaskPlanningCandidate) -> None:
        self.candidate = candidate

    async def plan_with_decision(
        self,
        *,
        tool_schemas: list[TaskToolSchema] | None,
        runtime_policy: TaskPlanningRuntimePolicy,
        template_contract: Any = None,
        **_kwargs: Any,
    ):
        return decide_task_plan(
            self.candidate,
            tool_schemas=tool_schemas,
            runtime_policy=runtime_policy,
            template_contract=template_contract,
        )


class _PlanningTestService(TaskService):
    def __init__(self, *args: Any, test_tools: list[TaskToolSchema], **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._test_tools = test_tools

    async def list_tools(self, user_id: str, context: TaskContext, *, strict: bool = False):
        del user_id, context, strict
        return self._test_tools


def _service(
    candidate: TaskPlanningCandidate,
    *,
    dag_enabled: bool,
    tool_schemas: list[TaskToolSchema] | None = None,
) -> tuple[_PlanningTestService, TaskStore]:
    store = TaskStore()
    tools = tool_schemas or []
    service = _PlanningTestService(
        store,
        planner=_DeterministicPlanningTestDouble(candidate),
        agentscope_executor=_PermissionExecutor(),
        enable_dag_scheduler=dag_enabled,
        enable_dag_editing=True,
        test_tools=tools,
    )
    # Verify Run creation and snapshots without starting a real executor.
    service._schedule_run = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    return service, store


async def _create_draft(service: TaskService, goal: str) -> str:
    task = await service.create_task(
        "planner-p2-owner",
        CreateTaskRequest(goal=goal, nodes=[], source_context=TaskContext()),
    )
    return task.id


async def _propose_and_confirm(service: TaskService, task_id: str):
    owner = "planner-p2-owner"
    proposed = await service.plan_task_with_decision(
        owner,
        task_id,
        TaskPlanningRequest(context=TaskContext()),
    )
    assert proposed is not None
    confirmed = await service.confirm_task_plan(
        owner,
        task_id,
        TaskPlanningConfirmRequest(
            planning_id=proposed.plan.planning_id,
            plan_version=proposed.plan_version,
            context=TaskContext(),
        ),
    )
    assert confirmed is not None
    return proposed, confirmed


def test_linear_plan_confirmation_creates_sop_run_with_frozen_plan() -> None:
    async def exercise() -> None:
        service, store = _service(_linear_candidate(), dag_enabled=True)
        task_id = await _create_draft(service, "分析需求并交付结论")
        proposed, confirmed = await _propose_and_confirm(service, task_id)

        assert proposed.plan.recommended_mode == "sop"
        assert confirmed.preflight.ready is True
        assert confirmed.task.graph is None
        assert confirmed.task.planning_confirmed_id == proposed.plan.planning_id

        run = await service.create_run(
            "planner-p2-owner",
            task_id,
            TaskRunRequest(input="运行目标"),
        )
        assert run is not None
        assert run.engine == "sop"
        assert run.planning_id == proposed.plan.planning_id
        assert run.planning_version == confirmed.plan_version
        assert run.planning_snapshot["recommended_mode"] == "sop"

        frozen = await service.get_run("planner-p2-owner", run.id)
        latest_task = await service.get_task("planner-p2-owner", task_id)
        assert frozen is not None and latest_task is not None
        await service.update_task(
            "planner-p2-owner",
            task_id,
            UpdateTaskRequest(goal="新目标"),
        )
        after_edit = await service.get_run("planner-p2-owner", run.id)
        assert after_edit == frozen
        assert await store.list_runs("planner-p2-owner", task_id)
        await service.shutdown()

    asyncio.run(exercise())


def test_independent_read_only_retrieval_confirms_and_creates_dag_run() -> None:
    async def exercise() -> None:
        service, _ = _service(
            _retrieval_candidate(),
            dag_enabled=True,
            tool_schemas=_retrieval_tools(),
        )
        task_id = await _create_draft(service, "检索项目和授权规则并汇总")
        proposed, confirmed = await _propose_and_confirm(service, task_id)

        assert proposed.plan.recommended_mode == "dag"
        assert proposed.plan.execution_allowed is True
        assert proposed.plan.max_concurrency == 2
        assert proposed.plan.input_bindings["merge"].keys() == {
            "project_records",
            "policy_records",
        }
        assert confirmed.task.graph is not None
        assert confirmed.preflight.ready is True

        run = await service.create_run(
            "planner-p2-owner",
            task_id,
            TaskRunRequest(input="检索输入"),
        )
        assert run is not None and run.engine == "dag"
        assert run.graph == confirmed.task.graph
        assert run.planning_id == proposed.plan.planning_id
        await service.shutdown()

    asyncio.run(exercise())


def test_unapproved_dag_plan_is_reviewable_but_cannot_create_run() -> None:
    async def exercise() -> None:
        service, store = _service(
            _retrieval_candidate(),
            dag_enabled=False,
            tool_schemas=_retrieval_tools(),
        )
        task_id = await _create_draft(service, "检索项目和授权规则并汇总")
        proposed, confirmed = await _propose_and_confirm(service, task_id)

        assert proposed.plan.recommended_mode == "dag"
        assert proposed.plan.execution_allowed is False
        assert confirmed.task.graph is not None
        assert confirmed.preflight.ready is False
        assert any(
            issue.path == "graph.execution_enabled"
            for issue in confirmed.preflight.issues
        )
        with pytest.raises(TaskWorkflowConflictError, match="DAG execution is disabled"):
            await service.create_run(
                "planner-p2-owner",
                task_id,
                TaskRunRequest(input="must remain blocked"),
            )
        assert await store.list_runs("planner-p2-owner", task_id) == []
        await service.shutdown()

    asyncio.run(exercise())
