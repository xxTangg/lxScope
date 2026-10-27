"""E1 parity checks for versioned scenario Tasks copied to linear DAGs.

Model, Knowledge and Workspace boundaries use the deterministic R7 synthetic
adapters. TaskService, scenario capability adapters, node output gates,
checkpoint persistence and finalization run through the actual Task module.
"""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_r7_nine_scenarios_integration_test as r7  # noqa: E402

from examples.agent_service.task._models import (  # noqa: E402
    NodeRunStatus,
    RunStatus,
    TaskGraph,
    TaskGraphEdge,
    TaskPreflightRequest,
    TaskRunControlRequest,
    TaskRunRequest,
    UpdateTaskRequest,
)
from examples.agent_service.task._service import (  # noqa: E402
    TaskService,
    TaskWorkflowConflictError,
)
from examples.agent_service.task._store import TaskStore  # noqa: E402
from examples.agent_service.task._template_models import (  # noqa: E402
    TemplateInstantiateRequest,
)
from examples.agent_service.task._template_registry import (  # noqa: E402
    TemplateRegistry,
)
from examples.agent_service.task._template_service import (  # noqa: E402
    TaskTemplateService,
)


SCENARIO_IDS = r7.SCENARIO_IDS
SCENARIO_DAG_TEST_ALLOWLIST = {
    ("s01_project_init", "3.0.0"),
    ("s02_project_planning", "5.1.0"),
    ("s03_expert_matching", "3.0.0"),
    ("s04_meeting_preparation", "3.0.0"),
    ("s05_meeting_minutes", "3.0.0"),
    ("s06_opinion_processing", "3.0.0"),
    ("s07_report_generation", "5.0.0"),
    ("s08_archive_transfer", "3.0.0"),
    ("s09_expense_settlement", "3.0.0"),
}
SCENARIOS_WITH_SYNTHETIC_KB = {
    "s01_project_init",
    "s02_project_planning",
    "s07_report_generation",
}
_UUID_PATTERN = re.compile(
    r"\b(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)
_EVIDENCE_ID_PATTERN = re.compile(r"\bEV-[0-9a-fA-F]{20,40}\b")
_TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})\b"
)


def _normalize_runtime_value(value: Any) -> Any:
    """Remove only per-run IDs and timestamps from deterministic comparisons."""

    if isinstance(value, str):
        return _TIMESTAMP_PATTERN.sub(
            "<timestamp>",
            _EVIDENCE_ID_PATTERN.sub(
                "<evidence-id>",
                _UUID_PATTERN.sub("<uuid>", value),
            ),
        )
    if isinstance(value, list):
        return [_normalize_runtime_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _normalize_runtime_value(item)
            for key, item in value.items()
            if key not in {"evidence_id", "retrieved_at", "collected_at"}
        }
    return value


async def _prepare_linear_scenario_dag(
    scenario_id: str,
    *,
    authorize_test_execution: bool,
) -> tuple[
    TaskService,
    Any,
    Any,
    Any,
    r7._SyntheticModelAndWorkspace,
    r7._SyntheticAuthorizedKnowledge,
    str,
    dict[str, Any],
]:
    registry = TemplateRegistry()
    template = registry.get_current_template(scenario_id)
    assert template is not None
    reviewed_version = dict(SCENARIO_DAG_TEST_ALLOWLIST)[scenario_id]
    assert template.template_version == reviewed_version
    inputs = r7._inputs_for(scenario_id, template)
    owner = f"e1-synthetic-{scenario_id}-{uuid4().hex}"
    executor = r7._SyntheticModelAndWorkspace(scenario_id, inputs)
    gateway = r7._SyntheticAuthorizedKnowledge(owner)
    allowlist = (
        {(scenario_id, reviewed_version)}
        if authorize_test_execution
        else set()
    )
    service = TaskService(
        TaskStore(),
        preview_executor=executor,
        artifact_writer=executor,
        enable_dag_scheduler=True,
        enable_dag_editing=True,
        dag_scenario_test_allowlist=allowlist,
    )
    source = await TaskTemplateService(registry, service).instantiate(
        owner,
        scenario_id,
        TemplateInstantiateRequest(
            template_version=template.template_version,
            inputs=inputs,
        ),
    )
    if scenario_id in SCENARIOS_WITH_SYNTHETIC_KB:
        source = await service.update_task(
            owner,
            source.id,
            UpdateTaskRequest(knowledge_base_ids=[r7.KB_ID]),
        )
        assert source is not None
    dag = await service.create_custom_flow_copy(owner, source.id, mode="dag")
    assert dag is not None and dag.graph is not None
    return service, registry, source, dag, executor, gateway, owner, inputs


def _assert_scenario_gates(run: Any) -> None:
    assert run.status is RunStatus.SUCCEEDED
    assert all(item.status is NodeRunStatus.SUCCEEDED for item in run.node_runs)
    for node, record in zip(run.nodes, run.node_runs):
        assert record.output
        if node.type.value == "agent":
            verification = record.metadata.get("task_ai_verification")
            assert isinstance(verification, dict)
            assert verification.get("method") == "task-verifier"
            assert verification.get("passed") is True
        if node.output_schema is not None or node.output_contract is not None:
            validation = record.metadata.get("output_contract_validation")
            assert isinstance(validation, dict)
            assert validation.get("status") == "passed"


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_current_scenario_linear_dag_reuses_sop_gates_and_delivery(
    scenario_id: str,
) -> None:
    async def exercise() -> None:
        sop_service, _, sop_task, sop_template, sop_executor, sop_gateway, sop_owner, inputs = (
            await r7._prepare(scenario_id)
        )
        try:
            sop_created = await sop_service.create_run(
                sop_owner,
                sop_task.id,
                TaskRunRequest(input="E1 DEMO/SYNTHETIC SOP/DAG 门禁对比"),
                sop_gateway,
            )
            assert sop_created is not None and sop_created.engine == "sop"
            sop_final = await r7._drive(
                sop_service,
                sop_owner,
                sop_created.id,
                sop_gateway,
                inputs,
            )
            _assert_scenario_gates(sop_final)
            assert len(sop_final.artifacts) == 1
            sop_artifact = sop_executor.artifacts[sop_final.artifacts[0].path]
            sop_artifact_text = r7._artifact_text(
                sop_artifact,
                sop_final.artifacts[0].format.value,
            )
            r7._assert_business_results(
                scenario_id,
                sop_final,
                inputs,
                sop_artifact_text,
            )

            (
                dag_service,
                _,
                source,
                dag_task,
                dag_executor,
                dag_gateway,
                dag_owner,
                dag_inputs,
            ) = await _prepare_linear_scenario_dag(
                scenario_id,
                authorize_test_execution=True,
            )
            try:
                assert dag_task.source_template_id == source.source_template_id
                assert dag_task.source_template_version == sop_template.template_version
                assert dag_task.graph is not None
                assert dag_task.graph.max_concurrency == 1
                assert len(dag_task.graph.edges) == len(dag_task.nodes) - 1
                preflight = await dag_service.preflight_task(
                    dag_owner,
                    dag_task.id,
                    TaskPreflightRequest(),
                    dag_gateway,
                )
                assert preflight is not None and preflight.ready, (
                    scenario_id,
                    preflight.model_dump(mode="json") if preflight else None,
                )
                dag_created = await dag_service.create_run(
                    dag_owner,
                    dag_task.id,
                    TaskRunRequest(input="E1 DEMO/SYNTHETIC SOP/DAG 门禁对比"),
                    dag_gateway,
                )
                assert dag_created is not None and dag_created.engine == "dag"
                dag_final = await r7._drive(
                    dag_service,
                    dag_owner,
                    dag_created.id,
                    dag_gateway,
                    dag_inputs,
                )
                _assert_scenario_gates(dag_final)
                assert dag_final.id == dag_created.id
                assert dag_final.source_template_version == sop_template.template_version
                assert len(dag_final.artifacts) == 1
                dag_artifact = dag_executor.artifacts[dag_final.artifacts[0].path]
                dag_artifact_text = r7._artifact_text(
                    dag_artifact,
                    dag_final.artifacts[0].format.value,
                )
                r7._assert_business_results(
                    scenario_id,
                    dag_final,
                    dag_inputs,
                    dag_artifact_text,
                )

                # For the linear compatibility profile, each Agent receives
                # exactly the preceding persisted output, as it does in SOP.
                dag_calls = set(dag_executor.calls)
                sop_calls = set(sop_executor.calls)
                for index, node in enumerate(dag_final.nodes):
                    if node.type.value != "agent" or index == 0:
                        continue
                    dag_expected_input = dag_final.node_runs[index - 1].output
                    sop_expected_input = sop_final.node_runs[index - 1].output
                    assert (node.id, dag_expected_input) in dag_calls
                    sop_node = sop_final.nodes[index]
                    assert (sop_node.id, sop_expected_input) in sop_calls
                    assert _normalize_runtime_value(dag_expected_input) == (
                        _normalize_runtime_value(sop_expected_input)
                    )

                for dag_node, sop_node in zip(dag_final.node_runs, sop_final.node_runs):
                    assert _normalize_runtime_value(dag_node.input) == (
                        _normalize_runtime_value(sop_node.input)
                    )
                    assert _normalize_runtime_value(dag_node.output) == (
                        _normalize_runtime_value(sop_node.output)
                    )
                    assert type(dag_node.result) is type(sop_node.result)
                    if isinstance(dag_node.result, dict) and isinstance(
                        sop_node.result,
                        dict,
                    ):
                        assert dag_node.result.keys() == sop_node.result.keys()
                assert _normalize_runtime_value(dag_final.final_output) == (
                    _normalize_runtime_value(sop_final.final_output)
                )
                assert _normalize_runtime_value(dag_artifact_text) == (
                    _normalize_runtime_value(sop_artifact_text)
                )
                assert dag_final.delivery_status == sop_final.delivery_status
                if scenario_id == "s02_project_planning":
                    assert dag_final.delivery_mode == sop_final.delivery_mode == "lightweight_draft"
                    assert dag_final.delivery_status == "draft_unverified"
                    assert dag_final.verification_report["status"] == "draft_unverified"
                    assert dag_final.verification_report["evidence_ledger_status"] == "not_performed"
                    assert dag_final.verification_report["human_review_required"] is True
                    assert _normalize_runtime_value(dag_final.verification_report) == (
                        _normalize_runtime_value(sop_final.verification_report)
                    )
                    assert dag_final.artifacts[0].delivery_status == "draft_unverified"
                    assert "未完成完整证据核验" in dag_artifact_text

                dag_events = await dag_service.list_events(dag_owner, dag_final.id)
                assert dag_events[-1].type == "run.succeeded"
            finally:
                await dag_service.shutdown()
        finally:
            await sop_service.shutdown()

    asyncio.run(exercise())


def test_source_template_dag_requires_exact_test_allowlist_and_stays_fail_closed() -> None:
    async def exercise() -> None:
        service, _, _, dag, _, gateway, owner, _ = await _prepare_linear_scenario_dag(
            "s09_expense_settlement",
            authorize_test_execution=False,
        )
        try:
            preflight = await service.preflight_task(
                owner,
                dag.id,
                TaskPreflightRequest(),
                gateway,
            )
            assert preflight is not None
            assert any(
                issue.path == "graph.scenario_execution"
                and "精确模板 ID/版本" in issue.message
                for issue in preflight.issues
            )
            with pytest.raises(TaskWorkflowConflictError, match="versioned scenario Tasks"):
                await service.create_run(
                    owner,
                    dag.id,
                    TaskRunRequest(input="E1 synthetic unauthorized run"),
                    gateway,
                )
            assert await service.list_runs(owner, dag.id) == []
        finally:
            await service.shutdown()

    asyncio.run(exercise())


def test_scenario_dag_editor_may_save_non_linear_graph_but_preflight_blocks_run() -> None:
    async def exercise() -> None:
        service, _, _, dag, _, gateway, owner, _ = await _prepare_linear_scenario_dag(
            "s09_expense_settlement",
            authorize_test_execution=True,
        )
        try:
            assert dag.graph is not None
            edited_graph = dag.graph.model_copy(update={"max_concurrency": 2})
            saved = await service.update_task(
                owner,
                dag.id,
                UpdateTaskRequest(graph=edited_graph),
            )
            assert saved is not None and saved.graph is not None
            assert saved.graph.max_concurrency == 2
            preflight = await service.preflight_task(
                owner,
                saved.id,
                TaskPreflightRequest(),
                gateway,
            )
            assert preflight is not None and any(
                issue.path == "graph.scenario_execution"
                and "并发度为 1" in issue.message
                for issue in preflight.issues
            )
            with pytest.raises(TaskWorkflowConflictError, match="并发度为 1"):
                await service.create_run(
                    owner,
                    saved.id,
                    TaskRunRequest(input="E1 synthetic modified graph"),
                    gateway,
                )
        finally:
            await service.shutdown()

    asyncio.run(exercise())


def test_scenario_dag_rejected_final_agent_does_not_write_artifact() -> None:
    async def exercise() -> None:
        service, _, _, dag, executor, gateway, owner, inputs = (
            await _prepare_linear_scenario_dag(
                "s07_report_generation",
                authorize_test_execution=True,
            )
        )
        try:
            assert dag.nodes
            executor.reject_node_id = dag.nodes[-1].id
            created = await service.create_run(
                owner,
                dag.id,
                TaskRunRequest(input="E1 DEMO/SYNTHETIC verifier rejection"),
                gateway,
            )
            assert created is not None
            final = await r7._drive(service, owner, created.id, gateway, inputs)
            assert final.status is RunStatus.FAILED
            assert final.artifacts == []
            assert all(
                item.status is NodeRunStatus.SUCCEEDED
                for item in final.node_runs[:-1]
            )
            failed = final.node_runs[-1]
            assert failed.status is NodeRunStatus.FAILED
            verification = failed.metadata.get("task_ai_verification")
            assert isinstance(verification, dict)
            assert verification.get("passed") is False
            assert executor.artifacts == {}
        finally:
            await service.shutdown()

    asyncio.run(exercise())


def test_s09_scenario_dag_pause_checkpoint_rehydrates_service_then_continues() -> None:
    async def exercise() -> None:
        service, registry, _, dag, executor, gateway, owner, inputs = (
            await _prepare_linear_scenario_dag(
                "s09_expense_settlement",
                authorize_test_execution=True,
            )
        )
        try:
            blocked_run, _ = await r7._start_blocked_agent(
                service,
                dag,
                executor,
                gateway,
                owner,
                inputs,
            )
            before_pause = await service.get_run(owner, blocked_run.id, gateway)
            assert before_pause is not None and before_pause.status is RunStatus.RUNNING
            successful_before_pause = {
                item.node_id: item.model_dump(mode="json")
                for item in before_pause.node_runs
                if item.status is NodeRunStatus.SUCCEEDED
            }
            assert successful_before_pause
            paused_request = await service.pause_run(
                owner,
                blocked_run.id,
                TaskRunControlRequest(
                    request_id="e1-s09-pause",
                    expected_run_version=before_pause.run_version,
                ),
            )
            assert paused_request is not None
            executor.release.set()
            active = service._active_runs.get(blocked_run.id)
            assert active is not None
            await asyncio.wait_for(active, timeout=30)
            paused = await service.get_run(owner, blocked_run.id, gateway)
            assert paused is not None
            assert paused.control_mode == "paused"
            assert paused.status is RunStatus.QUEUED
            for node_id, snapshot in successful_before_pause.items():
                current = next(item for item in paused.node_runs if item.node_id == node_id)
                assert current.model_dump(mode="json") == snapshot

            store = service._store
            await service.shutdown()
            template = registry.get_current_template("s09_expense_settlement")
            assert template is not None
            assert template.template_version == dict(
                SCENARIO_DAG_TEST_ALLOWLIST
            )[template.template_id]
            service = TaskService(
                store,
                preview_executor=executor,
                artifact_writer=executor,
                enable_dag_scheduler=True,
                enable_dag_editing=True,
                dag_scenario_test_allowlist={
                    (template.template_id, template.template_version)
                },
            )
            restored = await service.get_run(owner, blocked_run.id, gateway)
            assert restored is not None
            assert restored.control_mode == "paused"
            assert restored.status is RunStatus.QUEUED
            for node_id, snapshot in successful_before_pause.items():
                current = next(item for item in restored.node_runs if item.node_id == node_id)
                assert current.model_dump(mode="json") == snapshot

            continued = await service.continue_run(
                owner,
                blocked_run.id,
                TaskRunControlRequest(
                    request_id="e1-s09-continue-after-rebuild",
                    expected_run_version=restored.run_version,
                ),
                gateway,
            )
            assert continued is not None
            final = await r7._drive(service, owner, blocked_run.id, gateway, inputs)
            _assert_scenario_gates(final)
            assert final.id == blocked_run.id
            for node_id, snapshot in successful_before_pause.items():
                current = next(item for item in final.node_runs if item.node_id == node_id)
                assert current.attempt == snapshot["attempt"]
                assert current.output == snapshot["output"]
                assert current.started_at == datetime.fromisoformat(
                    snapshot["started_at"].replace("Z", "+00:00")
                )
                assert current.finished_at == datetime.fromisoformat(
                    snapshot["finished_at"].replace("Z", "+00:00")
                )
        finally:
            executor.release.set()
            await service.shutdown()

    asyncio.run(exercise())
