"""TaskService acceptance for the one reviewed S01 non-linear DAG profile.

Only deterministic Task model/workspace and authorized-knowledge adapters are
synthetic here. Task template instantiation, graph validation, preflight,
Run creation, scheduling, persistence, retry, control, final gates, and
artifact registration use the real TaskService/TaskStore paths.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_r7_nine_scenarios_integration_test as r7  # noqa: E402
import task_dag_e1_business_gate_test as e1  # noqa: E402

from examples.agent_service.task._models import (  # noqa: E402
    NodeRunStatus,
    RunStatus,
    TaskGraph,
    TaskGraphEdge,
    TaskGraphInputBinding,
    TaskInputReply,
    TaskPreflightRequest,
    TaskRunControlRequest,
    TaskRunRequest,
    UpdateTaskRequest,
)
from examples.agent_service.task._scenario_runtime import _retrieval_query  # noqa: E402
from examples.agent_service.task._service import (  # noqa: E402
    TaskService,
    TaskValidationFailure,
    TaskWorkflowConflictError,
)
from examples.agent_service.task._knowledge import TaskKnowledgeError  # noqa: E402


SCENARIO_ID = "s01_project_init"
SCENARIO_VERSION = "3.0.0"
_STEP_KEYS = (
    "collect_project_input",
    "extract_project_profile",
    "search_similar_projects",
    "check_project_rules",
    "draft_initiation_recommendation",
    "review_project_facts",
    "produce_initiation_card",
)


def _run_async_test(async_test: Any) -> Any:
    """Run async acceptance tests without adding a pytest plugin dependency."""

    def wrapper() -> None:
        asyncio.run(async_test())

    wrapper.__name__ = async_test.__name__
    wrapper.__doc__ = async_test.__doc__
    return wrapper


class _PilotKnowledge(r7._SyntheticAuthorizedKnowledge):
    """Synthetic authorized KB that can gate or fail exact read-only queries."""

    def __init__(self, owner: str) -> None:
        super().__init__(owner)
        self.queries: list[tuple[str, str]] = []
        self.active_queries = 0
        self.max_active_queries = 0
        self.hold_capabilities: set[str] = set()
        self.release = {
            "project.similar_search": asyncio.Event(),
            "project.rule_check": asyncio.Event(),
        }
        for event in self.release.values():
            event.set()
        self.entered = {
            "project.similar_search": asyncio.Event(),
            "project.rule_check": asyncio.Event(),
        }

    async def retrieve_task_evidence(
        self,
        user_id: str,
        knowledge_base_ids: list[str],
        query: str,
        *,
        source_material_ids: list[str],
        top_k: int,
    ) -> dict[str, Any]:
        capability = (
            "project.rule_check"
            if "规则" in query or "办法" in query
            else "project.similar_search"
        )
        self.queries.append((capability, query))
        self.active_queries += 1
        self.max_active_queries = max(
            self.max_active_queries,
            self.active_queries,
        )
        self.entered[capability].set()
        try:
            # Yield so two simultaneously dispatched Task nodes can overlap.
            await asyncio.sleep(0)
            if capability in self.hold_capabilities:
                await self.release[capability].wait()
            return await super().retrieve_task_evidence(
                user_id,
                knowledge_base_ids,
                query,
                source_material_ids=source_material_ids,
                top_k=top_k,
            )
        finally:
            self.active_queries -= 1


async def _prepare_pilot(
    *,
    authorize_test_execution: bool = True,
) -> tuple[
    TaskService,
    Any,
    Any,
    r7._SyntheticModelAndWorkspace,
    _PilotKnowledge,
    str,
    dict[str, Any],
]:
    service, registry, source, copied, executor, _, owner, inputs = (
        await e1._prepare_linear_scenario_dag(
            SCENARIO_ID,
            authorize_test_execution=authorize_test_execution,
        )
    )
    gateway = _PilotKnowledge(owner)
    executor.named_inputs_by_node = {}
    executor.previous_inputs_by_node = {}
    execute_node = executor.execute_node

    async def capture_executor_inputs(
        *, node: Any, previous_output: str, context: Any
    ) -> Any:
        executor.named_inputs_by_node[node.id] = dict(context.named_inputs)
        executor.previous_inputs_by_node[node.id] = previous_output
        return await execute_node(
            node=node,
            previous_output=previous_output,
            context=context,
        )

    executor.execute_node = capture_executor_inputs
    saved = await service.update_task(
        owner,
        copied.id,
        UpdateTaskRequest(graph=_pilot_graph(copied)),
    )
    assert saved is not None and saved.graph is not None
    return service, source, saved, executor, gateway, owner, inputs


def _pilot_graph(task: Any) -> TaskGraph:
    ids = {key: task.template_step_bindings[key] for key in _STEP_KEYS}
    edges = [
        ("collect_project_input", "extract_project_profile"),
        ("collect_project_input", "search_similar_projects"),
        ("collect_project_input", "check_project_rules"),
        ("extract_project_profile", "draft_initiation_recommendation"),
        ("search_similar_projects", "draft_initiation_recommendation"),
        ("check_project_rules", "draft_initiation_recommendation"),
        ("draft_initiation_recommendation", "review_project_facts"),
        ("review_project_facts", "produce_initiation_card"),
    ]

    def binding(source_key: str, source_value: str) -> TaskGraphInputBinding:
        return TaskGraphInputBinding(
            source_node_id=ids[source_key],
            source_value=source_value,
        )

    return TaskGraph(
        edges=[
            TaskGraphEdge(
                source_node_id=ids[source],
                target_node_id=ids[target],
            )
            for source, target in edges
        ],
        input_bindings={
            ids["search_similar_projects"]: {
                field: binding("collect_project_input", "output")
                for field in ("project_name", "project_brief", "industry")
            },
            ids["check_project_rules"]: {
                field: binding("collect_project_input", "output")
                for field in ("project_brief", "industry", "scope")
            },
            ids["draft_initiation_recommendation"]: {
                "project_profile": binding("extract_project_profile", "output"),
                "similar_projects": binding("search_similar_projects", "result"),
                "rule_candidates": binding("check_project_rules", "result"),
            },
        },
        max_concurrency=3,
    )


async def _wait_for_run(
    service: TaskService,
    owner: str,
    run_id: str,
    predicate: Any,
    *,
    timeout: float = 20,
) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        run = await service.get_run(owner, run_id)
        assert run is not None
        if predicate(run):
            return run
        await asyncio.sleep(0.005)
    run = await service.get_run(owner, run_id)
    raise AssertionError(f"Timed out waiting for S01 Run checkpoint: {run!r}")


async def _answer_pending(
    service: TaskService,
    owner: str,
    run: Any,
    gateway: _PilotKnowledge,
    inputs: dict[str, Any],
) -> Any:
    current = await service.get_run(owner, run.id, gateway)
    assert current is not None and current.pending_input is not None
    reply = TaskInputReply(
        request_id=current.pending_input.id,
        answers={
            question.key: r7._answer_value(question.key, inputs)
            for question in current.pending_input.questions
        },
    )
    answered = await service.answer_run(owner, current.id, reply, gateway)
    assert answered is not None
    return answered


def _ids(task: Any) -> dict[str, str]:
    return {key: task.template_step_bindings[key] for key in _STEP_KEYS}


@_run_async_test
async def test_s01_non_linear_pilot_is_exactly_scoped_and_remains_test_authorized() -> None:
    service, source, task, _, gateway, owner, _ = await _prepare_pilot(
        authorize_test_execution=False,
    )
    try:
        assert task.source_template_id == source.source_template_id == SCENARIO_ID
        assert task.source_template_version == source.source_template_version == SCENARIO_VERSION
        assert task.source_task_id == source.id
        assert task.graph is not None
        assert task.graph.max_concurrency == 3
        for copied_node, source_node in zip(task.nodes, source.nodes):
            assert copied_node.model_dump(exclude={"id", "order"}) == source_node.model_dump(
                exclude={"id", "order"}
            )

        preflight = await service.preflight_task(
            owner,
            task.id,
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
                task.id,
                TaskRunRequest(input="E2 synthetic unauthorized S01 DAG"),
                gateway,
            )
        assert await service.list_runs(owner, task.id) == []

        invalid_concurrency = task.graph.model_copy(update={"max_concurrency": 4})
        with pytest.raises(TaskValidationFailure):
            await service.update_task(
                owner,
                task.id,
                UpdateTaskRequest(graph=invalid_concurrency),
            )
        ids = _ids(task)
        wrong_binding = _pilot_graph(task).model_copy(
            deep=True,
            update={
                "input_bindings": {
                    **_pilot_graph(task).input_bindings,
                    ids["search_similar_projects"]: {
                        **_pilot_graph(task).input_bindings[
                            ids["search_similar_projects"]
                        ],
                        "project_name": TaskGraphInputBinding(
                            source_node_id=ids["extract_project_profile"],
                            source_value="output",
                        ),
                    },
                }
            },
        )
        with pytest.raises(TaskValidationFailure):
            await service.update_task(
                owner,
                task.id,
                UpdateTaskRequest(graph=wrong_binding),
            )
    finally:
        await service.shutdown()


@_run_async_test
async def test_s01_parallel_retrievals_overlap_and_join_waits_for_both_branches() -> None:
    service, source, task, executor, gateway, owner, inputs = await _prepare_pilot()
    ids = _ids(task)
    gateway.hold_capabilities.add("project.similar_search")
    gateway.release["project.similar_search"].clear()
    try:
        preflight = await service.preflight_task(
            owner,
            task.id,
            TaskPreflightRequest(),
            gateway,
        )
        assert preflight is not None and preflight.ready
        created = await service.create_run(
            owner,
            task.id,
            TaskRunRequest(input="E2 DEMO/SYNTHETIC S01 parallel DAG"),
            gateway,
        )
        assert created is not None and created.engine == "dag"
        await _wait_for_run(
            service,
            owner,
            created.id,
            lambda run: run.status is RunStatus.AWAITING_INPUT,
        )
        await _answer_pending(service, owner, created, gateway, inputs)
        await asyncio.wait_for(gateway.entered["project.similar_search"].wait(), 15)
        partial = await _wait_for_run(
            service,
            owner,
            created.id,
            lambda run: (
                next(item for item in run.node_runs if item.node_id == ids["extract_project_profile"]).status
                is NodeRunStatus.SUCCEEDED
                and next(item for item in run.node_runs if item.node_id == ids["check_project_rules"]).status
                is NodeRunStatus.SUCCEEDED
                and next(item for item in run.node_runs if item.node_id == ids["search_similar_projects"]).status
                is NodeRunStatus.RUNNING
            ),
        )
        assert gateway.max_active_queries >= 2
        partial_nodes = {item.node_id: item for item in partial.node_runs}
        assert partial_nodes[ids["draft_initiation_recommendation"]].status is NodeRunStatus.PENDING
        assert partial_nodes[ids["review_project_facts"]].status is NodeRunStatus.PENDING
        assert partial_nodes[ids["produce_initiation_card"]].status is NodeRunStatus.PENDING
        assert partial.artifacts == []

        gateway.release["project.similar_search"].set()
        final = await r7._drive(service, owner, created.id, gateway, inputs)
        assert final.status is RunStatus.SUCCEEDED
        assert final.id == created.id
        assert final.source_template_version == SCENARIO_VERSION
        assert all(item.status is NodeRunStatus.SUCCEEDED for item in final.node_runs)
        assert len(final.artifacts) == 1
        assert final.artifacts[0].format.value == "markdown"
        artifact = executor.artifacts[final.artifacts[0].path]
        artifact_text = r7._artifact_text(artifact, final.artifacts[0].format.value)
        r7._assert_business_results(SCENARIO_ID, final, inputs, artifact_text)

        captured_queries = {capability: query for capability, query in gateway.queries}
        expected_values = {key: str(value) for key, value in inputs.items()}
        assert captured_queries["project.similar_search"] == _retrieval_query(
            "project.similar_search",
            expected_values,
        )
        assert captured_queries["project.rule_check"] == _retrieval_query(
            "project.rule_check",
            expected_values,
        )
        final_nodes = {item.node_id: item for item in final.node_runs}
        join_inputs = executor.named_inputs_by_node[ids["draft_initiation_recommendation"]]
        assert set(join_inputs) == {"project_profile", "similar_projects", "rule_candidates"}
        assert join_inputs["project_profile"] == final_nodes[ids["extract_project_profile"]].output
        assert join_inputs["similar_projects"] == final_nodes[ids["search_similar_projects"]].result
        assert join_inputs["rule_candidates"] == final_nodes[ids["check_project_rules"]].result
        assert final_nodes[ids["check_project_rules"]].result["status"] == "candidates_for_review"
        assert final_nodes[ids["check_project_rules"]].result[
            "automated_compliance_conclusion"
        ] is False

        events = await service.list_events(owner, final.id)
        event_index = {
            (event.type, event.node_id): index
            for index, event in enumerate(events)
        }
        draft_success = event_index[("step.succeeded", ids["draft_initiation_recommendation"])]
        assert event_index[("step.succeeded", ids["search_similar_projects"])] < draft_success
        assert event_index[("step.succeeded", ids["check_project_rules"])] < draft_success
        assert events[-1].type == "run.succeeded"
        assert source.nodes
    finally:
        gateway.release["project.similar_search"].set()
        await service.shutdown()


@_run_async_test
async def test_s01_failed_read_only_branch_retries_in_same_run_and_preserves_other_branch() -> None:
    service, _, task, executor, gateway, owner, inputs = await _prepare_pilot()
    ids = _ids(task)
    gateway.fail_runtime_capability = "project.similar_search"
    try:
        created = await service.create_run(
            owner,
            task.id,
            TaskRunRequest(input="E2 DEMO/SYNTHETIC S01 retry branch"),
            gateway,
        )
        assert created is not None
        failed = await r7._drive(service, owner, created.id, gateway, inputs)
        assert failed.status is RunStatus.FAILED
        assert failed.id == created.id
        before = {item.node_id: item for item in failed.node_runs}
        assert before[ids["extract_project_profile"]].status is NodeRunStatus.SUCCEEDED
        assert before[ids["check_project_rules"]].status is NodeRunStatus.SUCCEEDED
        failed_branch = before[ids["search_similar_projects"]]
        assert failed_branch.status is NodeRunStatus.FAILED
        assert failed_branch.attempt >= 1
        assert any(item.status == "failed" for item in failed_branch.attempt_history)
        assert before[ids["draft_initiation_recommendation"]].status is NodeRunStatus.PENDING
        assert before[ids["review_project_facts"]].status is NodeRunStatus.PENDING
        assert before[ids["produce_initiation_card"]].status is NodeRunStatus.PENDING
        assert failed.artifacts == []
        assert executor.artifacts == {}

        retained_ids = (
            ids["collect_project_input"],
            ids["extract_project_profile"],
            ids["check_project_rules"],
        )
        retained = {
            node_id: before[node_id].model_dump(mode="json")
            for node_id in retained_ids
        }
        profile_call_count = sum(
            node_id == ids["extract_project_profile"]
            for node_id, _ in executor.calls
        )
        rule_retrieval_count = sum(
            capability == "project.rule_check"
            for capability, _ in gateway.queries
        )

        gateway.fail_runtime_capability = None
        request = TaskRunControlRequest(
            request_id="e2-s01-retry-similar-read-only",
            expected_run_version=failed.run_version,
            side_effects_verified=True,
        )
        retried = await service.retry_node(
            owner,
            failed.id,
            ids["search_similar_projects"],
            request,
        )
        assert retried is not None and retried.id == failed.id
        assert retried.status is RunStatus.QUEUED
        assert retried.control_mode == "paused"
        assert retried.run_version != failed.run_version
        retry_nodes = {item.node_id: item for item in retried.node_runs}
        assert retry_nodes[ids["search_similar_projects"]].status is NodeRunStatus.PENDING
        assert retry_nodes[ids["draft_initiation_recommendation"]].status is NodeRunStatus.PENDING
        duplicate = await service.retry_node(
            owner,
            failed.id,
            ids["search_similar_projects"],
            request,
        )
        assert duplicate is not None and duplicate.run_version == retried.run_version

        continued = await service.continue_run(
            owner,
            failed.id,
            TaskRunControlRequest(
                request_id="e2-s01-continue-after-retry",
                expected_run_version=retried.run_version,
            ),
            gateway,
        )
        assert continued is not None and continued.id == failed.id
        final = await r7._drive(service, owner, failed.id, gateway, inputs)
        assert final.status is RunStatus.SUCCEEDED
        assert final.id == failed.id
        final_nodes = {item.node_id: item for item in final.node_runs}
        for node_id, snapshot in retained.items():
            assert final_nodes[node_id].model_dump(mode="json") == snapshot
        assert final_nodes[ids["search_similar_projects"]].attempt > failed_branch.attempt
        assert any(
            item.status == "failed"
            for item in final_nodes[ids["search_similar_projects"]].attempt_history
        )
        assert sum(
            node_id == ids["extract_project_profile"]
            for node_id, _ in executor.calls
        ) == profile_call_count
        assert sum(
            capability == "project.rule_check"
            for capability, _ in gateway.queries
        ) == rule_retrieval_count
        assert len(final.artifacts) == 1
        artifact_text = r7._artifact_text(
            executor.artifacts[final.artifacts[0].path],
            final.artifacts[0].format.value,
        )
        r7._assert_business_results(SCENARIO_ID, final, inputs, artifact_text)
    finally:
        await service.shutdown()


@_run_async_test
async def test_s01_pilot_pause_step_continue_and_stop_keep_dag_control_semantics() -> None:
    service, _, task, executor, gateway, owner, inputs = await _prepare_pilot()
    ids = _ids(task)
    gateway.hold_capabilities = {"project.similar_search", "project.rule_check"}
    for event in gateway.release.values():
        event.clear()
    try:
        created = await service.create_run(
            owner,
            task.id,
            TaskRunRequest(input="E2 DEMO/SYNTHETIC S01 controls"),
            gateway,
        )
        assert created is not None
        await _wait_for_run(
            service,
            owner,
            created.id,
            lambda run: run.status is RunStatus.AWAITING_INPUT,
        )
        answered = await _answer_pending(service, owner, created, gateway, inputs)
        await asyncio.gather(
            gateway.entered["project.similar_search"].wait(),
            gateway.entered["project.rule_check"].wait(),
        )
        current = await service.get_run(owner, created.id, gateway)
        assert current is not None and current.status is RunStatus.RUNNING
        pause = await service.pause_run(
            owner,
            created.id,
            TaskRunControlRequest(
                request_id="e2-s01-pause",
                expected_run_version=current.run_version,
            ),
        )
        assert pause is not None and pause.control_mode == "pause_requested"
        for event in gateway.release.values():
            event.set()
        paused = await _wait_for_run(
            service,
            owner,
            created.id,
            lambda run: run.control_mode == "paused" and run.status is RunStatus.QUEUED,
        )
        paused_nodes = {item.node_id: item for item in paused.node_runs}
        assert all(
            paused_nodes[ids[key]].status is NodeRunStatus.SUCCEEDED
            for key in (
                "collect_project_input",
                "extract_project_profile",
                "search_similar_projects",
                "check_project_rules",
            )
        )
        assert paused_nodes[ids["draft_initiation_recommendation"]].status is NodeRunStatus.PENDING

        before_step_calls = len(executor.calls)
        stepped = await service.step_run(
            owner,
            created.id,
            TaskRunControlRequest(
                request_id="e2-s01-step-join",
                expected_run_version=paused.run_version,
            ),
            gateway,
        )
        assert stepped is not None
        await asyncio.wait_for(service._active_runs[created.id], timeout=30)
        after_step = await service.get_run(owner, created.id, gateway)
        assert after_step is not None
        assert after_step.control_mode == "paused"
        assert after_step.status is RunStatus.QUEUED
        after_step_nodes = {item.node_id: item for item in after_step.node_runs}
        assert after_step_nodes[ids["draft_initiation_recommendation"]].status is NodeRunStatus.SUCCEEDED
        assert after_step_nodes[ids["review_project_facts"]].status is NodeRunStatus.PENDING
        assert after_step_nodes[ids["produce_initiation_card"]].status is NodeRunStatus.PENDING
        assert len(executor.calls) == before_step_calls + 1
        assert executor.calls[-1][0] == ids["draft_initiation_recommendation"]

        continued = await service.continue_run(
            owner,
            created.id,
            TaskRunControlRequest(
                request_id="e2-s01-continue",
                expected_run_version=after_step.run_version,
            ),
            gateway,
        )
        assert continued is not None
        final = await r7._drive(service, owner, created.id, gateway, inputs)
        assert final.status is RunStatus.SUCCEEDED
        assert all(item.status is NodeRunStatus.SUCCEEDED for item in final.node_runs)
        artifacts_after_continue = dict(executor.artifacts)
        assert len(artifacts_after_continue) == 1

        # A separate paused Run proves Stop is terminal and cannot be resumed.
        for event in gateway.entered.values():
            event.clear()
        for event in gateway.release.values():
            event.clear()
        second = await service.create_run(
            owner,
            task.id,
            TaskRunRequest(input="E2 DEMO/SYNTHETIC S01 stop"),
            gateway,
        )
        assert second is not None
        await _wait_for_run(
            service,
            owner,
            second.id,
            lambda run: run.status is RunStatus.AWAITING_INPUT,
        )
        await _answer_pending(service, owner, second, gateway, inputs)
        await asyncio.gather(
            gateway.entered["project.similar_search"].wait(),
            gateway.entered["project.rule_check"].wait(),
        )
        current = await service.get_run(owner, second.id, gateway)
        assert current is not None
        paused_request = await service.pause_run(
            owner,
            second.id,
            TaskRunControlRequest(
                request_id="e2-s01-pause-before-stop",
                expected_run_version=current.run_version,
            ),
        )
        assert paused_request is not None
        for event in gateway.release.values():
            event.set()
        paused_second = await _wait_for_run(
            service,
            owner,
            second.id,
            lambda run: run.control_mode == "paused" and run.status is RunStatus.QUEUED,
        )
        stopped = await service.stop_run(
            owner,
            second.id,
            TaskRunControlRequest(
                request_id="e2-s01-stop",
                expected_run_version=paused_second.run_version,
            ),
        )
        assert stopped is not None
        assert stopped.status is RunStatus.CANCELED
        assert stopped.control_mode == "stopped"
        with pytest.raises(TaskWorkflowConflictError, match="terminal"):
            await service.continue_run(
                owner,
                second.id,
                TaskRunControlRequest(
                    request_id="e2-s01-illegal-continue-after-stop",
                    expected_run_version=stopped.run_version,
                ),
                gateway,
            )
        stopped_final = await service.get_run(owner, second.id, gateway)
        assert stopped_final is not None and stopped_final.status is RunStatus.CANCELED
        assert executor.artifacts == artifacts_after_continue
        assert answered.id == created.id
    finally:
        for event in gateway.release.values():
            event.set()
        await service.shutdown()


@_run_async_test
async def test_s01_final_verifier_rejection_blocks_artifact_on_parallel_graph() -> None:
    service, _, task, executor, gateway, owner, inputs = await _prepare_pilot()
    ids = _ids(task)
    executor.reject_node_id = ids["produce_initiation_card"]
    try:
        created = await service.create_run(
            owner,
            task.id,
            TaskRunRequest(input="E2 DEMO/SYNTHETIC S01 negative final gate"),
            gateway,
        )
        assert created is not None
        final = await r7._drive(service, owner, created.id, gateway, inputs)
        assert final.status is RunStatus.FAILED
        assert final.artifacts == []
        assert executor.artifacts == {}
        rejected = next(
            item for item in final.node_runs
            if item.node_id == ids["produce_initiation_card"]
        )
        assert rejected.status is NodeRunStatus.FAILED
        verification = rejected.metadata.get("task_ai_verification")
        assert isinstance(verification, dict) and verification.get("passed") is False
    finally:
        await service.shutdown()
