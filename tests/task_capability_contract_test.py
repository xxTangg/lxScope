"""Task business-capability contracts and read-only preflight coverage."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionRule,
)
from examples.agent_service.task._capability_contracts import (
    capability_category,
    capability_contract,
    validate_capability_input,
    validate_capability_output,
    validate_contract_tool_schema,
)
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    ExecutionContext,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    StepType,
    TaskContext,
    TaskNode,
    TaskPreflightRequest,
    TaskRecord,
    TaskRunRecord,
    TaskToolSchema,
    TaskStatus,
    ToolStepConfig,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_compiler import (
    TaskTemplateCompiler,
    TemplateCompileError,
)
from examples.agent_service.task._template_registry import TemplateRegistry
from examples.agent_service.task._template_models import TaskTemplateDefinition
from examples.agent_service.task._agentscope_executor import (
    AgentScopeTaskExecutor,
    _mcp_read_only_hint,
    _format_execution_history,
)


TASK_DIR = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "agent_service"
    / "task"
)
TEMPLATE_DIR = TASK_DIR / "templates"


def _project_profile() -> dict[str, Any]:
    return {
        "project_name": "测试项目",
        "project_brief": "核安全领域工程评审",
        "professional_fields": ["核安全"],
    }


def _lookup_result() -> dict[str, Any]:
    return {
        "contract_version": "task.expert-capability.v1",
        "source": {
            "system_id": "test-directory",
            "dataset_id": "test-experts",
            "data_version": "fixture-1",
            "queried_at": "2026-10-10T00:00:00Z",
        },
        "query_context": {
            "project_profile": _project_profile(),
            "candidate_limit": 3,
        },
        "candidates": [
            {
                "expert_id": "fixture-expert-1",
                "authorized_fields": {"field": "核安全"},
                "evidence_refs": [
                    {
                        "source_id": "fixture-record-1",
                        "record_version": "fixture-1",
                        "field": "professional_fields",
                    }
                ],
            }
        ],
    }


def _conflict_result(status: str = "clear") -> dict[str, Any]:
    return {
        "contract_version": "task.expert-capability.v1",
        "source": _lookup_result()["source"],
        "rule_version": "fixture-rules-1",
        "checked_at": "2026-10-10T00:00:01Z",
        "candidates": [
            {
                "expert_id": "fixture-expert-1",
                "conflict_status": status,
                "evidence_refs": [],
            }
        ],
    }


def test_nine_template_capabilities_have_explicit_categories() -> None:
    capability_keys: set[str] = set()
    for path in sorted(TEMPLATE_DIR.glob("s*.v1.json")):
        template = json.loads(path.read_text(encoding="utf-8"))
        capability_keys.update(template["required_capabilities"])
        capability_keys.update(
            step["capability_key"]
            for step in template["steps"]
            if step["capability_key"] is not None
        )

    assert len(list(TEMPLATE_DIR.glob("s*.v1.json"))) == 9
    assert capability_keys
    assert all(capability_category(key) != "unsupported" for key in capability_keys)
    assert all(capability_contract(key) is None for key in capability_keys - {
        "expert.lookup",
        "expert.conflict_check",
        "expert.rank",
    })


def test_expert_lookup_contract_checks_input_and_output_shape() -> None:
    lookup = capability_contract("expert.lookup")
    assert lookup is not None
    assert validate_capability_input(
        "expert.lookup",
        {"query": {"project_profile": _project_profile(), "candidate_limit": 3}},
    ) == ()
    result = _lookup_result()
    checked = validate_capability_output(
        "expert.lookup",
        lookup.output_schema or {},
        {"tool": "test", "chunks": []},
        json.dumps(result),
    )
    assert checked.passed is True
    assert checked.target == "tool_output_json"


def test_conflict_unknown_fails_closed_and_is_still_a_structural_status() -> None:
    contract = capability_contract("expert.conflict_check")
    assert contract is not None
    unknown = _conflict_result("unknown")
    assert validate_capability_output(
        "expert.conflict_check",
        contract.output_schema or {},
        {},
        json.dumps(unknown),
    ).passed is False
    assert validate_capability_output(
        "expert.conflict_check",
        contract.output_schema or {},
        {},
        json.dumps(_conflict_result("clear")),
    ).passed is True


def test_rank_input_rejects_unknown_but_accepts_reported_conflicts() -> None:
    unknown_payload = _conflict_result("unknown")
    known_conflict_payload = _conflict_result("conflict")
    assert validate_capability_input(
        "expert.rank",
        {"candidates": unknown_payload},
    )
    assert validate_capability_input(
        "expert.rank",
        {"candidates": known_conflict_payload},
    ) == ()


def test_contract_schema_binding_rejects_narrower_tool_interfaces() -> None:
    contract = capability_contract("expert.lookup")
    assert contract is not None and contract.input_schema is not None
    compatible = json.loads(json.dumps(contract.input_schema))
    assert validate_contract_tool_schema("expert.lookup", compatible) == ()

    incompatible = json.loads(json.dumps(contract.input_schema))
    incompatible["properties"]["query"]["properties"]["candidate_limit"][
        "maximum"
    ] = 2
    assert validate_contract_tool_schema("expert.lookup", incompatible)


def test_compiler_rejects_permissive_but_undeclared_expert_tool_schema() -> None:
    template = TemplateRegistry().get_template_version(
        "s03_expert_matching", "2.0.0"
    )
    assert template is not None
    from examples.agent_service.task._capability_contracts import capability_contract

    schemas = [
        TaskToolSchema(
            name=key,
            is_read_only=True,
            input_schema=(capability_contract(key).input_schema or {}),
        )
        for key in ("expert.lookup", "expert.conflict_check", "expert.rank")
    ]
    schemas[0] = schemas[0].model_copy(update={"input_schema": {"type": "object"}})

    try:
        TaskTemplateCompiler().compile_nodes(template, schemas)
    except TemplateCompileError as error:
        assert error.code == "TEMPLATE_CAPABILITY_INCOMPATIBLE"
    else:  # pragma: no cover - assertion provides clearer test failure
        raise AssertionError("A generic object schema cannot prove the lookup contract.")


class _PreflightOnlyExecutor:
    def __init__(self, *, permission: str = "allowed") -> None:
        self.permission = permission
        self.read_only = True
        self.read_only_hint: bool | None = None
        self.is_mcp = False
        self.tool_name = "fixture.read"
        self.discovery_error = False
        self.duplicate_registration = False
        self.tool_discovery_calls = 0
        self.permission_calls: list[tuple[str, bool]] = []
        self.execution_calls = 0

    async def list_tools(self, context: Any) -> list[TaskToolSchema]:
        del context
        self.tool_discovery_calls += 1
        if self.discovery_error:
            raise RuntimeError("fixture tool catalog unavailable")
        schema = TaskToolSchema(
            name=self.tool_name,
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            is_mcp=self.is_mcp,
            is_read_only=self.read_only,
            read_only_hint=self.read_only_hint,
        )
        if self.duplicate_registration:
            return [schema, schema.model_copy(deep=True)]
        return [schema]

    async def preflight_tool_permission(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: Any,
        arguments_resolved: bool,
    ) -> tuple[str, str]:
        del context
        self.permission_calls.append((tool_name, arguments_resolved))
        return self.permission, "fixture permission result"

    async def execute_node(self, **_: Any) -> StepExecutionResult:
        self.execution_calls += 1
        raise AssertionError("Preflight must never execute a Tool.")


def _plain_tool_task(
    arguments: dict[str, Any],
    *,
    tool_name: str = "fixture.read",
    capability_key: str | None = None,
) -> TaskRecord:
    return TaskRecord(
        user_id="capability-owner",
        title="Tool preflight fixture",
        goal="Run a read-only fixture Tool",
        status=TaskStatus.ACTIVE,
        nodes=[
            TaskNode(
                name="Fixture read",
                type=StepType.TOOL,
                config=ToolStepConfig(
                    tool_name=tool_name,
                    arguments=arguments,
                ),
                order=0,
                capability_key=capability_key,
            )
        ],
        source_context=TaskContext(
            agent_id="fixture-agent",
            session_id="fixture-session",
        ),
    )


def test_preflight_checks_permissions_without_invoking_tool() -> None:
    async def scenario() -> None:
        store = TaskStore()
        executor = _PreflightOnlyExecutor()
        service = TaskService(store, agentscope_executor=executor)  # type: ignore[arg-type]
        task = _plain_tool_task({"query": "static query"})
        await store.save_task(task)

        result = await service.preflight_task(
            task.user_id,
            task.id,
            TaskPreflightRequest(input="fixture input"),
        )

        assert result is not None and result.ready is True
        assert result.capability_checks[0].tool_available is True
        assert result.capability_checks[0].interface_compatible is True
        assert result.capability_checks[0].permission_status == "allowed"
        assert result.capability_checks[0].business_data_verified is None
        assert executor.permission_calls == [("fixture.read", True)]
        assert executor.execution_calls == 0

    asyncio.run(scenario())


def test_preflight_rejects_permission_denial_and_unresolved_permission() -> None:
    async def scenario() -> None:
        store = TaskStore()
        executor = _PreflightOnlyExecutor(permission="denied")
        service = TaskService(store, agentscope_executor=executor)  # type: ignore[arg-type]
        denied_task = _plain_tool_task({"query": "static query"})
        await store.save_task(denied_task)
        denied = await service.preflight_task(
            denied_task.user_id,
            denied_task.id,
            TaskPreflightRequest(input="fixture input"),
        )
        assert denied is not None and denied.ready is False
        assert denied.capability_checks[0].status == "permission_denied"

        executor.permission = "requires_runtime_check"
        unresolved_task = _plain_tool_task({"query": "{{previous_output}}"})
        await store.save_task(unresolved_task)
        unresolved = await service.preflight_task(
            unresolved_task.user_id,
            unresolved_task.id,
            TaskPreflightRequest(input="fixture input"),
        )
        assert unresolved is not None and unresolved.ready is False
        assert unresolved.capability_checks[0].permission_status == (
            "requires_runtime_check"
        )
        assert executor.execution_calls == 0

    asyncio.run(scenario())


def test_preflight_rejects_incompatible_schema_without_calling_tool() -> None:
    async def scenario() -> None:
        store = TaskStore()
        executor = _PreflightOnlyExecutor()
        executor.list_tools = incompatible_tool_schemas  # type: ignore[method-assign]
        service = TaskService(store, agentscope_executor=executor)  # type: ignore[arg-type]
        task = _plain_tool_task({"query": 5})
        await store.save_task(task)
        result = await service.preflight_task(
            task.user_id,
            task.id,
            TaskPreflightRequest(input="fixture input"),
        )
        assert result is not None and result.ready is False
        assert result.capability_checks[0].interface_compatible is False
        assert result.capability_checks[0].status == "incompatible"
        assert executor.execution_calls == 0

    async def incompatible_tool_schemas(context: Any) -> list[TaskToolSchema]:
        del context
        return [
            TaskToolSchema(
                name="fixture.read",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                is_read_only=True,
            )
        ]

    asyncio.run(scenario())


def test_preflight_distinguishes_duplicate_tool_binding_from_missing_tool() -> None:
    async def scenario() -> None:
        store = TaskStore()
        executor = _PreflightOnlyExecutor()
        executor.duplicate_registration = True
        service = TaskService(store, agentscope_executor=executor)  # type: ignore[arg-type]
        task = _plain_tool_task({"query": "static query"})
        await store.save_task(task)

        result = await service.preflight_task(
            task.user_id,
            task.id,
            TaskPreflightRequest(input="fixture input"),
        )

        assert result is not None and result.ready is False
        check = result.capability_checks[0]
        assert check.tool_available is True
        assert check.interface_compatible is False
        assert check.status == "incompatible"
        assert "重复注册" in check.message
        assert executor.permission_calls == []
        assert executor.execution_calls == 0

    asyncio.run(scenario())


def test_preflight_and_saved_tool_results_are_user_scoped() -> None:
    async def scenario() -> None:
        store = TaskStore()
        executor = _PreflightOnlyExecutor()
        service = TaskService(store, agentscope_executor=executor)  # type: ignore[arg-type]
        task = _plain_tool_task({"query": "static query"})
        await store.save_task(task)
        run = TaskRunRecord(
            task_id=task.id,
            user_id=task.user_id,
            task_revision=task.revision,
            nodes=task.nodes,
            node_runs=[
                NodeRunRecord(
                    node_id=task.nodes[0].id,
                    name="Fixture read",
                    prompt="",
                    type=StepType.TOOL,
                    order=0,
                    status=NodeRunStatus.SUCCEEDED,
                    output='{"private_evidence":"owner-only"}',
                    result={"private_evidence": "owner-only"},
                )
            ],
        )
        await store.save_run(run)

        assert (
            await service.preflight_task(
                "unrelated-user",
                task.id,
                TaskPreflightRequest(input="fixture input"),
            )
            is None
        )
        assert await service.get_run("unrelated-user", run.id) is None
        owner_run = await service.get_run(task.user_id, run.id)
        assert owner_run is not None
        assert owner_run.node_runs[0].result == {
            "private_evidence": "owner-only"
        }
        assert executor.tool_discovery_calls == 0
        assert executor.permission_calls == []
        assert executor.execution_calls == 0

    asyncio.run(scenario())


def test_preflight_reports_tool_catalog_outage_as_unverified() -> None:
    async def scenario() -> None:
        store = TaskStore()
        executor = _PreflightOnlyExecutor()
        executor.discovery_error = True
        service = TaskService(store, agentscope_executor=executor)  # type: ignore[arg-type]
        task = _plain_tool_task({"query": "static query"})
        await store.save_task(task)

        result = await service.preflight_task(
            task.user_id,
            task.id,
            TaskPreflightRequest(input="fixture input"),
        )

        assert result is not None and result.ready is False
        assert result.missing_capabilities == []
        check = result.capability_checks[0]
        assert check.tool_available is None
        assert check.status == "unverified"
        assert "工具目录无法访问" in check.message
        assert executor.permission_calls == []
        assert executor.execution_calls == 0

    asyncio.run(scenario())


def test_preflight_blocks_side_effect_tool_without_executing_it() -> None:
    async def scenario() -> None:
        store = TaskStore()
        executor = _PreflightOnlyExecutor()
        executor.read_only = False
        service = TaskService(store, agentscope_executor=executor)  # type: ignore[arg-type]
        task = _plain_tool_task({"query": "static query"})
        await store.save_task(task)

        result = await service.preflight_task(
            task.user_id,
            task.id,
            TaskPreflightRequest(input="fixture input"),
        )

        assert result is not None and result.ready is False
        assert result.capability_checks[0].external_side_effect is True
        assert result.capability_checks[0].status == "unsupported"
        assert executor.execution_calls == 0

    asyncio.run(scenario())


AMAP_WEATHER_TOOL = "mcp__amap-maps__maps_weather"


def test_mcp_read_only_metadata_parser_preserves_true_false_and_absent() -> None:
    def tool_with_hint(hint: Any) -> Any:
        annotations = (
            SimpleNamespace()
            if hint is None
            else SimpleNamespace(readOnlyHint=hint)
        )
        return SimpleNamespace(
            is_mcp=True,
            _tool=SimpleNamespace(annotations=annotations),
        )

    assert _mcp_read_only_hint(tool_with_hint(True)) is True
    assert _mcp_read_only_hint(tool_with_hint(False)) is False
    assert _mcp_read_only_hint(tool_with_hint(None)) is None


def test_preflight_allows_only_the_task_contracted_amap_weather_mcp() -> None:
    async def scenario() -> None:
        store = TaskStore()
        executor = _PreflightOnlyExecutor()
        executor.tool_name = AMAP_WEATHER_TOOL
        executor.is_mcp = True
        # Simulate an MCP descriptor with no readOnlyHint annotation.
        executor.read_only = False
        service = TaskService(store, agentscope_executor=executor)  # type: ignore[arg-type]
        task = _plain_tool_task(
            {"query": "上海"},
            tool_name=AMAP_WEATHER_TOOL,
        )
        await store.save_task(task)

        result = await service.preflight_task(
            task.user_id,
            task.id,
            TaskPreflightRequest(input="fixture input"),
        )

        assert result is not None and result.ready is True
        check = result.capability_checks[0]
        assert check.tool_available is True
        assert check.interface_compatible is True
        assert check.permission_status == "allowed"
        assert check.external_side_effect is False
        assert check.status == "available"
        assert executor.permission_calls == [(AMAP_WEATHER_TOOL, True)]
        assert executor.execution_calls == 0

    asyncio.run(scenario())


def test_preflight_keeps_unknown_mcp_conservatively_blocked() -> None:
    async def scenario() -> None:
        store = TaskStore()
        executor = _PreflightOnlyExecutor()
        executor.tool_name = "mcp__custom-maps__custom_weather"
        executor.is_mcp = True
        executor.read_only = False
        service = TaskService(store, agentscope_executor=executor)  # type: ignore[arg-type]
        task = _plain_tool_task(
            {"query": "上海"},
            tool_name=executor.tool_name,
        )
        await store.save_task(task)

        result = await service.preflight_task(
            task.user_id,
            task.id,
            TaskPreflightRequest(input="fixture input"),
        )

        assert result is not None and result.ready is False
        check = result.capability_checks[0]
        assert check.external_side_effect is True
        assert check.status == "unsupported"
        assert "未被只读" in check.message
        assert executor.execution_calls == 0

    asyncio.run(scenario())


def test_preflight_does_not_override_explicit_false_read_only_hint() -> None:
    async def scenario() -> None:
        store = TaskStore()
        executor = _PreflightOnlyExecutor()
        executor.tool_name = AMAP_WEATHER_TOOL
        executor.is_mcp = True
        executor.read_only = False
        executor.read_only_hint = False
        service = TaskService(store, agentscope_executor=executor)  # type: ignore[arg-type]
        task = _plain_tool_task(
            {"query": "上海"},
            tool_name=AMAP_WEATHER_TOOL,
        )
        await store.save_task(task)

        result = await service.preflight_task(
            task.user_id,
            task.id,
            TaskPreflightRequest(input="fixture input"),
        )

        assert result is not None and result.ready is False
        check = result.capability_checks[0]
        assert check.external_side_effect is True
        assert check.status == "unsupported"
        assert "未被只读" in check.message
        assert executor.execution_calls == 0

    asyncio.run(scenario())


def test_preflight_keeps_write_capability_blocked_even_with_mcp_metadata() -> None:
    async def scenario() -> None:
        store = TaskStore()
        executor = _PreflightOnlyExecutor()
        executor.tool_name = "mcp__notify__send_message"
        executor.is_mcp = True
        executor.read_only = True
        service = TaskService(store, agentscope_executor=executor)  # type: ignore[arg-type]
        task = _plain_tool_task(
            {"query": "message"},
            tool_name=executor.tool_name,
            capability_key="notification.send",
        )
        await store.save_task(task)

        result = await service.preflight_task(
            task.user_id,
            task.id,
            TaskPreflightRequest(input="fixture input"),
        )

        assert result is not None and result.ready is False
        check = result.capability_checks[0]
        assert check.external_side_effect is True
        assert check.status == "unsupported"
        assert executor.execution_calls == 0

    asyncio.run(scenario())


def test_template_compiler_accepts_only_contracted_weather_mcp_without_hint() -> None:
    template = TaskTemplateDefinition.model_validate(
        {
            "schema_version": 1,
            "template_id": "s99_weather_lookup",
            "template_version": "1.0.0",
            "title": "Weather lookup fixture",
            "description": "Task compiler safety contract fixture.",
            "category": "test",
            "required_capabilities": [AMAP_WEATHER_TOOL],
            "input_schema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Weather location.",
                    }
                },
                "additionalProperties": False,
            },
            "output_requirement": "Return the tool result.",
            "steps": [
                {
                    "step_key": "weather",
                    "title": "Get weather",
                    "kind": "tool",
                    "required": True,
                    "editable": False,
                    "capability_key": AMAP_WEATHER_TOOL,
                    "config": {
                        "type": "tool",
                        "arguments": {"query": "Shanghai"},
                    },
                    "output_requirement": "Return weather data.",
                    "failure_handling": "Stop if the lookup fails.",
                    "sensitive_information": "No sensitive data.",
                }
            ],
        }
    )

    compiled = TaskTemplateCompiler().compile_nodes(
        template,
        [
            TaskToolSchema(
                name=AMAP_WEATHER_TOOL,
                is_mcp=True,
                is_read_only=False,
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            )
        ],
    )

    assert compiled.nodes[0].config.tool_name == AMAP_WEATHER_TOOL

    try:
        TaskTemplateCompiler().compile_nodes(
            template,
            [
                TaskToolSchema(
                    name=AMAP_WEATHER_TOOL,
                    is_mcp=True,
                    is_read_only=False,
                    read_only_hint=False,
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                )
            ],
        )
    except TemplateCompileError as error:
        assert error.code == "TEMPLATE_CAPABILITY_UNSAFE"
    else:  # pragma: no cover - fail with a focused diagnostic
        raise AssertionError("An explicit non-read-only hint must be respected.")


def test_template_compiler_rejects_unknown_mcp_without_read_only_metadata() -> None:
    unknown_tool = "mcp__custom-maps__custom_weather"
    template = TaskTemplateDefinition.model_validate(
        {
            "schema_version": 1,
            "template_id": "s99_weather_lookup",
            "template_version": "1.0.0",
            "title": "Weather lookup fixture",
            "description": "Task compiler safety contract fixture.",
            "category": "test",
            "required_capabilities": [unknown_tool],
            "input_schema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Weather location.",
                    }
                },
                "additionalProperties": False,
            },
            "output_requirement": "Return the tool result.",
            "steps": [
                {
                    "step_key": "weather",
                    "title": "Get weather",
                    "kind": "tool",
                    "required": True,
                    "editable": False,
                    "capability_key": unknown_tool,
                    "config": {
                        "type": "tool",
                        "arguments": {"query": "Shanghai"},
                    },
                    "output_requirement": "Return weather data.",
                    "failure_handling": "Stop if the lookup fails.",
                    "sensitive_information": "No sensitive data.",
                }
            ],
        }
    )

    try:
        TaskTemplateCompiler().compile_nodes(
            template,
            [
                TaskToolSchema(
                    name=unknown_tool,
                    is_mcp=True,
                    is_read_only=False,
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                )
            ],
        )
    except TemplateCompileError as error:
        assert error.code == "TEMPLATE_CAPABILITY_UNSAFE"
        assert error.blocked_capabilities == (unknown_tool,)
    else:  # pragma: no cover - fail with a focused diagnostic
        raise AssertionError("An unknown MCP tool must remain blocked.")


class _PermissionTool:
    name = "fixture.read"
    is_read_only = True
    is_external_tool = False
    is_state_injected = False

    def __init__(self) -> None:
        self.executions = 0

    async def check_read_only(self, arguments: dict[str, Any]) -> bool:
        del arguments
        return True

    async def __call__(self, **_: Any) -> None:
        self.executions += 1


class _RuntimeMCPTool(_PermissionTool):
    is_mcp = True
    is_read_only = False
    is_external_tool = False
    is_state_injected = False
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name

    async def check_read_only(self, arguments: dict[str, Any]) -> bool:
        del arguments
        return False

    async def check_permissions(self, *_: Any, **__: Any) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="MCP use requires an explicit Task permission rule.",
        )

    async def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> list[PermissionRule]:
        del tool_input
        return []

    async def __call__(self, **_: Any) -> str:
        self.executions += 1
        return "weather result"


class _PermissionToolkit:
    def __init__(self, tool: _PermissionTool) -> None:
        self.tool = tool

    async def get_tool(self, name: str) -> _PermissionTool | None:
        return self.tool if name == self.tool.name else None


class _PermissionProbeExecutor(AgentScopeTaskExecutor):
    def __init__(self, tool: _PermissionTool, context: PermissionContext) -> None:
        self.tool = tool
        self.permission_context = context

    @asynccontextmanager
    async def _mcp_scope_for(self, context: Any, tool_names: Any = None):
        del context, tool_names
        yield

    async def _build_toolkit(self, context: Any) -> _PermissionToolkit:
        del context
        return _PermissionToolkit(self.tool)

    async def _get_session(self, context: Any) -> Any:
        del context
        return type(
            "Session",
            (),
            {"state": type("State", (), {"permission_context": self.permission_context})()},
        )()


def test_agent_scope_preflight_uses_permission_engine_without_tool_call() -> None:
    async def scenario() -> None:
        tool = _PermissionTool()
        allowed = _PermissionProbeExecutor(tool, PermissionContext())
        status, _ = await allowed.preflight_tool_permission(
            tool_name=tool.name,
            arguments={"query": "fixture"},
            context=object(),  # type: ignore[arg-type]
        )
        assert status == "allowed"

        denied_context = PermissionContext(
            deny_rules={
                tool.name: [
                    PermissionRule(
                        tool_name=tool.name,
                        rule_content="",
                        behavior=PermissionBehavior.DENY,
                        source="task-test",
                    )
                ]
            }
        )
        denied = _PermissionProbeExecutor(tool, denied_context)
        denied_status, _ = await denied.preflight_tool_permission(
            tool_name=tool.name,
            arguments={"query": "fixture"},
            context=object(),  # type: ignore[arg-type]
        )
        assert denied_status == "denied"
        assert tool.executions == 0

    asyncio.run(scenario())


def test_runtime_mcp_weather_contract_calls_after_permission_recheck_only() -> None:
    async def scenario() -> None:
        tool = _RuntimeMCPTool(AMAP_WEATHER_TOOL)
        allowed_context = PermissionContext(
            allow_rules={
                tool.name: [
                    PermissionRule(
                        tool_name=tool.name,
                        rule_content="",
                        behavior=PermissionBehavior.ALLOW,
                        source="task-test",
                    )
                ]
            }
        )
        executor = _PermissionProbeExecutor(tool, allowed_context)
        node = TaskNode(
            name="Get weather",
            type=StepType.TOOL,
            config=ToolStepConfig(
                tool_name=tool.name,
                arguments={"query": "Shanghai"},
            ),
            capability_key=tool.name,
        )
        context = ExecutionContext(
            user_id="owner",
            task_id="task-weather",
            run_id="run-weather",
            task_revision=1,
        )

        result = await executor.execute_node(
            node=node,
            previous_output="",
            context=context,
        )
        assert result.text == '["weather result"]'
        assert result.metadata["task_security_review_checks"][0]["status"] == (
            "passed"
        )
        assert tool.executions == 1

        denied_tool = _RuntimeMCPTool(AMAP_WEATHER_TOOL)
        denied_executor = _PermissionProbeExecutor(
            denied_tool,
            PermissionContext(),
        )
        try:
            await denied_executor.execute_node(
                node=node,
                previous_output="",
                context=context,
            )
        except RuntimeError as error:
            assert "Permission" in str(error)
        else:  # pragma: no cover - fail with a focused diagnostic
            raise AssertionError("Runtime permission denial must be enforced.")
        assert denied_tool.executions == 0

    asyncio.run(scenario())


def test_runtime_unknown_or_write_mcp_cannot_be_called_even_if_allowed() -> None:
    async def scenario() -> None:
        context = ExecutionContext(
            user_id="owner",
            task_id="task-mcp",
            run_id="run-mcp",
            task_revision=1,
        )
        for tool_name, capability_key, read_only_metadata, hint in (
            ("mcp__custom__unknown_tool", "mcp__custom__unknown_tool", False, None),
            ("mcp__notify__send_message", "notification.send", True, None),
            (AMAP_WEATHER_TOOL, AMAP_WEATHER_TOOL, False, False),
        ):
            tool = _RuntimeMCPTool(tool_name)
            tool.is_read_only = read_only_metadata
            if hint is not None:
                tool._tool = SimpleNamespace(
                    annotations=SimpleNamespace(readOnlyHint=hint)
                )
            permission_context = PermissionContext(
                allow_rules={
                    tool.name: [
                        PermissionRule(
                            tool_name=tool.name,
                            rule_content="",
                            behavior=PermissionBehavior.ALLOW,
                            source="task-test",
                        )
                    ]
                }
            )
            executor = _PermissionProbeExecutor(tool, permission_context)
            node = TaskNode(
                name="MCP operation",
                type=StepType.TOOL,
                config=ToolStepConfig(
                    tool_name=tool.name,
                    arguments={"query": "payload"},
                ),
                capability_key=capability_key,
            )
            try:
                await executor.execute_node(
                    node=node,
                    previous_output="",
                    context=context,
                )
            except RuntimeError as error:
                assert "confirmed Task read-only contract" in str(error)
            else:  # pragma: no cover - fail with a focused diagnostic
                raise AssertionError(f"Unsafe MCP tool was not blocked: {tool_name}")
            assert tool.executions == 0

    asyncio.run(scenario())


class _BadJsonToolExecutor(_PreflightOnlyExecutor):
    async def execute_node(
        self,
        *,
        node: TaskNode,
        previous_output: str,
        context: Any,
    ) -> StepExecutionResult:
        del node, previous_output, context
        self.execution_calls += 1
        return StepExecutionResult(
            text="not-json",
            result={"tool": "fixture.read", "chunks": [{"text": "not-json"}]},
        )


def test_tool_schema_failure_is_persisted_and_stops_run() -> None:
    async def scenario() -> None:
        store = TaskStore()
        executor = _BadJsonToolExecutor()
        service = TaskService(store, agentscope_executor=executor)  # type: ignore[arg-type]
        task = _plain_tool_task({"query": "static query"})
        task.nodes[0].output_schema = {
            "type": "object",
            "required": ["data"],
            "properties": {"data": {"type": "string"}},
            "additionalProperties": False,
        }
        task.nodes[0].max_attempts = 1
        await store.save_task(task)

        await service.create_run(
            task.user_id,
            task.id,
            TaskRunRequest(input="fixture input"),
        )
        run = None
        for _ in range(100):
            run = (await service.list_runs(task.user_id, task.id))[0]
            if run.status in {RunStatus.FAILED, RunStatus.SUCCEEDED}:
                break
            await asyncio.sleep(0.02)

        assert run is not None and run.status is RunStatus.FAILED
        node_run = run.node_runs[0]
        assert node_run.status is NodeRunStatus.FAILED
        assert len(node_run.security_reviews) == 1
        assert node_run.security_reviews[0]["source"] == "task_preflight"
        assert node_run.security_reviews[0]["step_id"] == node_run.node_id
        assert node_run.security_reviews[0]["permission_status"] == "allowed"
        assert node_run.result == {
            "tool": "fixture.read",
            "chunks": [{"text": "not-json"}],
        }
        assert node_run.metadata["output_schema_validation"]["status"] == "failed"
        assert node_run.metadata["attempts"] == 1
        assert node_run.metadata["verifications"][0]["verifier"] == (
            "task-output-contract"
        )
        assert "合法 JSON" in (node_run.error or "")
        assert run.final_output == ""
        assert executor.execution_calls == 1

    from examples.agent_service.task._models import TaskRunRequest

    asyncio.run(scenario())


def test_history_passes_contract_tool_json_to_later_agents() -> None:
    result = _lookup_result()
    node = TaskNode(
        id="lookup-node",
        name="查询候选专家",
        type=StepType.TOOL,
        capability_key="expert.lookup",
        config=ToolStepConfig(
            tool_name="expert.lookup",
            arguments={"query": "{{previous_output}}"},
        ),
        order=0,
        output_schema=capability_contract("expert.lookup").output_schema,
    )
    run = TaskRunRecord(
        id="fixture-run",
        task_id="fixture-task",
        user_id="fixture-owner",
        task_revision=1,
        nodes=[node],
        node_runs=[
            NodeRunRecord(
                node_id=node.id,
                name=node.name,
                prompt="",
                type=StepType.TOOL,
                order=0,
                status=NodeRunStatus.SUCCEEDED,
                output=json.dumps(result, ensure_ascii=False),
                result={"tool": "expert.lookup", "chunks": [result]},
            )
        ],
        status=RunStatus.RUNNING,
    )
    history = TaskService._execution_history(run)
    assert history[0]["structured_result"] == result
    formatted = _format_execution_history(history)
    assert "fixture-expert-1" in formatted
    assert '"structured_result"' in formatted
