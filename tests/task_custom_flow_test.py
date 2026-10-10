"""Task custom-flow copy, editing, provenance, and safety tests."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from examples.agent_service.task._capability_contracts import capability_contract
from examples.agent_service.task._models import (
    TaskPlanDraft,
    TaskPlanNode,
    TaskRecord,
    TaskRunRecord,
    TaskToolSchema,
)
from examples.agent_service.task._router import task_router
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_registry import TemplateRegistry


class _TestPlanner:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, **_: Any) -> TaskPlanDraft:
        self.calls += 1
        return TaskPlanDraft(
            suggested_title="Planner 生成任务",
            intent="测试自由任务 Planner 路径",
            nodes=[
                TaskPlanNode(
                    name="处理输入",
                    prompt="整理用户输入并给出结果。",
                ),
            ],
        )


class _TestExecutor:
    def __init__(self) -> None:
        self.tools: list[TaskToolSchema] = []

    async def list_tools(self, context: Any) -> list[TaskToolSchema]:
        del context
        return self.tools

    async def preflight_tool_permission(self, **_: Any) -> tuple[str, str]:
        return "allowed", "当前用户权限检查通过。"

    def business_data_verified(self, capability_key: str) -> bool:
        del capability_key
        return True


def _client(
    planner: _TestPlanner | None = None,
    executor: _TestExecutor | None = None,
) -> tuple[TestClient, TaskStore, TaskService, _TestPlanner, _TestExecutor]:
    store = TaskStore()
    active_planner = planner or _TestPlanner()
    active_executor = executor or _TestExecutor()
    service = TaskService(
        store,
        planner=active_planner,  # type: ignore[arg-type]
        agentscope_executor=active_executor,  # type: ignore[arg-type]
    )
    app = FastAPI()
    app.include_router(task_router)
    app.state.task_service = service
    return TestClient(app), store, service, active_planner, active_executor


def _instantiate_s01(client: TestClient, user_id: str = "owner") -> dict[str, Any]:
    response = client.post(
        "/tasks/templates/s01_project_init/instantiate",
        headers={"X-User-ID": user_id},
        json={
            "template_version": "1.0.0",
            "inputs": {
                "project_name": "测试项目",
                "project_brief": "范围明确的工程评审项目。",
            },
            "source_context": {
                "agent_id": "test-agent",
                "session_id": "test-session",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _custom_copy(client: TestClient, task_id: str, user_id: str = "owner") -> dict[str, Any]:
    response = client.post(
        f"/tasks/{task_id}/custom-flow-copy",
        headers={"X-User-ID": user_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _patch_nodes(
    client: TestClient,
    task_id: str,
    nodes: list[dict[str, Any]],
    user_id: str = "owner",
) -> Any:
    return client.patch(
        f"/tasks/{task_id}",
        headers={"X-User-ID": user_id},
        json={"nodes": nodes},
    )


def test_custom_copy_is_private_deep_and_keeps_planner_out_of_template_flow(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    client, _store, _service, planner, _executor = _client()
    with client:
        source = _instantiate_s01(client)
        source_nodes = source["nodes"]
        assert source["workflow_mode"] == "template_instance"

        copied = _custom_copy(client, source["id"])

        assert copied["id"] != source["id"]
        assert copied["workflow_mode"] == "custom_flow"
        assert copied["source_task_id"] == source["id"]
        assert copied["source_task_revision"] == source["revision"]
        assert copied["source_template_id"] == source["source_template_id"]
        assert copied["source_template_version"] == source["source_template_version"]
        assert copied["template_inputs"] == source["template_inputs"]
        assert copied["template_step_bindings"] != source["template_step_bindings"]
        assert {node["id"] for node in copied["nodes"]}.isdisjoint(
            {node["id"] for node in source_nodes}
        )
        assert copied["nodes"] == [
            {**node, "id": copied["template_step_bindings"][key]}
            for key, node in zip(source["template_step_bindings"], source_nodes)
        ]

        # A standard template Task remains locked and a custom copy cannot
        # accidentally be sent through the Planner endpoint.
        changed = [dict(node) for node in source_nodes]
        changed.append(
            {
                "id": "extra-node",
                "name": "新增步骤",
                "prompt": "整理结果。",
                "type": "agent",
                "config": {"type": "agent", "prompt": "整理结果。"},
                "order": len(changed),
            },
        )
        assert _patch_nodes(client, source["id"], changed).status_code == 422
        assert client.get(
            f"/tasks/{source['id']}", headers={"X-User-ID": "owner"}
        ).json()["nodes"] == source_nodes
        assert client.get(
            f"/tasks/{copied['id']}", headers={"X-User-ID": "other"}
        ).status_code == 404
        assert client.post(
            f"/tasks/{source['id']}/custom-flow-copy",
            headers={"X-User-ID": "other"},
        ).status_code == 404
        assert client.post(
            f"/tasks/{copied['id']}/generate",
            headers={"X-User-ID": "owner"},
            json={},
        ).status_code == 409
        assert planner.calls == 0

        free = client.post(
            "/tasks/",
            headers={"X-User-ID": "owner"},
            json={"goal": "自由任务 Planner 回归"},
        )
        assert free.status_code == 201, free.text
        assert free.json()["workflow_mode"] == "free_ai_planner"
        generated = client.post(
            f"/tasks/{free.json()['id']}/generate",
            headers={"X-User-ID": "owner"},
            json={},
        )
        assert generated.status_code == 200, generated.text
        assert generated.json()["nodes"]
        assert planner.calls == 1


def test_custom_flow_can_add_delete_change_type_and_reorder_nodes(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    client, _store, _service, _planner, _executor = _client()
    with client:
        source = _instantiate_s01(client)
        copied = _custom_copy(client, source["id"])
        nodes = [dict(node) for node in copied["nodes"]]

        # Reorder ordinary nodes while retaining each node ID.
        original_ids = [node["id"] for node in nodes]
        nodes[0], nodes[1] = nodes[1], nodes[0]
        response = _patch_nodes(client, copied["id"], nodes)
        assert response.status_code == 200, response.text
        assert [node["id"] for node in response.json()["nodes"]] == [
            original_ids[1],
            original_ids[0],
            *original_ids[2:],
        ]

        nodes = response.json()["nodes"]
        agent_node = next(node for node in nodes if node["type"] == "agent")
        agent_node["type"] = "python"
        agent_node["prompt"] = ""
        agent_node["config"] = {
            "type": "python",
            "code": "print(previous_output)",
            "timeout_seconds": 30,
        }
        response = _patch_nodes(client, copied["id"], nodes)
        assert response.status_code == 200, response.text
        assert any(node["type"] == "python" for node in response.json()["nodes"])

        nodes = response.json()["nodes"]
        nodes.append(
            {
                "id": "custom-added-node",
                "name": "自定义汇总",
                "prompt": "按已有证据整理结论。",
                "type": "agent",
                "config": {
                    "type": "agent",
                    "prompt": "按已有证据整理结论。",
                },
                "order": len(nodes),
            },
        )
        response = _patch_nodes(client, copied["id"], nodes)
        assert response.status_code == 200, response.text
        assert len(response.json()["nodes"]) == 8

        nodes = response.json()["nodes"]
        nodes = [node for node in nodes if node["id"] != "custom-added-node"]
        response = _patch_nodes(client, copied["id"], nodes)
        assert response.status_code == 200, response.text
        assert len(response.json()["nodes"]) == 7


def test_custom_flow_rejects_broken_dependencies_and_required_tool_edits(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    client, _store, _service, _planner, _executor = _client()
    with client:
        source = _instantiate_s01(client)
        copied = _custom_copy(client, source["id"])
        nodes = [dict(node) for node in copied["nodes"]]

        agent_node = next(node for node in nodes if node["type"] == "agent")
        agent_node["prompt"] = "使用 {{answers.never_asked}}。"
        agent_node["config"] = {
            "type": "agent",
            "prompt": agent_node["prompt"],
        }
        assert _patch_nodes(client, copied["id"], nodes).status_code == 422

        nodes = [dict(node) for node in copied["nodes"]]
        nodes.append(
            {
                "id": "",
                "name": "无效 ID",
                "prompt": "此节点不能保存。",
                "type": "agent",
                "config": {
                    "type": "agent",
                    "prompt": "此节点不能保存。",
                },
                "order": len(nodes),
            },
        )
        assert _patch_nodes(client, copied["id"], nodes).status_code == 422

        nodes = [dict(node) for node in copied["nodes"]]
        input_nodes = [node for node in nodes if node["type"] == "input"]
        input_node = input_nodes[-1]
        input_node["config"]["questions"][0]["key"] = "project_name"
        assert _patch_nodes(client, copied["id"], nodes).status_code == 422

        nodes = [dict(node) for node in copied["nodes"]]
        final_node = nodes[-1]
        final_node["type"] = "input"
        final_node["prompt"] = ""
        final_node["config"] = {
            "type": "input",
            "reason": "补充结果。",
            "questions": [
                {
                    "key": "final_input",
                    "label": "补充结果",
                    "required": True,
                    "options": [],
                },
            ],
        }
        final_node["artifact"] = None
        final_node["output_schema"] = None
        assert _patch_nodes(client, copied["id"], nodes).status_code == 422

        nodes = [dict(node) for node in copied["nodes"]]
        required_tool = next(
            node
            for node in nodes
            if node["config"].get("tool_name") == "project.similar_search"
        )
        nodes.remove(required_tool)
        assert _patch_nodes(client, copied["id"], nodes).status_code == 422

        nodes = [dict(node) for node in copied["nodes"]]
        required_tool = next(
            node
            for node in nodes
            if node["config"].get("tool_name") == "project.similar_search"
        )
        required_tool["type"] = "agent"
        required_tool["prompt"] = "替代业务规则检查。"
        required_tool["config"] = {
            "type": "agent",
            "prompt": required_tool["prompt"],
        }
        assert _patch_nodes(client, copied["id"], nodes).status_code == 422

        nodes = [dict(node) for node in copied["nodes"]]
        first_tool_index = next(
            index
            for index, node in enumerate(nodes)
            if node["config"].get("tool_name") == "project.similar_search"
        )
        second_tool_index = next(
            index
            for index, node in enumerate(nodes)
            if node["config"].get("tool_name") == "project.rule_check"
        )
        nodes[first_tool_index], nodes[second_tool_index] = (
            nodes[second_tool_index],
            nodes[first_tool_index],
        )
        assert _patch_nodes(client, copied["id"], nodes).status_code == 422

        nodes = [dict(node) for node in copied["nodes"]]
        nodes.append(
            {
                "id": "custom-8",
                "name": "第八步",
                "prompt": "汇总。",
                "type": "agent",
                "config": {"type": "agent", "prompt": "汇总。"},
                "order": len(nodes),
            },
        )
        nodes.append(
            {
                "id": "custom-9",
                "name": "第九步",
                "prompt": "继续汇总。",
                "type": "agent",
                "config": {"type": "agent", "prompt": "继续汇总。"},
                "order": len(nodes),
            },
        )
        assert _patch_nodes(client, copied["id"], nodes).status_code == 422


def test_custom_flow_preflight_still_blocks_missing_tools_and_side_effects(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    executor = _TestExecutor()
    client, store, _service, _planner, executor = _client(executor=executor)
    with client:
        source = _instantiate_s01(client)
        copied = _custom_copy(client, source["id"])

        missing = client.post(
            f"/tasks/{copied['id']}/preflight",
            headers={"X-User-ID": "owner"},
            json={"input": "检查测试项目"},
        )
        assert missing.status_code == 200, missing.text
        assert missing.json()["ready"] is False
        assert "project.similar_search" in {
            check["capability_key"]
            for check in missing.json()["capability_checks"]
            if check["status"] == "missing"
        }

        template = TemplateRegistry().get_template("s01_project_init")
        assert template is not None
        required_tool_steps = [
            step
            for step in template.steps
            if step.kind == "tool"
        ]
        executor.tools = [
            TaskToolSchema(
                name=step.capability_key,
                description="Task scoped validation fixture",
                input_schema=(
                    (
                        capability_contract(step.capability_key).input_schema
                        or {"type": "object", "additionalProperties": True}
                    )
                    if capability_contract(step.capability_key)
                    else {"type": "object", "additionalProperties": True}
                ),
                is_read_only=step.capability_key != "project.similar_search",
            )
            for step in required_tool_steps
        ]
        blocked = client.post(
            f"/tasks/{copied['id']}/preflight",
            headers={"X-User-ID": "owner"},
            json={"input": "检查测试项目"},
        )
        assert blocked.status_code == 200, blocked.text
        checks = blocked.json()["capability_checks"]
        similar_check = next(
            check
            for check in checks
            if check["capability_key"] == "project.similar_search"
        )
        assert similar_check["status"] == "unsupported", similar_check
        run = client.post(
            f"/tasks/{copied['id']}/runs",
            headers={"X-User-ID": "owner"},
            json={"input": "检查测试项目"},
        )
        assert run.status_code == 422
        assert asyncio.run(store.list_runs("owner", copied["id"])) == []


def test_custom_run_snapshot_retains_source_provenance(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_CAPABILITY_TOOL_MAP", raising=False)
    executor = _TestExecutor()
    client, _store, _service, _planner, executor = _client(executor=executor)
    with client:
        template = TemplateRegistry().get_template("s06_opinion_processing")
        assert template is not None
        executor.tools = [
            TaskToolSchema(
                name=step.capability_key,
                description="Task scoped provenance fixture",
                input_schema={"type": "object", "additionalProperties": True},
                is_read_only=True,
            )
            for step in template.steps
            if step.kind == "tool"
        ]
        source_response = client.post(
            "/tasks/templates/s06_opinion_processing/instantiate",
            headers={"X-User-ID": "owner"},
            json={
                "template_version": "1.0.0",
                "inputs": {
                    "project_name": "测试项目",
                    "image_asset_ids": ["asset-1"],
                },
                "source_context": {
                    "agent_id": "test-agent",
                    "session_id": "test-session",
                },
            },
        )
        assert source_response.status_code == 201, source_response.text
        source = source_response.json()
        copied = _custom_copy(client, source["id"])

        response = client.post(
            f"/tasks/{copied['id']}/runs",
            headers={"X-User-ID": "owner"},
            json={"input": "测试运行快照来源"},
        )
        assert response.status_code == 202, response.text
        run = response.json()
        assert run["workflow_mode"] == "custom_flow"
        assert run["source_template_id"] == "s06_opinion_processing"
        assert run["source_template_version"] == "1.0.0"
        assert run["source_task_id"] == source["id"]
        assert run["source_task_revision"] == source["revision"]


def test_old_task_and_run_records_load_without_custom_provenance_fields() -> None:
    old_task = TaskRecord.model_validate({"user_id": "owner"})
    old_run = TaskRunRecord.model_validate(
        {
            "task_id": "old-task",
            "user_id": "owner",
            "task_revision": 1,
            "nodes": [],
            "node_runs": [],
        },
    )

    assert old_task.workflow_mode is None
    assert old_task.source_task_id is None
    assert old_run.workflow_mode is None
    assert old_run.source_template_id is None
