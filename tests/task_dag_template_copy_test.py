"""Task API acceptance for making official-template copies editable as DAGs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
	NodeRunRecord,
	NodeRunStatus,
	RunStatus,
	TaskGraph,
	TaskNode,
	TaskRunRecord,
	TaskToolSchema,
)
from examples.agent_service.task._dag import build_inputs_by_node_id
from examples.agent_service.task._router import task_router
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_registry import TemplateRegistry


SCENARIOS = {
    "s01_project_init": "s01-project-init.v2.json",
    "s02_project_planning": "s02-project-planning.v2.json",
    "s03_expert_matching": "s03-expert-matching.v3.json",
    "s04_meeting_preparation": "s04-meeting-preparation.v3.json",
    "s05_meeting_minutes": "s05-meeting-minutes.v3.json",
    "s06_opinion_processing": "s06-opinion-processing.v3.json",
    "s07_report_generation": "s07-report-generation.v3.json",
    "s08_archive_transfer": "s08-archive-transfer.v3.json",
    "s09_expense_settlement": "s09-expense-settlement.v3.json",
}
INPUT_ROOT = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "agent_service"
    / "task"
    / "docs"
    / "p7b-demo-data"
    / "inputs"
)


class _IsolatedExecutor:
    async def list_tools(self, context: Any) -> list[TaskToolSchema]:
        del context
        return []

    async def preflight_tool_permission(self, **_: Any) -> tuple[str, str]:
        return "allowed", "Isolated Task test permission fixture."

    def business_data_verified(self, capability_key: str) -> bool:
        del capability_key
        return True

    async def preflight_artifact_writer(self, *, context: Any) -> tuple[bool, str]:
        del context
        return True, "Isolated Task test artifact writer."

    async def write_artifact(self, *, path: str, data: bytes, context: Any) -> int:
        del path, context
        return len(data)

    async def read_artifact(self, *, path: str, context: Any) -> bytes:
        del path, context
        return b""

    async def execute_node(
        self,
        *,
        node: Any,
        previous_output: str,
        context: Any,
    ) -> StepExecutionResult:
        del context
        return StepExecutionResult(text=f"{node.name}: {previous_output}")


def _client(*, dag_enabled: bool = False) -> tuple[TestClient, TaskStore]:
    store = TaskStore()
    service = TaskService(
        store,
        agentscope_executor=_IsolatedExecutor(),  # type: ignore[arg-type]
        enable_dag_editing=True,
        enable_dag_scheduler=dag_enabled,
    )
    app = FastAPI()
    app.include_router(task_router)
    app.state.task_service = service
    return TestClient(app), store


def _inputs(template_id: str, template: Any) -> dict[str, Any]:
    sample_file = SCENARIOS[template_id]
    sample = json.loads((INPUT_ROOT / sample_file).read_text(encoding="utf-8"))[
        "inputs"
    ]
    result = {
        key: value
        for key, value in sample.items()
        if key in template.input_schema.properties
    }
    if "source_material_ids" in template.input_schema.properties:
        result["source_material_ids"] = ["r8-synthetic-source"]
    if template_id == "s04_meeting_preparation":
        result["meeting_date"] = "2025-11-06 09:00"
    assert set(template.input_schema.required) <= set(result)
    return result


def _instantiate(client: TestClient, template: Any) -> dict[str, Any]:
    response = client.post(
        f"/tasks/templates/{template.template_id}/instantiate",
        headers={"X-User-ID": "dag-template-owner"},
        json={
            "template_version": template.template_version,
            "inputs": _inputs(template.template_id, template),
            "source_context": {
                "agent_id": "isolated-agent",
                "session_id": "isolated-session",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _copy_dag(client: TestClient, source_id: str) -> dict[str, Any]:
    response = client.post(
        f"/tasks/{source_id}/custom-flow-copy",
        headers={"X-User-ID": "dag-template-owner"},
        json={"mode": "dag"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_all_current_official_templates_can_be_saved_as_provenance_preserving_dags(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    client, _store = _client(dag_enabled=False)
    registry = TemplateRegistry()
    current_templates = registry.list_templates()
    assert {item.template_id for item in current_templates} == set(SCENARIOS)

    with client:
        capabilities = client.get(
            "/tasks/capabilities",
            headers={"X-User-ID": "dag-template-owner"},
        )
        assert capabilities.status_code == 200
        assert capabilities.json() == {
            "editing_enabled": True,
            "execution_enabled": False,
            "sop_control_enabled": True,
            "sop_node_retry_enabled": True,
        }

        for template in current_templates:
            source = _instantiate(client, template)
            source_nodes = source["nodes"]
            frozen_run = TaskRunRecord(
                id=f"run-{template.template_id}",
                task_id=source["id"],
                user_id="dag-template-owner",
                task_revision=source["revision"],
                nodes=[TaskNode.model_validate(node) for node in source_nodes],
                node_runs=[
                    NodeRunRecord(
                        node_id=node["id"],
                        name=node["name"],
                        prompt=node["prompt"],
                        type=node["type"],
                        order=node["order"],
                        status=NodeRunStatus.RUNNING
                        if index == 0
                        else NodeRunStatus.PENDING,
                    )
                    for index, node in enumerate(source_nodes)
                ],
                status=RunStatus.RUNNING,
                engine="sop",
                source_template_id=template.template_id,
                source_template_version=template.template_version,
            )
            asyncio.run(_store.save_run(frozen_run))
            run_before = asyncio.run(
                _store.get_run("dag-template-owner", frozen_run.id)
            )
            assert run_before is not None
            copied = _copy_dag(client, source["id"])

            assert copied["id"] != source["id"]
            assert copied["workflow_mode"] == "custom_flow"
            assert copied["source_template_id"] == template.template_id
            assert copied["source_template_version"] == template.template_version
            assert copied["source_task_id"] == source["id"]
            assert copied["source_task_revision"] == source["revision"]
            assert copied["template_inputs"] == source["template_inputs"]
            assert copied["template_step_bindings"] != source["template_step_bindings"]
            assert set(copied["template_step_bindings"].values()) == {
                node["id"] for node in copied["nodes"]
            }
            assert copied["graph"]["schema_version"] == 1
            assert copied["graph"]["max_concurrency"] == 1
            assert copied["graph"]["input_bindings"] == {}
            assert copied["graph"]["edges"] == [
                {
                    "source_node_id": copied["nodes"][index]["id"],
                    "target_node_id": copied["nodes"][index + 1]["id"],
                }
                for index in range(len(copied["nodes"]) - 1)
            ]
            assert {
                node["id"] for node in source_nodes
            }.isdisjoint({node["id"] for node in copied["nodes"]})
            for source_node, copied_node in zip(source_nodes, copied["nodes"]):
                assert {
                    key: value
                    for key, value in source_node.items()
                    if key != "id"
                } == {
                    key: value
                    for key, value in copied_node.items()
                    if key != "id"
                }
            synthetic_node_runs = [
                NodeRunRecord(
                    node_id=node["id"],
                    name=node["name"],
                    prompt=node["prompt"],
                    type=node["type"],
                    order=index,
                    status=NodeRunStatus.SUCCEEDED,
                    output=f"synthetic-output-{index}",
                )
                for index, node in enumerate(copied["nodes"])
            ]
            for index, node in enumerate(copied["nodes"]):
                resolved = build_inputs_by_node_id(
                    TaskGraph.model_validate(copied["graph"]),
                    synthetic_node_runs,
                    node["id"],
                )
                expected = (
                    {}
                    if index == 0
                    else {
                        copied["nodes"][index - 1]["id"]: {
                            "output": f"synthetic-output-{index - 1}",
                            "result": None,
                        }
                    }
                )
                assert resolved == expected

            # The official SOP definition is unchanged and remains the only
            # object used by existing Runs.
            reloaded_source = client.get(
                f"/tasks/{source['id']}",
                headers={"X-User-ID": "dag-template-owner"},
            ).json()
            assert reloaded_source["graph"] is None
            assert reloaded_source["nodes"] == source_nodes
            assert reloaded_source["source_template_id"] == template.template_id
            run_after = asyncio.run(
                _store.get_run("dag-template-owner", frozen_run.id)
            )
            assert run_after is not None
            assert run_after.model_dump(mode="json") == run_before.model_dump(
                mode="json"
            )

            # DAG edits remain allowed even though the execution switch is off.
            patch = client.patch(
                f"/tasks/{copied['id']}",
                headers={"X-User-ID": "dag-template-owner"},
                json={"nodes": copied["nodes"], "graph": copied["graph"]},
            )
            assert patch.status_code == 200, patch.text
            reopened = client.get(
                f"/tasks/{copied['id']}",
                headers={"X-User-ID": "dag-template-owner"},
            )
            assert reopened.status_code == 200
            assert reopened.json()["graph"] == copied["graph"]

            # Every scenario accepts an explicit, non-cyclic shortcut edge;
            # invalid cycles, bindings, and resource capacities are refused.
            edited_graph = json.loads(json.dumps(copied["graph"]))
            first_id = copied["nodes"][0]["id"]
            last_id = copied["nodes"][-1]["id"]
            shortcut = {
                "source_node_id": first_id,
                "target_node_id": last_id,
            }
            if shortcut not in edited_graph["edges"]:
                edited_graph["edges"].append(shortcut)
            edited_graph["max_concurrency"] = 2
            edge_update = client.patch(
                f"/tasks/{copied['id']}",
                headers={"X-User-ID": "dag-template-owner"},
                json={"graph": edited_graph},
            )
            assert edge_update.status_code == 200, edge_update.text
            assert client.get(
                f"/tasks/{copied['id']}",
                headers={"X-User-ID": "dag-template-owner"},
            ).json()["graph"] == edited_graph

            cyclic_graph = json.loads(json.dumps(edited_graph))
            cyclic_graph["edges"].append(
                {"source_node_id": last_id, "target_node_id": first_id}
            )
            cycle_rejected = client.patch(
                f"/tasks/{copied['id']}",
                headers={"X-User-ID": "dag-template-owner"},
                json={"graph": cyclic_graph},
            )
            assert cycle_rejected.status_code == 422, cycle_rejected.text

            invalid_binding = json.loads(json.dumps(edited_graph))
            invalid_binding["input_bindings"] = {
                first_id: {
                    "invalid_source": {
                        "source_node_id": last_id,
                        "source_value": "output",
                    }
                }
            }
            binding_rejected = client.patch(
                f"/tasks/{copied['id']}",
                headers={"X-User-ID": "dag-template-owner"},
                json={"graph": invalid_binding},
            )
            assert binding_rejected.status_code == 422, binding_rejected.text

            resource_nodes = json.loads(json.dumps(copied["nodes"]))
            resource_nodes[0]["resource_requirements"] = {
                "isolated_template_test": 2
            }
            invalid_resource_graph = json.loads(json.dumps(edited_graph))
            invalid_resource_graph["resource_limits"] = {
                "isolated_template_test": 1
            }
            resource_rejected = client.patch(
                f"/tasks/{copied['id']}",
                headers={"X-User-ID": "dag-template-owner"},
                json={"nodes": resource_nodes, "graph": invalid_resource_graph},
            )
            assert resource_rejected.status_code == 422, resource_rejected.text
            assert client.get(
                f"/tasks/{copied['id']}",
                headers={"X-User-ID": "dag-template-owner"},
            ).json()["graph"] == edited_graph

            preflight = client.post(
                f"/tasks/{copied['id']}/preflight",
                headers={"X-User-ID": "dag-template-owner"},
                json={"input": "合成验收输入"},
            )
            assert preflight.status_code == 200, preflight.text
            assert preflight.json()["ready"] is False
            issue_paths = {issue["path"] for issue in preflight.json()["issues"]}
            assert "graph.execution_enabled" in issue_paths
            assert "graph.scenario_execution" in issue_paths


def test_template_dag_graph_edits_are_validated_and_persisted_without_touching_source(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    client, _store = _client(dag_enabled=False)
    template = TemplateRegistry().get_current_template("s01_project_init")
    assert template is not None

    with client:
        source = _instantiate(client, template)
        copied = _copy_dag(client, source["id"])
        nodes = copied["nodes"]
        assert len(nodes) < 8
        branch = {
            "id": "custom-branch-node",
            "name": "自定义并行补充",
            "prompt": "基于输入编写补充摘要。",
            "type": "agent",
            "config": {
                "type": "agent",
                "prompt": "基于输入编写补充摘要。",
            },
            "order": len(nodes) - 1,
            "acceptance_criteria": "返回合成摘要。",
            "max_attempts": 1,
            "resource_requirements": {},
        }
        edited_nodes = [*nodes[:-1], branch, nodes[-1]]
        for index, node in enumerate(edited_nodes):
            node["order"] = index
        graph = json.loads(json.dumps(copied["graph"]))
        first_id = nodes[0]["id"]
        last_id = nodes[-1]["id"]
        graph["edges"].extend(
            [
                {"source_node_id": first_id, "target_node_id": branch["id"]},
                {"source_node_id": branch["id"], "target_node_id": last_id},
            ]
        )
        graph["max_concurrency"] = 2

        validation = client.post(
            "/tasks/validate",
            headers={"X-User-ID": "dag-template-owner"},
            json={"task_id": copied["id"], "nodes": edited_nodes, "graph": graph},
        )
        assert validation.status_code == 200, validation.text
        assert validation.json()["valid"] is True, validation.text
        saved = client.patch(
            f"/tasks/{copied['id']}",
            headers={"X-User-ID": "dag-template-owner"},
            json={"nodes": edited_nodes, "graph": graph},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["graph"]["edges"] == graph["edges"]
        normalized_saved = saved.json()
        reopened = client.get(
            f"/tasks/{copied['id']}",
            headers={"X-User-ID": "dag-template-owner"},
        ).json()
        assert reopened["nodes"] == normalized_saved["nodes"]
        assert reopened["graph"] == graph

        # Cycles are rejected by the same Task graph validator and are not
        # persisted. The copied Task's source lineage is never removed.
        cyclic = json.loads(json.dumps(graph))
        cyclic["edges"].append(
            {"source_node_id": last_id, "target_node_id": first_id}
        )
        rejected = client.patch(
            f"/tasks/{copied['id']}",
            headers={"X-User-ID": "dag-template-owner"},
            json={"nodes": edited_nodes, "graph": cyclic},
        )
        assert rejected.status_code == 422, rejected.text
        assert client.get(
            f"/tasks/{copied['id']}",
            headers={"X-User-ID": "dag-template-owner"},
        ).json()["graph"] == graph

        # A retained template-step handoff may not be removed, and invalid
        # bindings or resource declarations fail before persistence.
        broken_handoff = json.loads(json.dumps(graph))
        retained_ids = list(copied["template_step_bindings"].values())
        first_pair = (retained_ids[0], retained_ids[1])
        broken_handoff["edges"] = [
            edge
            for edge in broken_handoff["edges"]
            if (edge["source_node_id"], edge["target_node_id"]) != first_pair
        ]
        rejected_handoff = client.patch(
            f"/tasks/{copied['id']}",
            headers={"X-User-ID": "dag-template-owner"},
            json={"nodes": edited_nodes, "graph": broken_handoff},
        )
        assert rejected_handoff.status_code == 422, rejected_handoff.text

        invalid_binding = json.loads(json.dumps(graph))
        invalid_binding["input_bindings"] = {
            first_id: {
                "not_an_ancestor": {
                    "source_node_id": last_id,
                    "source_value": "output",
                }
            }
        }
        rejected_binding = client.patch(
            f"/tasks/{copied['id']}",
            headers={"X-User-ID": "dag-template-owner"},
            json={"nodes": edited_nodes, "graph": invalid_binding},
        )
        assert rejected_binding.status_code == 422, rejected_binding.text

        resource_nodes = json.loads(json.dumps(edited_nodes))
        resource_nodes[-2]["resource_requirements"] = {"isolated_tool_slot": 2}
        invalid_resource = json.loads(json.dumps(graph))
        invalid_resource["resource_limits"] = {"isolated_tool_slot": 1}
        rejected_resource = client.patch(
            f"/tasks/{copied['id']}",
            headers={"X-User-ID": "dag-template-owner"},
            json={"nodes": resource_nodes, "graph": invalid_resource},
        )
        assert rejected_resource.status_code == 422, rejected_resource.text
        assert client.get(
            f"/tasks/{copied['id']}",
            headers={"X-User-ID": "dag-template-owner"},
        ).json()["graph"] == graph
        assert client.get(
            f"/tasks/{source['id']}",
            headers={"X-User-ID": "dag-template-owner"},
        ).json()["graph"] is None


def test_template_derived_dag_run_remains_server_blocked_and_sop_copy_default_is_compatible(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    client, _store = _client(dag_enabled=True)
    template = TemplateRegistry().get_current_template("s02_project_planning")
    assert template is not None

    with client:
        source = _instantiate(client, template)
        legacy_copy_response = client.post(
            f"/tasks/{source['id']}/custom-flow-copy",
            headers={"X-User-ID": "dag-template-owner"},
        )
        assert legacy_copy_response.status_code == 201, legacy_copy_response.text
        legacy_copy = legacy_copy_response.json()
        assert legacy_copy["workflow_mode"] == "custom_flow"
        assert legacy_copy["graph"] is None

        dag_copy = _copy_dag(client, source["id"])
        preflight = client.post(
            f"/tasks/{dag_copy['id']}/preflight",
            headers={"X-User-ID": "dag-template-owner"},
            json={"input": "合成 S02 输入"},
        )
        assert preflight.status_code == 200
        assert preflight.json()["ready"] is False
        assert any(
            issue["path"] == "graph.scenario_execution"
            for issue in preflight.json()["issues"]
        )

        blocked = client.post(
            f"/tasks/{dag_copy['id']}/runs",
            headers={"X-User-ID": "dag-template-owner"},
            json={"input": "不得启动版本化场景 DAG"},
        )
        assert blocked.status_code == 409, blocked.text
        assert "versioned scenario Tasks" in blocked.text

        # Official template instances remain locked against graph edits.
        source_graph = {
            "schema_version": 1,
            "edges": [],
            "input_bindings": {},
            "max_concurrency": 1,
            "resource_limits": {},
        }
        source_edit = client.patch(
            f"/tasks/{source['id']}",
            headers={"X-User-ID": "dag-template-owner"},
            json={"graph": source_graph},
        )
        assert source_edit.status_code == 409, source_edit.text

