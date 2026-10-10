"""Task template history and current-version regression checks."""

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from examples.agent_service.task._models import (
    TaskRecord,
    TaskRunRecord,
    TaskToolSchema,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_compiler import TaskTemplateCompiler
from examples.agent_service.task._template_models import (
    TaskTemplateDefinition,
    TemplateInstantiateRequest,
)
from examples.agent_service.task._template_registry import TemplateRegistry
from examples.agent_service.task._template_service import TaskTemplateService
from examples.agent_service.task._template_validation import (
    TaskTemplateValidator,
)


TASK_DIR = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "agent_service"
    / "task"
)
TEMPLATE_DIR = TASK_DIR / "templates"
S03_ID = "s03_expert_matching"
S03_FILE = "s03_expert_matching.v1.json"
S03_TOOLS = [
    TaskToolSchema(
        name=name,
        input_schema={"type": "object"},
        is_read_only=True,
    )
    for name in (
        "expert.lookup",
        "expert.conflict_check",
        "expert.rank",
    )
]
S03_INPUTS = {
    "project_name": "测试项目",
    "project_brief": "评审项目背景",
    "professional_fields": ["工程"],
}


class _NoPlanner:
    """Fail the test if template instantiation calls the Planner."""

    async def plan(self, **_: Any) -> Any:
        raise AssertionError("Template instantiation must not invoke Planner.")


class _TemplateExecutor:
    def __init__(self) -> None:
        self.planner = _NoPlanner()

    async def list_tools(self, _context: Any) -> list[TaskToolSchema]:
        return S03_TOOLS


def _s03_payload() -> dict[str, Any]:
    return json.loads((TEMPLATE_DIR / S03_FILE).read_text(encoding="utf-8"))


def _write_template(
    root: Path,
    file_name: str,
    payload: dict[str, Any],
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / file_name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_default_registry_lists_only_nine_current_scenarios() -> None:
    templates = TemplateRegistry().list_templates()

    assert len(templates) == 9
    assert len({template.template_id for template in templates}) == 9
    assert all(template.template_version == "1.0.0" for template in templates)
    assert templates[2].template_id == S03_ID


def test_registry_resolves_exact_history_and_keeps_gallery_current(
    tmp_path: Path,
) -> None:
    v1 = _s03_payload()
    v2 = copy.deepcopy(v1)
    v2["template_version"] = "2.0.0"
    v2["title"] = "专家智能匹配 v2（测试清单）"
    _write_template(tmp_path, S03_FILE, v1)
    _write_template(tmp_path, "s03_expert_matching.v2.json", v2)
    registry = TemplateRegistry(
        tmp_path,
        version_files=(
            (S03_ID, "1.0.0", S03_FILE),
            (S03_ID, "2.0.0", "s03_expert_matching.v2.json"),
        ),
        current_versions=((S03_ID, "2.0.0"),),
    )

    assert len(registry.list_templates()) == 1
    current = registry.get_current_template(S03_ID)
    historical = registry.get_template_version(S03_ID, "1.0.0")
    resolved_current = registry.get_template_version(S03_ID, "2.0.0")
    assert current is not None and current.template_version == "2.0.0"
    assert historical is not None and historical.title == v1["title"]
    assert (
        resolved_current is not None
        and resolved_current.title == v2["title"]
    )
    assert registry.get_template_version(S03_ID, "9.9.9") is None
    assert registry.get_template_version("unknown_scenario", "1.0.0") is None


def test_historical_task_validates_against_its_exact_template_version(
    tmp_path: Path,
) -> None:
    v1 = _s03_payload()
    v2 = copy.deepcopy(v1)
    v2["template_version"] = "2.0.0"
    v2["title"] = "专家智能匹配 v2（测试清单）"
    v2["steps"][1]["config"]["prompt"] = "不同版本的项目画像提示。"
    v2_file = "s03_expert_matching.v2.json"
    _write_template(tmp_path, S03_FILE, v1)
    _write_template(tmp_path, v2_file, v2)
    registry = TemplateRegistry(
        tmp_path,
        version_files=(
            (S03_ID, "1.0.0", S03_FILE),
            (S03_ID, "2.0.0", v2_file),
        ),
        current_versions=((S03_ID, "2.0.0"),),
    )
    template = registry.get_template_version(S03_ID, "1.0.0")
    assert template is not None
    compiler = TaskTemplateCompiler()
    compiled = compiler.compile_nodes(template, S03_TOOLS)
    task = TaskRecord(
        user_id="history-owner",
        nodes=compiled.nodes,
        source_template_id=S03_ID,
        source_template_version="1.0.0",
        template_step_bindings=compiled.step_bindings,
        template_inputs=S03_INPUTS,
    )

    result = TaskTemplateValidator(registry, compiler).validate(
        task,
        task.nodes,
        tool_schemas=S03_TOOLS,
    )
    unknown_version = task.model_copy(
        update={"source_template_version": "9.9.9"},
        deep=True,
    )
    unknown_result = TaskTemplateValidator(registry, compiler).validate(
        unknown_version,
        unknown_version.nodes,
        tool_schemas=S03_TOOLS,
    )

    assert result.valid is True
    assert unknown_result.valid is False
    current = registry.get_current_template(S03_ID)
    assert current is not None and current.template_version == "2.0.0"


def test_registry_rejects_undeclared_json_even_when_history_is_configured(
    tmp_path: Path,
) -> None:
    _write_template(tmp_path, S03_FILE, _s03_payload())
    (tmp_path / "unregistered.json").write_text("{}", encoding="utf-8")
    registry = TemplateRegistry(
        tmp_path,
        version_files=((S03_ID, "1.0.0", S03_FILE),),
        current_versions=((S03_ID, "1.0.0"),),
    )

    with pytest.raises(ValueError, match="unexpected_template_file"):
        registry.list_templates()


def test_template_instantiation_uses_current_version_without_planner() -> None:
    async def instantiate() -> TaskRecord:
        executor = _TemplateExecutor()
        service = TaskService(
            TaskStore(),
            agentscope_executor=executor,  # type: ignore[arg-type]
            planner=executor.planner,  # type: ignore[arg-type]
        )
        template_service = TaskTemplateService(TemplateRegistry(), service)
        request = TemplateInstantiateRequest(
            template_version="1.0.0",
            inputs=S03_INPUTS,
            source_context={
                "agent_id": "history-test-agent",
                "session_id": "history-test-session",
            },
        )
        return await template_service.instantiate(
            "history-owner",
            S03_ID,
            request,
        )

    task = asyncio.run(instantiate())

    assert task.source_template_id == S03_ID
    assert task.source_template_version == "1.0.0"


def test_template_copy_edits_do_not_change_source_or_run_snapshot() -> None:
    source_path = TEMPLATE_DIR / S03_FILE
    source_before = source_path.read_bytes()

    async def instantiate() -> TaskRecord:
        executor = _TemplateExecutor()
        service = TaskService(
            TaskStore(),
            agentscope_executor=executor,  # type: ignore[arg-type]
            planner=executor.planner,  # type: ignore[arg-type]
        )
        return await TaskTemplateService(
            TemplateRegistry(),
            service,
        ).instantiate(
            "history-owner",
            S03_ID,
            TemplateInstantiateRequest(
                template_version="1.0.0",
                inputs=S03_INPUTS,
                source_context={
                    "agent_id": "history-test-agent",
                    "session_id": "history-test-session",
                },
            ),
        )

    task = asyncio.run(instantiate())
    task_node_snapshots = [node.model_copy(deep=True) for node in task.nodes]
    run = TaskRunRecord(
        task_id=task.id,
        user_id=task.user_id,
        task_revision=task.revision,
        nodes=task_node_snapshots,
        node_runs=[],
    )
    original_run_name = run.nodes[0].name
    task.nodes[0].name = "用户副本改名"

    source_again = TemplateRegistry().get_current_template(S03_ID)

    assert source_again is not None
    assert source_again.title == "专家智能匹配"
    assert run.nodes[0].name == original_run_name
    assert source_path.read_bytes() == source_before


def test_template_definition_rejects_more_than_eight_steps() -> None:
    payload = _s03_payload()
    payload["steps"].append(copy.deepcopy(payload["steps"][-1]))
    payload["steps"][-1]["step_key"] = "ninth_test_step"

    with pytest.raises(ValidationError):
        TaskTemplateDefinition.model_validate(payload)
