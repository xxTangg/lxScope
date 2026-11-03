"""Task template history and current-version regression checks."""

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from examples.agent_service.task._capability_contracts import capability_contract
from examples.agent_service.task._models import (
    TaskRecord,
    TaskRunRecord,
    TaskToolSchema,
)
from examples.agent_service.task._output_validation import describe_output_schema
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
        input_schema=(capability_contract(name).input_schema or {}),
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
S03_V3_INPUTS = {
    **S03_INPUTS,
    "expert_records_json": "[{\"expert_id\":\"demo-1\",\"name\":\"演示记录\",\"professional_fields\":[\"工程\"],\"conflict_status\":\"unknown\"}]",
}
CURRENT_VERSIONS = {
    "s01_project_init": "3.0.0",
    "s02_project_planning": "5.1.0",
    "s03_expert_matching": "3.0.0",
    "s04_meeting_preparation": "3.0.0",
    "s05_meeting_minutes": "3.0.0",
    "s06_opinion_processing": "3.0.0",
    "s07_report_generation": "5.0.0",
    "s08_archive_transfer": "3.0.0",
    "s09_expense_settlement": "3.0.0",
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
    assert {
        template.template_id: template.template_version
        for template in templates
    } == CURRENT_VERSIONS
    assert templates[2].template_id == S03_ID


def test_s07_v5_uses_ai_acceptance_and_keeps_v4_history() -> None:
    registry = TemplateRegistry()
    current = registry.get_current_template("s07_report_generation")
    assert current is not None
    assert current.template_version == "5.0.0"

    for version in ("1.0.0", "2.0.0", "3.0.0"):
        historical = registry.get_template_version(
            "s07_report_generation", version
        )
        assert historical is not None
        assert historical.template_version == version
        assert all(step.output_contract is None for step in historical.steps)

    draft = next(
        step for step in current.steps if step.step_key == "draft_report_chapters"
    )
    assert draft.output_contract is None
    assert "task-citations" in draft.config.prompt
    assert "task-claim-evidence" not in draft.config.prompt
    assert "report.citation_check" not in draft.config.prompt
    assert "task-verifier" in draft.output_requirement
    assert "report.citation_check" not in current.required_capabilities
    assert not any(
        step.capability_key == "report.citation_check" for step in current.steps
    )

    compiled = TaskTemplateCompiler().compile_nodes(
        current,
        [],
        require_tool_bindings=False,
    )
    draft_node_id = compiled.step_bindings["draft_report_chapters"]
    compiled_draft = next(
        node for node in compiled.nodes if node.id == draft_node_id
    )
    assert compiled_draft.effective_output_contract is None

    historical_v4 = registry.get_template_version("s07_report_generation", "4.0.0")
    assert historical_v4 is not None
    historical_draft = next(
        step
        for step in historical_v4.steps
        if step.step_key == "draft_report_chapters"
    )
    assert historical_draft.output_contract is not None
    assert historical_draft.output_contract.validation_mode == "citations"


def test_s02_v3_history_and_v5_lightweight_default_preserve_contracts() -> None:
    registry = TemplateRegistry()
    current = registry.get_current_template("s02_project_planning")
    assert current is not None
    assert current.template_version == "5.1.0"
    assert len(current.steps) == 5

    v1 = registry.get_template_version("s02_project_planning", "1.0.0")
    v2 = registry.get_template_version("s02_project_planning", "2.0.0")
    v3 = registry.get_template_version("s02_project_planning", "3.0.0")
    v3_1 = registry.get_template_version("s02_project_planning", "3.1.0")
    v3_2 = registry.get_template_version("s02_project_planning", "3.2.0")
    v4 = registry.get_template_version("s02_project_planning", "4.0.0")
    v4_1 = registry.get_template_version("s02_project_planning", "4.1.0")
    assert len(current.steps) <= 8
    assert (
        v1 is not None
        and v2 is not None
        and v3 is not None
        and v3_1 is not None
        and v3_2 is not None
        and v4 is not None
        and v4_1 is not None
    )
    assert all(step.output_contract is None for step in v1.steps)
    assert all(step.output_contract is None for step in v2.steps)
    assert v3.template_version == "3.0.0"
    assert v3_1.template_version == "3.1.0"
    assert v3_2.template_version == "3.2.0"
    assert v4.template_version == "4.0.0"
    assert v4_1.template_version == "4.1.0"
    assert all(step.output_contract is None for step in current.steps)
    assert current.steps[-1].artifact is not None
    assert current.steps[-1].artifact.format == "docx"
    assert "不要输出 Claim、Occurrence" in current.steps[-1].config.prompt
    assert "尚未完成正式核验" in current.steps[-1].output_requirement
    assert v4.steps[-1].output_contract is not None
    assert v4_1.steps[-1].output_contract is not None
    assert v4.steps[-1].output_contract.contract_id == "task.s02.planning.report.v4"
    assert v4_1.steps[-1].output_contract.contract_id == "task.s02.planning.report.v4.1"
    v4_candidate_schema = v4.steps[-1].output_contract.json_schema["properties"]["chapters"]["items"]["properties"]["items"]["items"]["oneOf"][0]["properties"]["source_candidates"]["items"]
    assert v4_candidate_schema == {"type": "string", "minLength": 1, "maxLength": 2000}
    assert set(
        v4_1.steps[-1].output_contract.json_schema["properties"]["chapters"]
        ["items"]["properties"]["items"]["items"]["properties"]
        ["source_candidates"]["items"]["properties"]
    ) == {"quote", "supports"}
    assert "supports 必须是当前 item.text 中唯一出现的连续逐字子串" in (
        v4_1.steps[-1].config.prompt
    )
    assert "unverified 项的 source_quotes 和 rule_ids 必须为 []" not in (
        v3.steps[-1].config.prompt
    )
    assert v1.steps[-1].artifact is None
    assert v2.steps[-1].artifact is not None
    assert v2.steps[-1].artifact.format == "docx"

    final_step = v3_2.steps[-1]
    historical_v3_output = v3.steps[-1]
    assert final_step.step_key == "produce_planning_output"
    assert final_step.artifact is not None
    assert final_step.artifact.format == "docx"
    assert final_step.output_contract is not None
    assert final_step.output_contract.validation_mode == "json_schema"
    assert final_step.output_contract.downstream_binding == "validated_result"
    assert final_step.output_contract.output_type == "structured_json"
    assert final_step.output_contract.json_schema is not None
    assert historical_v3_output.output_contract is not None
    assert v3_1.steps[-1].output_contract is not None
    assert final_step.output_contract.json_schema != v3_1.steps[-1].output_contract.json_schema
    assert v3_1.steps[-1].output_contract.json_schema == historical_v3_output.output_contract.json_schema
    assert "不得含 unverified_status" in final_step.config.prompt
    assert "普通条目不得包含待处理字段" in final_step.output_requirement
    assert "只有 unverified 可以含" in final_step.config.prompt
    assert "来源语义需要人工复核不等于 unverified" in final_step.config.prompt
    assert "template_limitation" in final_step.config.prompt
    assert final_step.output_contract.json_schema["properties"][
        "chapters"
    ]["minItems"] == 8
    assert final_step.output_contract.json_schema["properties"][
        "chapters"
    ]["maxItems"] == 8
    item_schema = final_step.output_contract.json_schema["properties"][
        "chapters"
    ]["items"]["properties"]["items"]["items"]
    assert len(item_schema["oneOf"]) == 4
    assert item_schema["oneOf"][3]["properties"]["source_quotes"]["maxItems"] == 0
    assert "unverified_status" not in item_schema["oneOf"][0]["properties"]
    assert item_schema["oneOf"][2]["properties"]["source_quotes"]["minItems"] == 1

    compiled = TaskTemplateCompiler().compile_nodes(
        v3_2,
        [],
        require_tool_bindings=False,
    )
    final_node = next(
        node
        for node in compiled.nodes
        if node.id == compiled.step_bindings["produce_planning_output"]
    )
    assert final_node.output_contract is not None
    assert final_node.effective_output_contract == final_step.output_contract
    assert final_node.effective_output_contract.json_schema is not None
    assert final_node.prompt.count(
        describe_output_schema(final_node.effective_output_contract.json_schema)
    ) == 1


def test_all_v1_templates_remain_readable_and_v2_templates_add_safe_artifacts() -> None:
    registry = TemplateRegistry()
    current = registry.list_templates()

    assert len(current) == 9
    for template in current:
        historical = registry.get_template_version(
            template.template_id,
            "1.0.0",
        )
        assert historical is not None
        assert historical.template_version == "1.0.0"
        assert all(step.artifact is None for step in historical.steps)
        assert template.template_version == CURRENT_VERSIONS[template.template_id]

        final_step = template.steps[-1]
        assert final_step.artifact is not None
        assert final_step.kind in {"agent", "tool"}

    s03_v1 = _s03_payload()
    s03_v2 = json.loads(
        (TEMPLATE_DIR / "s03_expert_matching.v2.json").read_text(
            encoding="utf-8"
        )
    )
    s03_v1["template_version"] = "2.0.0"
    s03_v2["steps"][-1].pop("artifact", None)
    assert s03_v1 == s03_v2
    assert len(s03_v2["steps"]) == 8
    assert s03_v2["steps"][6]["step_key"] == "collect_human_review"
    assert s03_v2["steps"][6]["kind"] == "input"

    s02_v1 = json.loads(
        (TEMPLATE_DIR / "s02_project_planning.v1.json").read_text(
            encoding="utf-8"
        )
    )
    s02_v2 = json.loads(
        (TEMPLATE_DIR / "s02_project_planning.v2.json").read_text(
            encoding="utf-8"
        )
    )
    s02_v1["template_version"] = "2.0.0"
    s02_v2["steps"][-1].pop("artifact", None)
    assert s02_v1 == s02_v2

    safe_current = {
        template.template_id: template for template in current
    }
    for template_id, side_effect_capability in (
        ("s04_meeting_preparation", "notification.send"),
        ("s08_archive_transfer", "archive.write"),
        ("s09_expense_settlement", "expense.erp_submit"),
    ):
        template = safe_current[template_id]
        assert side_effect_capability not in template.required_capabilities
        assert side_effect_capability in {
            item.capability_key for item in template.unsupported_capabilities
        }


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
            template_version="3.0.0",
            inputs=S03_V3_INPUTS,
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
    assert task.source_template_version == "3.0.0"


def test_explicit_historical_s02_version_keeps_its_original_contract() -> None:
    async def instantiate() -> TaskRecord:
        service = TaskService(TaskStore(), planner=_NoPlanner())  # type: ignore[arg-type]
        return await TaskTemplateService(
            TemplateRegistry(),
            service,
        ).instantiate(
            "history-owner",
            "s02_project_planning",
            TemplateInstantiateRequest(
                template_version="2.0.0",
                inputs={
                    "project_name": "历史版本兼容测试",
                    "project_brief": "验证 v2 仍按其既有协议实例化。",
                    "professional_fields": ["工程"],
                    "source_material_ids": [],
                },
            ),
        )

    task = asyncio.run(instantiate())

    assert task.source_template_id == "s02_project_planning"
    assert task.source_template_version == "2.0.0"
    assert all(node.output_contract is None for node in task.nodes)
    assert task.nodes[-1].artifact is not None
    assert task.nodes[-1].artifact.format == "docx"


def test_template_copy_edits_do_not_change_source_or_run_snapshot() -> None:
    source_path = TEMPLATE_DIR / "s03_expert_matching.v2.json"
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
                template_version="3.0.0",
                inputs=S03_V3_INPUTS,
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
