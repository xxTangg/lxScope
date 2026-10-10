"""Static checks for the P1 Task scenario template data."""

import json
import re
from pathlib import Path
from typing import Any


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
