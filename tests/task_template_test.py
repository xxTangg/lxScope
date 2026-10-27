"""Static checks for the P1 Task scenario template data."""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from examples.agent_service.task._capability_contracts import capability_contract
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    StepVerdict,
    TaskRecord,
    TaskToolSchema,
)
from examples.agent_service.task._router import (
    _get_template_registry,
    task_router,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_compiler import (
    TaskTemplateCompiler,
    TemplateCompileError,
)
from examples.agent_service.task._template_models import (
    TaskTemplateDefinition,
    TemplateInstantiateRequest,
)
from examples.agent_service.task._template_registry import (
    TemplateRegistry,
    TemplateRegistryError,
)
from examples.agent_service.task._template_validation import (
    TaskTemplateValidator,
)


TEMPLATE_DIR = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "agent_service"
    / "task"
    / "templates"
)
EXPECTED_IDS = {
    "s01_project_init",
    "s02_project_planning",
    "s03_expert_matching",
    "s04_meeting_preparation",
    "s05_meeting_minutes",
    "s06_opinion_processing",
    "s07_report_generation",
    "s08_archive_transfer",
    "s09_expense_settlement",
}
SUPPORTED_KINDS = {"agent", "tool", "python", "input"}
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
VARIABLE_PATTERN = re.compile(r"\{\{([^{}]+)\}\}")
ALLOWED_VARIABLES = re.compile(
    r"^(user_input|previous_output|answers\.[\w.-]+)$"
)
REQUIRED_STEP_FIELDS = {
    "step_key",
    "title",
    "kind",
    "required",
    "editable",
    "capability_key",
    "config",
    "output_requirement",
    "failure_handling",
    "sensitive_information",
}


def _load_templates() -> list[tuple[Path, dict[str, Any]]]:
    """Load all versioned JSON templates without importing Task runtime code."""

    paths = sorted(TEMPLATE_DIR.glob("s*.v1.json"))
    assert len(paths) == 9
    return [
        (
            path,
            json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            ),
        )
        for path in paths
    ]


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate object keys before json.loads can overwrite them."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(key)
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def test_nine_templates_load_with_stable_unique_ids_and_versions() -> None:
    templates = _load_templates()
    ids = [template["template_id"] for _, template in templates]

    assert set(ids) == EXPECTED_IDS
    assert len(ids) == len(set(ids))
    assert [path.name for path, _ in templates] == [
        "s01_project_init.v1.json",
        "s02_project_planning.v1.json",
        "s03_expert_matching.v1.json",
        "s04_meeting_preparation.v1.json",
        "s05_meeting_minutes.v1.json",
        "s06_opinion_processing.v1.json",
        "s07_report_generation.v1.json",
        "s08_archive_transfer.v1.json",
        "s09_expense_settlement.v1.json",
    ]

    for _, template in templates:
        assert template["schema_version"] == 1
        assert VERSION_PATTERN.fullmatch(template["template_version"])
        assert template["template_id"]
        assert template["title"].strip()


def test_templates_have_bounded_steps_and_complete_step_definitions() -> None:
    for _, template in _load_templates():
        steps = template["steps"]
        assert 1 <= len(steps) <= 8
        keys = [step["step_key"] for step in steps]
        assert all(key.strip() for key in keys)
        assert len(keys) == len(set(keys))
        assert any(step["required"] is True for step in steps)

        for step in steps:
            assert REQUIRED_STEP_FIELDS <= step.keys()
            assert step["kind"] in SUPPORTED_KINDS
            assert isinstance(step["required"], bool)
            assert isinstance(step["editable"], bool)
            assert step["config"]["type"] == step["kind"]
            assert step["output_requirement"].strip()
            assert step["failure_handling"].strip()
            assert step["sensitive_information"].strip()
            if step["kind"] == "tool":
                assert isinstance(step["capability_key"], str)
                assert step["capability_key"].strip()
                assert "tool_name" not in step["config"]


def test_templates_declare_inputs_capabilities_and_unsupported_boundaries() -> None:
    for _, template in _load_templates():
        input_schema = template["input_schema"]
        assert input_schema["type"] == "object"
        assert isinstance(input_schema["properties"], dict)
        assert set(input_schema["required"]) <= set(input_schema["properties"])
        assert isinstance(template["required_capabilities"], list)
        assert template["required_capabilities"]
        assert isinstance(template["unsupported_capabilities"], list)
        assert template["output_requirement"].strip()

        declared_capabilities = set(template["required_capabilities"])
        step_capabilities = {
            step["capability_key"]
            for step in template["steps"]
            if step["kind"] == "tool"
        }
        assert step_capabilities <= declared_capabilities

        for unsupported in template["unsupported_capabilities"]:
            assert unsupported["capability_key"].strip()
            assert unsupported["reason"].strip()


def test_template_variables_and_sensitive_material_are_bounded() -> None:
    for _, template in _load_templates():
        text = "\n".join(_walk_strings(template))
        assert "http://" not in text.lower()
        assert "https://" not in text.lower()
        assert not re.search(r"\b(api_key|password|secret)\b", text, re.IGNORECASE)

        for variable in VARIABLE_PATTERN.findall(text):
            assert ALLOWED_VARIABLES.fullmatch(variable)


def _template_client(
    registry: TemplateRegistry | None = None,
    task_service: TaskService | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(task_router)
    if registry is not None:
        app.dependency_overrides[_get_template_registry] = lambda: registry
    if task_service is not None:
        app.state.task_service = task_service
    return TestClient(app)


class _TemplateToolExecutor:
    """Expose only test-scoped Task tool schemas to template compilation."""

    def __init__(self, tools: list[TaskToolSchema]) -> None:
        self.tools = tools
        self.contexts: list[Any] = []
        self.executed_nodes: list[str] = []

    async def list_tools(self, context: Any) -> list[TaskToolSchema]:
        self.contexts.append(context)
        return self.tools

    async def execute_node(
        self,
        *,
        node: Any,
        previous_output: str,
        context: Any,
    ) -> StepExecutionResult:
        del context
        self.executed_nodes.append(node.name)
        return StepExecutionResult(
            text=(
                f"TEST MOCK ONLY — {node.name}; "
                f"previous={previous_output[:80]}"
            ),
            result={"simulation": True},
        )

    async def verify_node(
        self,
        *,
        node: Any,
        output: str,
        context: Any,
    ) -> StepVerdict:
        del node, output, context
        return StepVerdict(passed=True, message="test mock accepted")


class _PlannerMustNotRun:
    """Fail loudly if template instantiation crosses into AI planning."""

    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, **_: Any) -> Any:
        self.calls += 1
        raise AssertionError("Template instantiation must not invoke Planner.")


def _expert_template_tools(
    names: list[str] | None = None,
) -> list[TaskToolSchema]:
    capability_keys = [
        "expert.lookup",
        "expert.conflict_check",
        "expert.rank",
    ]
    tool_names = names or capability_keys
    return [
        TaskToolSchema(
            name=name,
            input_schema=(
                capability_contract(capability_keys[index]).input_schema
                or {}
                if name not in capability_keys
                else capability_contract(name).input_schema or {}
            ),
            is_read_only=True,
        )
        for index, name in enumerate(tool_names)
    ]


def _expert_template_service(
    tools: list[TaskToolSchema],
) -> tuple[TaskStore, TaskService, _PlannerMustNotRun, _TemplateToolExecutor]:
    store = TaskStore()
    planner = _PlannerMustNotRun()
    executor = _TemplateToolExecutor(tools)
    task_service = TaskService(
        store,
        agentscope_executor=executor,  # type: ignore[arg-type]
        planner=planner,  # type: ignore[arg-type]
    )
    return store, task_service, planner, executor


def _expert_template_request() -> dict[str, Any]:
    return {
        "template_version": "3.0.0",
        "inputs": {
            "project_name": "测试项目",
            "project_brief": "核安全领域的工程评审",
            "professional_fields": ["核安全"],
            "required_expert_count": 3,
            "expert_records_json": "[{\"expert_id\":\"demo-1\",\"name\":\"演示候选\",\"professional_fields\":[\"核安全\"],\"conflict_status\":\"unknown\"}]",
        },
        "source_context": {
            "agent_id": "agent-template-test",
            "session_id": "session-template-test",
        },
    }


def _wait_for_run_state(
    client: TestClient,
    run_id: str,
    states: set[str],
) -> dict[str, Any]:
    for _ in range(300):
        response = client.get(
            f"/tasks/runs/{run_id}",
            headers={"X-User-ID": "template-owner"},
        )
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in states:
            return run
        time.sleep(0.01)
    pytest.fail(f"Task run {run_id} did not reach {sorted(states)}")


def test_registry_loads_catalog_in_stable_order_independent_of_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    templates = TemplateRegistry().list_templates()

    assert len(templates) == 9
    assert [template.template_id for template in templates] == [
        "s01_project_init",
        "s02_project_planning",
        "s03_expert_matching",
        "s04_meeting_preparation",
        "s05_meeting_minutes",
        "s06_opinion_processing",
        "s07_report_generation",
        "s08_archive_transfer",
        "s09_expense_settlement",
    ]
    assert all(
        isinstance(template, TaskTemplateDefinition) for template in templates
    )


def test_template_routes_require_identity_and_serve_list_detail_and_404() -> None:
    with _template_client() as client:
        unauthenticated = client.get("/tasks/templates")
        assert unauthenticated.status_code == 422
        empty_identity = client.get(
            "/tasks/templates",
            headers={"X-User-ID": ""},
        )
        assert empty_identity.status_code == 401

        headers = {"X-User-ID": "template-reader"}
        listing = client.get("/tasks/templates", headers=headers)
        assert listing.status_code == 200
        assert [item["template_id"] for item in listing.json()] == [
            "s01_project_init",
            "s02_project_planning",
            "s03_expert_matching",
            "s04_meeting_preparation",
            "s05_meeting_minutes",
            "s06_opinion_processing",
            "s07_report_generation",
            "s08_archive_transfer",
            "s09_expense_settlement",
        ]

        detail = client.get(
            "/tasks/templates/s03_expert_matching",
            headers=headers,
        )
        assert detail.status_code == 200
        assert detail.json()["template_version"] == "3.0.0"

        unknown = client.get(
            "/tasks/templates/unknown_template",
            headers=headers,
        )
        assert unknown.status_code == 404


def test_registry_returns_locatable_missing_and_invalid_file_errors(
    tmp_path: Path,
) -> None:
    registry = TemplateRegistry(tmp_path)

    with pytest.raises(TemplateRegistryError) as missing:
        registry.list_templates()
    assert missing.value.code == "template_file_missing"
    assert missing.value.file_name == "s01_project_init.v3.json"

    invalid_file = tmp_path / "s01_project_init.v3.json"
    invalid_file.write_text("{not-json", encoding="utf-8")
    with pytest.raises(TemplateRegistryError) as invalid_json:
        registry.list_templates()
    assert invalid_json.value.code == "invalid_json"
    assert invalid_json.value.file_name == invalid_file.name
    assert str(tmp_path) not in str(invalid_json.value)


def test_template_api_reports_invalid_catalog_without_exposing_paths(
    tmp_path: Path,
) -> None:
    invalid_file = tmp_path / "s01_project_init.v3.json"
    invalid_file.write_text("{not-json", encoding="utf-8")

    with _template_client(TemplateRegistry(tmp_path)) as client:
        response = client.get(
            "/tasks/templates",
            headers={"X-User-ID": "template-reader"},
        )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "task_template_registry_error"
    assert detail["reason"] == "invalid_json"
    assert detail["file"] == invalid_file.name
    assert str(tmp_path) not in response.text


def test_registry_rejects_invalid_template_schema_and_extra_files(
    tmp_path: Path,
) -> None:
    invalid_file = tmp_path / "s01_project_init.v3.json"
    invalid_file.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")

    with pytest.raises(TemplateRegistryError) as invalid_schema:
        TemplateRegistry(tmp_path).list_templates()
    assert invalid_schema.value.code == "invalid_template_schema"
    assert invalid_schema.value.file_name == invalid_file.name
    assert all("input_value" not in issue for issue in invalid_schema.value.issues)

    invalid_file.write_text("{}", encoding="utf-8")
    (tmp_path / "unexpected.v1.json").write_text("{}", encoding="utf-8")
    with pytest.raises(TemplateRegistryError) as unexpected:
        TemplateRegistry(tmp_path).list_templates()
    assert unexpected.value.code == "unexpected_template_file"
    assert unexpected.value.file_name == "unexpected.v1.json"


def test_template_model_rejects_coercion_and_unrecognized_fields() -> None:
    payload = json.loads(
        (TEMPLATE_DIR / "s01_project_init.v1.json").read_text(
            encoding="utf-8"
        )
    )
    payload["steps"][0]["required"] = "true"

    with pytest.raises(ValidationError):
        TaskTemplateDefinition.model_validate(payload)

    payload["steps"][0]["required"] = True
    payload["unexpected"] = "must be rejected"
    with pytest.raises(ValidationError):
        TaskTemplateDefinition.model_validate(payload)


def test_template_instantiation_copies_without_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    template_before = TemplateRegistry().get_template("s03_expert_matching")
    assert template_before is not None
    stored_template = template_before.model_dump(mode="json")
    store, task_service, planner, executor = _expert_template_service(
        _expert_template_tools()
    )

    with _template_client(task_service=task_service) as client:
        unauthenticated = client.post(
            "/tasks/templates/s03_expert_matching/instantiate",
            json=_expert_template_request(),
        )
        assert unauthenticated.status_code == 422

        first_response = client.post(
            "/tasks/templates/s03_expert_matching/instantiate",
            headers={"X-User-ID": "template-owner"},
            json=_expert_template_request(),
        )
        second_response = client.post(
            "/tasks/templates/s03_expert_matching/instantiate",
            headers={"X-User-ID": "template-owner"},
            json=_expert_template_request(),
        )
        assert first_response.status_code == 201, first_response.text
        assert second_response.status_code == 201, second_response.text

        first = first_response.json()
        second = second_response.json()
        assert first["id"] != second["id"]
        first_node_ids = {node["id"] for node in first["nodes"]}
        second_node_ids = {node["id"] for node in second["nodes"]}
        assert len(first_node_ids) == len(first["nodes"])
        assert len(second_node_ids) == len(second["nodes"])
        assert first_node_ids.isdisjoint(second_node_ids)
        assert [node["order"] for node in first["nodes"]] == list(
            range(len(first["nodes"]))
        )
        assert first["source_template_id"] == "s03_expert_matching"
        assert first["source_template_version"] == "3.0.0"
        assert set(first["template_step_bindings"]) == {
            "collect_matching_requirements",
            "extract_project_profile",
            "lookup_expert_candidates",
            "check_expert_conflicts",
            "rank_expert_candidates",
            "explain_matching_reasons",
            "collect_human_review",
            "produce_expert_shortlist",
        }
        assert "测试项目" in first["goal"]
        assert planner.calls == 0
        assert executor.contexts == []

        forbidden = client.get(
            f"/tasks/{first['id']}",
            headers={"X-User-ID": "another-user"},
        )
        assert forbidden.status_code == 404

        listed = client.get(
            "/tasks/",
            headers={"X-User-ID": "template-owner"},
        )
        assert listed.status_code == 200
        assert {task["id"] for task in listed.json()} == {
            first["id"],
            second["id"],
        }

    assert len(asyncio.run(store.list_tasks("template-owner"))) == 2
    template_after = TemplateRegistry().get_template("s03_expert_matching")
    assert template_after is not None
    assert template_after.model_dump(mode="json") == stored_template


def test_s03_keeps_safety_steps_required_and_profile_optional() -> None:
    template = TemplateRegistry().get_template("s03_expert_matching")
    assert template is not None
    by_key = {step.step_key: step for step in template.steps}

    assert len(template.steps) == 8
    assert by_key["extract_project_profile"].required is False
    assert by_key["check_expert_conflicts"].required is True
    assert by_key["collect_human_review"].required is True
    assert all(
        by_key[key].editable is False
        for key in (
            "lookup_expert_candidates",
            "check_expert_conflicts",
            "rank_expert_candidates",
        )
    )


def test_template_preflight_separates_local_expert_adapter_from_artifact_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    store, task_service, planner, executor = _expert_template_service(
        _expert_template_tools()
    )
    headers = {"X-User-ID": "template-owner"}

    with _template_client(task_service=task_service) as client:
        created = client.post(
            "/tasks/templates/s03_expert_matching/instantiate",
            headers=headers,
            json=_expert_template_request(),
        )
        assert created.status_code == 201, created.text
        task = created.json()
        assert task["template_inputs"]["project_name"] == "测试项目"

        profile_id = task["template_step_bindings"]["extract_project_profile"]
        profile = next(node for node in task["nodes"] if node["id"] == profile_id)
        profile["config"]["prompt"] = "测试用：仅提取明确提供的项目信息。"
        saved = client.patch(
            f"/tasks/{task['id']}",
            headers=headers,
            json={"nodes": task["nodes"]},
        )
        assert saved.status_code == 200, saved.text

        validated = client.post(
            "/tasks/validate",
            headers=headers,
            json={"task_id": task["id"]},
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["valid"] is True

        preflight = client.post(
            f"/tasks/{task['id']}/preflight",
            headers=headers,
            json={"input": "测试运行输入"},
        )
        assert preflight.status_code == 200, preflight.text
        assert preflight.json()["ready"] is False
        checks = {
            check["capability_key"]: check
            for check in preflight.json()["capability_checks"]
        }
        assert {
            "expert.user_data_lookup",
            "expert.user_conflict_check",
            "expert.user_rank",
            "document.render",
        } <= set(checks)
        for capability in (
            "expert.user_data_lookup",
            "expert.user_conflict_check",
            "expert.user_rank",
        ):
            assert checks[capability]["tool_available"] is True
            assert checks[capability]["interface_compatible"] is True
            assert checks[capability]["business_data_verified"] is False
            assert checks[capability]["status"] == "available"
        assert checks["document.render"]["status"] == "unverified"
        assert executor.executed_nodes == []

        foreign_preflight = client.post(
            f"/tasks/{task['id']}/preflight",
            headers={"X-User-ID": "other-user"},
            json={},
        )
        assert foreign_preflight.status_code == 404

        rejected = client.post(
            f"/tasks/{task['id']}/runs",
            headers=headers,
            json={"input": "测试运行输入"},
        )
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["detail"]["code"] == "TASK_PREFLIGHT_BLOCKED"
        assert asyncio.run(store.list_runs("template-owner", task["id"])) == []
        assert executor.executed_nodes == []


def test_optional_template_step_can_be_deleted_but_conflict_check_cannot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    store, task_service, _, _ = _expert_template_service(
        _expert_template_tools()
    )
    headers = {"X-User-ID": "template-owner"}

    with _template_client(task_service=task_service) as client:
        created = client.post(
            "/tasks/templates/s03_expert_matching/instantiate",
            headers=headers,
            json=_expert_template_request(),
        )
        assert created.status_code == 201, created.text
        task = created.json()
        optional_id = task["template_step_bindings"]["extract_project_profile"]
        without_optional = [
            node for node in task["nodes"] if node["id"] != optional_id
        ]
        for index, node in enumerate(without_optional):
            node["order"] = index
        saved_optional = client.patch(
            f"/tasks/{task['id']}",
            headers=headers,
            json={"nodes": without_optional},
        )
        assert saved_optional.status_code == 200, saved_optional.text
        assert len(saved_optional.json()["nodes"]) == 7

        conflict_id = task["template_step_bindings"]["check_expert_conflicts"]
        edited_locked = [
            dict(node) for node in saved_optional.json()["nodes"]
        ]
        conflict_node = next(
            node for node in edited_locked if node["id"] == conflict_id
        )
        conflict_node["config"] = {
            **conflict_node["config"],
            "tool_name": "expert.rank",
        }
        changed_tool = client.patch(
            f"/tasks/{task['id']}",
            headers=headers,
            json={"nodes": edited_locked},
        )
        assert changed_tool.status_code == 422
        assert any(
            "由模板锁定" in issue["message"]
            for issue in changed_tool.json()["detail"]["issues"]
        )

        without_conflict = [
            node for node in saved_optional.json()["nodes"]
            if node["id"] != conflict_id
        ]
        for index, node in enumerate(without_conflict):
            node["order"] = index
        rejected = client.patch(
            f"/tasks/{task['id']}",
            headers=headers,
            json={"nodes": without_conflict},
        )
        assert rejected.status_code == 422
        assert any(
            "强制步骤“整理回避声明并标明未做正式核验”不能删除"
            in issue["message"]
            for issue in rejected.json()["detail"]["issues"]
        )
        unchanged = client.get(f"/tasks/{task['id']}", headers=headers)
        assert unchanged.json()["nodes"] == saved_optional.json()["nodes"]
        assert asyncio.run(store.list_runs("template-owner", task["id"])) == []


def test_template_rejects_wrong_required_order_before_save_or_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    store, task_service, _, executor = _expert_template_service(
        _expert_template_tools()
    )
    headers = {"X-User-ID": "template-owner"}

    with _template_client(task_service=task_service) as client:
        created = client.post(
            "/tasks/templates/s03_expert_matching/instantiate",
            headers=headers,
            json=_expert_template_request(),
        )
        task = created.json()
        before_rejected = client.get(
            f"/tasks/{task['id']}",
            headers=headers,
        ).json()
        reordered = list(task["nodes"])
        first_tool = task["template_step_bindings"]["lookup_expert_candidates"]
        conflict_tool = task["template_step_bindings"]["check_expert_conflicts"]
        first_index = next(
            i for i, node in enumerate(reordered) if node["id"] == first_tool
        )
        conflict_index = next(
            i
            for i, node in enumerate(reordered)
            if node["id"] == conflict_tool
        )
        reordered[first_index], reordered[conflict_index] = (
            reordered[conflict_index],
            reordered[first_index],
        )
        for index, node in enumerate(reordered):
            node["order"] = index

        validation = client.post(
            "/tasks/validate",
            headers=headers,
            json={"task_id": task["id"], "nodes": reordered},
        )
        assert validation.status_code == 200
        assert validation.json()["valid"] is False
        assert any(
            "前置顺序" in issue["message"]
            for issue in validation.json()["issues"]
        )

        rejected = client.patch(
            f"/tasks/{task['id']}",
            headers=headers,
            json={"nodes": reordered},
        )
        assert rejected.status_code == 422
        assert any(
            "前置顺序" in issue["message"]
            for issue in rejected.json()["detail"]["issues"]
        )
        # The invalid edit was never saved or queued as a Run.
        after_rejected = client.get(
            f"/tasks/{task['id']}",
            headers=headers,
        ).json()
        assert after_rejected["nodes"] == before_rejected["nodes"]
        assert executor.executed_nodes == []
        assert asyncio.run(store.list_runs("template-owner", task["id"])) == []


def test_template_run_missing_input_and_artifact_writer_are_rejected_before_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    store, task_service, _, executor = _expert_template_service(
        _expert_template_tools()
    )
    headers = {"X-User-ID": "template-owner"}

    with _template_client(task_service=task_service) as client:
        created = client.post(
            "/tasks/templates/s03_expert_matching/instantiate",
            headers=headers,
            json=_expert_template_request(),
        )
        assert created.status_code == 201
        task = created.json()

        empty_goal = client.patch(
            f"/tasks/{task['id']}",
            headers=headers,
            json={"goal": ""},
        )
        assert empty_goal.status_code == 200, empty_goal.text
        no_input = client.post(
            f"/tasks/{task['id']}/preflight",
            headers=headers,
            json={},
        )
        assert no_input.status_code == 200
        assert no_input.json()["ready"] is False
        assert any(issue["path"] == "input" for issue in no_input.json()["issues"])
        no_input_run = client.post(
            f"/tasks/{task['id']}/runs",
            headers=headers,
            json={},
        )
        assert no_input_run.status_code == 422
        assert asyncio.run(store.list_runs("template-owner", task["id"])) == []

        # External expert Tools are not used by the current Task-local adapter.
        task = empty_goal.json()
        restored = client.patch(
            f"/tasks/{task['id']}",
            headers=headers,
            json={"goal": "有效测试输入"},
        )
        assert restored.status_code == 200
        executor.tools = []
        missing = client.post(
            f"/tasks/{task['id']}/preflight",
            headers=headers,
            json={},
        )
        assert missing.status_code == 200
        assert missing.json()["ready"] is False
        assert missing.json()["missing_capabilities"] == []
        assert all(
            check["status"] == "available"
            for check in missing.json()["capability_checks"]
            if check["capability_key"].startswith("expert.user_")
        )
        rejected_run = client.post(
            f"/tasks/{task['id']}/runs",
            headers=headers,
            json={},
        )
        assert rejected_run.status_code == 422
        assert rejected_run.json()["detail"]["code"] == "TASK_PREFLIGHT_BLOCKED"
        assert asyncio.run(store.list_runs("template-owner", task["id"])) == []
        assert executor.executed_nodes == []


def test_template_preflight_does_not_bind_external_schema_for_local_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    _, task_service, _, executor = _expert_template_service(
        _expert_template_tools()
    )
    headers = {"X-User-ID": "template-owner"}

    with _template_client(task_service=task_service) as client:
        created = client.post(
            "/tasks/templates/s03_expert_matching/instantiate",
            headers=headers,
            json=_expert_template_request(),
        )
        assert created.status_code == 201, created.text
        task = created.json()
        executor.tools = [
            tool.model_copy(update={"input_schema": {}})
            if tool.name == "expert.lookup"
            else tool
            for tool in executor.tools
        ]

        preflight = client.post(
            f"/tasks/{task['id']}/preflight",
            headers=headers,
            json={"input": "测试输入"},
        )

    assert preflight.status_code == 200
    assert preflight.json()["ready"] is False
    assert not any(
        issue["path"].endswith("input_schema")
        for issue in preflight.json()["issues"]
    )
    assert {
        check["capability_key"]
        for check in preflight.json()["capability_checks"]
        if check["status"] == "available"
    } >= {
        "expert.user_data_lookup",
        "expert.user_conflict_check",
        "expert.user_rank",
    }


def test_instantiation_and_edit_work_without_tools_but_preflight_blocks_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    store, task_service, planner, executor = _expert_template_service([])

    with _template_client(task_service=task_service) as client:
        response = client.post(
            "/tasks/templates/s03_expert_matching/instantiate",
            headers={"X-User-ID": "template-owner"},
            json=_expert_template_request(),
        )
        assert response.status_code == 201, response.text
        task = response.json()
        assert len(task["nodes"]) == 8
        assert executor.contexts == []

        editable_agent = next(
            node for node in task["nodes"] if node["type"] == "agent"
        )
        editable_agent["prompt"] += "\n仅使用可追溯的业务证据。"
        updated = client.patch(
            f"/tasks/{task['id']}",
            headers={"X-User-ID": "template-owner"},
            json={"nodes": task["nodes"]},
        )
        assert updated.status_code == 200, updated.text
        assert executor.contexts == []

        preflight = client.post(
            f"/tasks/{task['id']}/preflight",
            headers={"X-User-ID": "template-owner"},
            json={"input": "测试输入"},
        )

    assert preflight.status_code == 200
    assert preflight.json()["ready"] is False
    assert preflight.json()["missing_capabilities"] == []
    assert all(
        check["status"] == "available"
        for check in preflight.json()["capability_checks"]
        if check["capability_key"].startswith("expert.user_")
    )
    assert len(asyncio.run(store.list_tasks("template-owner"))) == 1
    assert planner.calls == 0


def test_unrelated_external_expert_tool_does_not_override_task_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    tools = _expert_template_tools()
    tools[0] = tools[0].model_copy(update={"is_read_only": False})
    store, task_service, planner, _ = _expert_template_service(tools)

    with _template_client(task_service=task_service) as client:
        response = client.post(
            "/tasks/templates/s03_expert_matching/instantiate",
            headers={"X-User-ID": "template-owner"},
            json=_expert_template_request(),
        )
        assert response.status_code == 201, response.text
        task = response.json()
        preflight = client.post(
            f"/tasks/{task['id']}/preflight",
            headers={"X-User-ID": "template-owner"},
            json={"input": "测试输入"},
        )

    assert preflight.status_code == 200
    assert preflight.json()["ready"] is False
    check = next(
        item
        for item in preflight.json()["capability_checks"]
        if item["capability_key"] == "expert.user_data_lookup"
    )
    assert check["external_side_effect"] is False
    assert check["status"] == "available"
    assert len(asyncio.run(store.list_tasks("template-owner"))) == 1
    assert planner.calls == 0


def test_instantiation_rejects_invalid_inputs_before_tool_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    store, task_service, planner, executor = _expert_template_service(
        _expert_template_tools()
    )
    request = _expert_template_request()
    request["inputs"].pop("project_name")

    with _template_client(task_service=task_service) as client:
        response = client.post(
            "/tasks/templates/s03_expert_matching/instantiate",
            headers={"X-User-ID": "template-owner"},
            json=request,
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TEMPLATE_INPUT_INVALID"
    assert any(
        issue.startswith("inputs.project_name:")
        for issue in response.json()["detail"]["issues"]
    )
    assert executor.contexts == []
    assert asyncio.run(store.list_tasks("template-owner")) == []
    assert planner.calls == 0


def test_template_instantiation_rejects_client_provenance_and_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    store, task_service, planner, _ = _expert_template_service(
        _expert_template_tools()
    )
    request = _expert_template_request()
    request["source_template_id"] = "s09_expense_settlement"

    with _template_client(task_service=task_service) as client:
        spoofed = client.post(
            "/tasks/templates/s03_expert_matching/instantiate",
            headers={"X-User-ID": "template-owner"},
            json=request,
        )
        old_version_request = _expert_template_request()
        old_version_request["template_version"] = "9.9.9"
        stale = client.post(
            "/tasks/templates/s03_expert_matching/instantiate",
            headers={"X-User-ID": "template-owner"},
            json=old_version_request,
        )

    assert spoofed.status_code == 422
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "TEMPLATE_VERSION_MISMATCH"
    assert asyncio.run(store.list_tasks("template-owner")) == []
    assert planner.calls == 0


def test_compiler_binds_aliases_and_rejects_unbound_requirements() -> None:
    template = TemplateRegistry().get_template_version(
        "s03_expert_matching",
        "2.0.0",
    )
    assert template is not None
    alias_map = {
        "expert.lookup": "mcp__expert__lookup",
        "expert.conflict_check": "mcp__expert__check_conflicts",
        "expert.rank": "mcp__expert__rank",
    }
    tools = _expert_template_tools(list(alias_map.values()))

    compiled = TaskTemplateCompiler(alias_map).compile_nodes(template, tools)

    configured_names = {
        node.config.tool_name
        for node in compiled.nodes
        if node.type.value == "tool"
    }
    assert configured_names == set(alias_map.values())

    project_template = TemplateRegistry().get_template_version(
        "s01_project_init",
        "2.0.0",
    )
    assert project_template is not None
    project_tools = [
        TaskToolSchema(name="project.similar_search"),
        TaskToolSchema(name="project.rule_check"),
    ]
    compiled_project = TaskTemplateCompiler().compile_nodes(
        project_template,
        project_tools,
    )
    assert len(compiled_project.nodes) == len(project_template.steps)


def test_compiler_rejects_expressions_and_invalid_tool_schemas() -> None:
    template_payload = json.loads(
        (TEMPLATE_DIR / "s03_expert_matching.v1.json").read_text(
            encoding="utf-8"
        )
    )
    template_payload["steps"][1]["config"]["prompt"] = (
        "Do not evaluate {{previous_output | unsafe}}."
    )
    invalid_variables = TaskTemplateDefinition.model_validate(template_payload)
    tools = _expert_template_tools()

    with pytest.raises(TemplateCompileError) as unsupported_variable:
        TaskTemplateCompiler().compile_nodes(invalid_variables, tools)
    assert unsupported_variable.value.code == "TEMPLATE_VARIABLE_INVALID"

    tools[0] = tools[0].model_copy(
        update={"input_schema": {"type": "not-a-json-schema"}}
    )
    current_template = TemplateRegistry().get_template_version(
        "s03_expert_matching",
        "2.0.0",
    )
    assert current_template is not None
    with pytest.raises(TemplateCompileError) as invalid_tool_schema:
        TaskTemplateCompiler().compile_nodes(
            current_template,
            tools,
        )
    assert invalid_tool_schema.value.code == "TEMPLATE_CAPABILITY_INCOMPATIBLE"
    assert any(
        "Tool 输入 Schema 无效" in issue
        for issue in invalid_tool_schema.value.issues
    )


def test_template_compiler_reads_only_server_owned_environment_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "LXSCOPE_TASK_CAPABILITY_TOOL_MAP",
        json.dumps({"expert.lookup": "mcp__expert__lookup"}),
    )

    compiler = TaskTemplateCompiler.from_environment()

    assert compiler._aliases == {"expert.lookup": "mcp__expert__lookup"}


def test_task_record_old_json_defaults_template_provenance_fields() -> None:
    old_record = TaskRecord(user_id="legacy-user")
    payload = old_record.model_dump(mode="json")
    payload.pop("source_template_id")
    payload.pop("source_template_version")
    payload.pop("template_step_bindings")
    payload.pop("template_inputs")

    loaded = TaskRecord.model_validate_json(json.dumps(payload))

    assert loaded.source_template_id is None
    assert loaded.source_template_version is None
    assert loaded.template_step_bindings == {}
    assert loaded.template_inputs is None


def test_template_validation_reads_legacy_p3_input_snapshot() -> None:
    template = TemplateRegistry().get_template("s03_expert_matching")
    assert template is not None
    compiled = TaskTemplateCompiler().compile_nodes(
        template,
        _expert_template_tools(),
    )
    inputs = _expert_template_request()["inputs"]
    goal = (
        f"{template.description}\n\n模板输入参数（JSON）：\n"
        + json.dumps(inputs, ensure_ascii=False, sort_keys=True)
    )
    legacy_task = TaskRecord(
        user_id="template-owner",
        goal=goal,
        nodes=compiled.nodes,
        source_template_id=template.template_id,
        source_template_version=template.template_version,
        template_step_bindings=compiled.step_bindings,
    )

    result = TaskTemplateValidator(compiler=TaskTemplateCompiler()).validate(
        legacy_task,
        legacy_task.nodes,
        tool_schemas=_expert_template_tools(),
    )

    assert result.valid is True


def test_instantiate_request_rejects_client_provenance() -> None:
    request = _expert_template_request()
    request["source_template_id"] = "client-controlled"

    with pytest.raises(ValidationError):
        TemplateInstantiateRequest.model_validate(request)
