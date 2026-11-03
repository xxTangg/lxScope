# -*- coding: utf-8 -*-
"""Per-scenario SOP node retry checks using local synthetic Task adapters.

These tests exercise the TaskService retry and single-step boundaries against
the real, versioned S01-S09 Task node snapshots. They do not claim that the
business integrations, knowledge sources, model verifier, or artifact delivery
gates are production-ready; those remain covered by their own fail-closed
preflight checks and require isolated service-backed acceptance.
"""

from __future__ import annotations

import asyncio
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from agentscope.message import UserMsg

from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    StepType,
    StepVerdict,
    TaskRunControlRequest,
    TaskRunRecord,
)
from examples.agent_service.task._service import (
    TaskService,
    _node_snapshot_fingerprint,
)
from examples.agent_service.task._sop import TaskSOPRunState, TaskStepState
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_models import (
    TemplateInputProperty,
    TemplateInstantiateRequest,
)
from examples.agent_service.task._template_registry import TemplateRegistry
from examples.agent_service.task._template_service import TaskTemplateService


def _synthetic_value(prop: TemplateInputProperty, key: str):
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
    raise AssertionError(f"Unsupported input type {prop.type!r}.")


class _SyntheticScenarioExecutor:
    """Local Agent adapter; it never calls a model or external Tool."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_node(self, *, node, previous_output, context):
        del previous_output, context
        self.calls.append(node.id)
        return StepExecutionResult(
            text=f"SYNTHETIC-RETRY-OUTPUT-{node.id}",
            result={"synthetic": True, "node_id": node.id},
        )

    async def verify_node(self, **kwargs):
        del kwargs
        return StepVerdict(passed=True, message="Synthetic verifier passed.")


class TaskSOPTemplateRetryTest(IsolatedAsyncioTestCase):
    async def test_each_s01_to_s09_template_retries_and_steps_one_agent_node(self):
        registry = TemplateRegistry()
        store = TaskStore()
        executor = _SyntheticScenarioExecutor()
        service = TaskService(store, preview_executor=executor)
        templates = registry.list_templates()
        expected_ids = {
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
        self.assertEqual({item.template_id for item in templates}, expected_ids)

        try:
            for index, template in enumerate(templates):
                with self.subTest(template=template.template_id):
                    owner = f"synthetic-{template.template_id}-{index}"
                    template_service = TaskTemplateService(registry, service)
                    inputs = {
                        key: _synthetic_value(prop, key)
                        for key, prop in template.input_schema.properties.items()
                    }
                    task = await template_service.instantiate(
                        owner,
                        template.template_id,
                        TemplateInstantiateRequest(
                            template_version=template.template_version,
                            inputs=inputs,
                        ),
                    )
                    self.assertEqual(task.source_template_id, template.template_id)
                    self.assertEqual(task.workflow_mode.value, "template_instance")
                    self.assertIsNone(task.graph)
                    self.assertTrue(task.nodes)

                    selected = next(
                        node for node in task.nodes if node.type is StepType.AGENT
                    )
                    run_id = uuid4().hex
                    node_runs = [
                        NodeRunRecord(
                            node_id=node.id,
                            name=node.name,
                            prompt=node.prompt,
                            type=node.type,
                            order=node.order,
                            status=(
                                NodeRunStatus.SUCCEEDED
                                if node.order < selected.order
                                else NodeRunStatus.FAILED
                                if node.id == selected.id
                                else NodeRunStatus.PENDING
                            ),
                            output=(
                                f"SYNTHETIC-PREFIX-{node.id}"
                                if node.order < selected.order
                                else ""
                            ),
                            attempt=(
                                1
                                if node.order <= selected.order
                                else 0
                            ),
                            error=(
                                "Synthetic failure before retry."
                                if node.id == selected.id
                                else None
                            ),
                        )
                        for node in task.nodes
                    ]
                    initial_state = TaskSOPRunState(
                        id=run_id,
                        inputs=[
                            UserMsg(name="user", content="SYNTHETIC INPUT")
                        ],
                        steps=[TaskStepState() for _ in task.nodes],
                        task_node_ids=[node.id for node in task.nodes],
                    )
                    run = TaskRunRecord(
                        id=run_id,
                        task_id=task.id,
                        user_id=owner,
                        task_revision=task.revision,
                        nodes=task.nodes,
                        node_runs=node_runs,
                        input="SYNTHETIC INPUT",
                        status=RunStatus.FAILED,
                        engine="sop",
                        workflow_mode=task.workflow_mode,
                        source_template_id=task.source_template_id,
                        source_template_version=task.source_template_version,
                        node_snapshot_fingerprint=_node_snapshot_fingerprint(
                            task.nodes
                        ),
                        sop_state=initial_state.model_dump(mode="json"),
                    )
                    await store.save_run(run)
                    service._run_users[run.id] = owner  # pylint: disable=protected-access

                    retry_request = TaskRunControlRequest(
                        request_id=f"retry-{template.template_id}",
                        expected_run_version=run.run_version,
                    )
                    retried = await service.retry_node(
                        owner,
                        run.id,
                        selected.id,
                        retry_request,
                    )
                    assert retried is not None
                    self.assertEqual(retried.id, run.id)
                    self.assertEqual(retried.status, RunStatus.QUEUED)
                    self.assertEqual(retried.control_mode, "paused")
                    self.assertTrue(
                        all(
                            retried.node_runs[node.order].status
                            is NodeRunStatus.SUCCEEDED
                            for node in task.nodes[: selected.order]
                        )
                    )
                    self.assertEqual(
                        retried.node_runs[selected.order].status,
                        NodeRunStatus.PENDING,
                    )
                    self.assertEqual(
                        retried.node_runs[selected.order].attempt_history[-1].status,
                        "failed",
                    )

                    duplicate = await service.retry_node(
                        owner,
                        run.id,
                        selected.id,
                        retry_request,
                    )
                    assert duplicate is not None
                    self.assertEqual(duplicate.run_version, retried.run_version)

                    step_request = TaskRunControlRequest(
                        request_id=f"step-{template.template_id}",
                        expected_run_version=retried.run_version,
                    )
                    stepped = await service.step_run(owner, run.id, step_request)
                    assert stepped is not None
                    active = service._active_runs[run.id]  # pylint: disable=protected-access
                    await active
                    checkpoint = await store.get_run(owner, run.id)
                    assert checkpoint is not None
                    self.assertEqual(checkpoint.status, RunStatus.QUEUED)
                    self.assertEqual(checkpoint.control_mode, "paused")
                    self.assertEqual(
                        checkpoint.node_runs[selected.order].status,
                        NodeRunStatus.SUCCEEDED,
                    )
                    self.assertTrue(
                        all(
                            item.status is NodeRunStatus.PENDING
                            for item in checkpoint.node_runs[selected.order + 1 :]
                        )
                    )
                    self.assertEqual(executor.calls[-1], selected.id)
                    self.assertEqual(
                        len(executor.calls),
                        index + 1,
                        "A single SOP Step must dispatch exactly one Agent node.",
                    )
        finally:
            await service.shutdown()


if __name__ == "__main__":
    import unittest

    unittest.main()
