"""Synthetic, isolated contract checks for the nine current Task scenarios."""

from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Any
from zipfile import is_zipfile

from examples.agent_service.task._artifact_spec import spec_from_markdown
from examples.agent_service.task._artifacts import generate_artifact
from examples.agent_service.task._models import (
    ArtifactConfig,
    ArtifactFormat,
    NodeRunRecord,
    StepType,
    TaskErrorDetail,
    TaskNode,
    TaskCapabilityCheck,
    TaskPreflightRequest,
    TaskRunRecord,
    TaskValidationResponse,
    ToolStepConfig,
)
from examples.agent_service.task._errors import classify_node_error
from examples.agent_service.task._scenario_evidence_policies import (
    get_scenario_evidence_policy,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_models import (
    TemplateInstantiateRequest,
    TemplateInputProperty,
    TaskTemplateDefinition,
)
from examples.agent_service.task._template_registry import TemplateRegistry
from examples.agent_service.task._template_service import TaskTemplateService
from examples.agent_service.task._template_validation import TaskValidationFailure


def _synthetic_value(prop: TemplateInputProperty, key: str) -> Any:
    """Create conspicuously synthetic values for the bounded input schema."""

    if prop.type == "string":
        return f"SYNTHETIC-{key}"
    if prop.type == "integer":
        return max(1, int(prop.minimum or 0))
    if prop.type == "number":
        return max(1.0, float(prop.minimum or 0))
    if prop.type == "boolean":
        return False
    if prop.type == "array":
        assert prop.items is not None
        count = max(1, prop.min_items or 0)
        return [
            _synthetic_value(prop.items, f"{key}-{index + 1}")
            for index in range(count)
        ]
    raise AssertionError(f"Unsupported template input type: {prop.type}")


def _synthetic_inputs(template: TaskTemplateDefinition) -> dict[str, Any]:
    return {
        key: _synthetic_value(prop, key)
        for key, prop in template.input_schema.properties.items()
    }


def test_structured_errors_round_trip_and_legacy_run_shapes_remain_readable() -> None:
    legacy_node = NodeRunRecord(
        node_id="node-1",
        name="合成测试节点",
        prompt="",
        order=0,
        input="synthetic input",
        output="",
        display_summary="",
    )
    legacy_run = TaskRunRecord(
        task_id="task-1",
        user_id="synthetic-user",
        task_revision=1,
        nodes=[],
        node_runs=[legacy_node],
        input="synthetic input",
    )
    assert legacy_node.error_detail is None
    assert legacy_run.error_detail is None

    detail = TaskErrorDetail(
        category="external_interface_failed",
        code="TASK_TOOL_EXECUTION_FAILED",
        message="Synthetic Tool failure",
        task_id="task-1",
        run_id="run-1",
        node_id="node-1",
    )
    current_node = legacy_node.model_copy(update={"error_detail": detail})
    current_run = legacy_run.model_copy(update={"error_detail": detail})
    restored_node = NodeRunRecord.model_validate(
        current_node.model_dump(mode="json")
    )
    restored_run = TaskRunRecord.model_validate(
        current_run.model_dump(mode="json")
    )

    assert restored_node.error_detail is not None
    assert restored_node.error_detail.category == "external_interface_failed"
    assert restored_node.error_detail.run_id == "run-1"
    assert restored_run.error_detail is not None
    assert restored_run.error_detail.node_id == "node-1"


def test_all_current_scenario_artifact_configs_render_real_bytes() -> None:
    registry = TemplateRegistry()
    templates = registry.list_templates()
    assert len(templates) == 9

    for template in templates:
        output = f"# SYNTHETIC {template.template_id}\n\n演示草稿，不是业务结果。"
        configured = template.steps[-1].artifact
        assert configured is not None
        generated = generate_artifact(
            output=output,
            config=ArtifactConfig(
                format=ArtifactFormat(configured.format),
                filename=configured.filename,
            ),
            default_stem=template.template_id,
            spec=spec_from_markdown(output),
        )

        assert generated.data
        assert "/" not in generated.name and "\\" not in generated.name
        if generated.format is ArtifactFormat.MARKDOWN:
            assert generated.data == output.encode("utf-8")
        else:
            assert is_zipfile(BytesIO(generated.data))


def test_error_classification_is_specific_and_api_keeps_preflight_category() -> None:
    node = TaskNode(
        id="node-1",
        name="合成 Tool 节点",
        prompt="",
        type=StepType.TOOL,
        order=0,
        config=ToolStepConfig(tool_name="demo.tool", arguments={}),
    )
    run = TaskRunRecord(
        task_id="task-1",
        user_id="synthetic-user",
        task_revision=1,
        nodes=[node],
        node_runs=[],
    )

    assert classify_node_error(
        run,
        node,
        {},
        "Tool 'demo.tool' is not available in this session.",
    ).category == "missing_tool"
    assert classify_node_error(
        run,
        node,
        {},
        "Permission denied by the synthetic Tool.",
    ).category == "permission_denied"
    assert classify_node_error(
        run,
        node,
        {"capability_output_validation": {"status": "failed"}},
        "Output did not match the capability schema.",
    ).category == "schema_mismatch"
    assert classify_node_error(
        run,
        node,
        {"knowledge_error_code": "retrieval_failed"},
        "Synthetic authorized retrieval failure.",
    ).category == "data_source_not_ready"
    assert classify_node_error(
        run,
        node,
        {},
        "Synthetic writer failure.",
        artifact_failure=True,
    ).category == "artifact_generation_failed"

    preflight_check = TaskCapabilityCheck(
        step_name="Task RAG",
        capability_key="planning.template_retrieve",
        category="数据查询",
        permission_status="unavailable",
        status="unverified",
        message="知识库服务未就绪。",
        error_category="data_source_not_ready",
        error_code="TASK_KNOWLEDGE_SERVICE_UNAVAILABLE",
    )
    failure = TaskValidationFailure(
        TaskValidationResponse(
            valid=False,
            ready=False,
            missing_capabilities=["planning.template_retrieve"],
            capability_checks=[preflight_check],
        )
    )
    assert failure.public_detail()["error_category"] == "data_source_not_ready"


def test_all_scenarios_create_private_tasks_and_fail_preflight_closed() -> None:
    async def exercise() -> None:
        registry = TemplateRegistry()
        store = TaskStore()
        # An object without Planner methods makes accidental planning fail fast.
        task_service = TaskService(store, planner=object())  # type: ignore[arg-type]
        template_service = TaskTemplateService(registry, task_service)

        try:
            templates = registry.list_templates()
            assert len(templates) == 9
            assert {item.template_id for item in templates} == {
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

            current_versions = {
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
            for index, template in enumerate(templates):
                assert template.template_version == current_versions[template.template_id]
                assert 1 <= len(template.steps) <= 8
                policy = get_scenario_evidence_policy(
                    template.template_id,
                    template.template_version,
                )
                assert policy is not None
                assert "fact" in policy.allowed_evidence_types_by_claim_kind
                assert "recommendation" in policy.manual_review_claim_kinds
                request = TemplateInstantiateRequest(
                    template_version=template.template_version,
                    inputs=_synthetic_inputs(template),
                )
                owner = f"synthetic-user-{index}"
                task = await template_service.instantiate(
                    owner,
                    template.template_id,
                    request,
                )

                assert task.workflow_mode.value == "template_instance"
                assert task.source_template_id == template.template_id
                assert task.source_template_version == template.template_version
                assert len(task.nodes) == len(template.steps)
                assert len(task.nodes) <= 8
                assert task.nodes[-1].artifact is not None
                assert await task_service.get_task(owner, task.id) is not None
                assert await task_service.get_task(
                    f"unrelated-user-{index}", task.id
                ) is None

                preflight = await task_service.preflight_task(
                    owner,
                    task.id,
                    TaskPreflightRequest(),
                )
                assert preflight is not None
                assert preflight.ready is False
                assert all(
                    check.status == "available"
                    or (check.error_category and check.error_code)
                    for check in preflight.capability_checks
                )
                for check in preflight.capability_checks:
                    if check.capability_key in {
                        "planning.template_retrieve",
                        "document.citation_verify",
                    } and check.status != "available":
                        assert check.error_category == "data_source_not_ready"
                    if (
                        check.capability_key == "document.render"
                        and check.status != "available"
                    ):
                        assert check.error_category == "artifact_generation_failed"
                assert await task_service.list_runs(owner, task.id) == []
        finally:
            await task_service.shutdown()

    asyncio.run(exercise())
