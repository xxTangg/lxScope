"""Static checks for the P1 Task scenario template data."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from examples.agent_service.task._router import (
    _get_template_registry,
    task_router,
)
from examples.agent_service.task._template_models import TaskTemplateDefinition
from examples.agent_service.task._template_registry import (
    TemplateRegistry,
    TemplateRegistryError,
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


def _template_client(registry: TemplateRegistry | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(task_router)
    if registry is not None:
        app.dependency_overrides[_get_template_registry] = lambda: registry
    return TestClient(app)


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
        assert detail.json()["template_version"] == "1.0.0"

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
    assert missing.value.file_name == "s01_project_init.v1.json"

    invalid_file = tmp_path / "s01_project_init.v1.json"
    invalid_file.write_text("{not-json", encoding="utf-8")
    with pytest.raises(TemplateRegistryError) as invalid_json:
        registry.list_templates()
    assert invalid_json.value.code == "invalid_json"
    assert invalid_json.value.file_name == invalid_file.name
    assert str(tmp_path) not in str(invalid_json.value)


def test_template_api_reports_invalid_catalog_without_exposing_paths(
    tmp_path: Path,
) -> None:
    invalid_file = tmp_path / "s01_project_init.v1.json"
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
    invalid_file = tmp_path / "s01_project_init.v1.json"
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
