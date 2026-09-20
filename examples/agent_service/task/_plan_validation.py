"""Management-side validation for generated task plans.

This module deliberately depends only on the task models.  It does not import
or modify AgentScope internals; AgentScope remains responsible for model and
toolkit capabilities while the management layer validates the persisted task
graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jsonschema


@dataclass(frozen=True)
class PlanIssue:
    """A user/model-readable task-plan validation issue."""

    path: str
    message: str

    def as_text(self) -> str:
        return f"{self.path}: {self.message}"


def _dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if isinstance(value, dict):
        return value
    return {}


def _nodes(plan: Any) -> list[Any]:
    if isinstance(plan, dict):
        return list(plan.get("nodes") or [])
    return list(getattr(plan, "nodes", None) or [])


def _node_type(node: Any) -> str:
    data = _dump(node)
    value = data.get("type") or data.get("node_type") or getattr(node, "type", "")
    return str(value or "").strip().lower()


def _node_config(node: Any) -> dict[str, Any]:
    data = _dump(node)
    config = data.get("config")
    if isinstance(config, dict):
        return config
    value = getattr(node, "config", None)
    return _dump(value)

def _tool_arguments(node: Any) -> dict[str, Any]:
    config = _node_config(node)
    value = config.get("arguments")
    return value if isinstance(value, dict) else {}



def _tool_name(node: Any) -> str:
    config = _node_config(node)
    value = config.get("tool_name") or config.get("tool")
    if isinstance(value, dict):
        value = value.get("tool_name") or value.get("name")
    return str(value or "").strip()


def validate_task_plan(
    plan: Any,
    tool_schemas: list[Any] | None = None,
    *,
    require_final_agent: bool = True,
) -> list[PlanIssue]:
    """Validate management-task invariants without executing any tool.

    The validator is intentionally conservative.  It accepts both Pydantic
    models and dictionaries so it can be used before and after persistence.
    """

    issues: list[PlanIssue] = []
    nodes = _nodes(plan)
    schema_by_name: dict[str, dict[str, Any]] = {}
    for schema in (tool_schemas or []):
        data = _dump(schema)
        name = str(data.get("name") or data.get("tool_name") or "").strip()
        if name:
            schema_by_name[name] = data
    available = set(schema_by_name)

    if not nodes:
        return [PlanIssue("nodes", "计划至少需要一个节点")]

    seen_tool = False
    for index, node in enumerate(nodes):
        path = f"nodes[{index}]"
        node_type = _node_type(node)
        if not node_type:
            issues.append(PlanIssue(path, "节点类型不能为空"))
            continue

        if node_type in {"tool", "toolstep"} or node_type.endswith("toolstep"):
            seen_tool = True
            name = _tool_name(node)
            if not name:
                issues.append(PlanIssue(f"{path}.config.tool_name", "工具名称不能为空"))
            elif tool_schemas is not None and not available:
                issues.append(
                    PlanIssue(
                        f"{path}.config.tool_name",
                        "当前会话没有可用的 MCP/Tool，不能生成 ToolStep",
                    )
                )
            elif tool_schemas is None:
                pass
            elif name not in available:
                issues.append(PlanIssue(f"{path}.config.tool_name", f"工具不存在: {name}"))
            else:
                input_schema = schema_by_name[name].get("input_schema")
                if isinstance(input_schema, dict) and input_schema:
                    try:
                        jsonschema.validate(_tool_arguments(node), input_schema)
                    except jsonschema.ValidationError as error:
                        issues.append(
                            PlanIssue(f"{path}.config.arguments", f"参数不符合工具 schema: {error.message}")
                        )

        if node_type in {"agent", "agentstep"} or node_type.endswith("agentstep"):
            prompt = _node_config(node).get("prompt")
            prompt_text = str(prompt or "").strip()
            if not prompt_text:
                issues.append(PlanIssue(f"{path}.config.prompt", "AgentStep 提示词不能为空"))
            if prompt_text and any(
                marker in prompt_text.lower()
                for marker in ("调用工具", "使用工具", "use tool", "call tool")
            ) and not seen_tool:
                issues.append(
                    PlanIssue(
                        f"{path}.config.prompt",
                        "不能用 AgentStep 的文字代替真实 ToolStep",
                    )
                )

    if require_final_agent:
        last_type = _node_type(nodes[-1])
        if not (
            last_type in {"agent", "agentstep"}
            or last_type.endswith("agentstep")
        ):
            issues.append(PlanIssue("nodes[-1]", "最终节点应负责汇总结果的 AgentStep"))

    return issues


def format_plan_issues(issues: list[PlanIssue]) -> str:
    return "\n".join(f"- {issue.as_text()}" for issue in issues)
