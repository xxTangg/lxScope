# -*- coding: utf-8 -*-
"""Task planning ports and a deterministic development fallback."""

from typing import Protocol

from ._models import TaskContext, TaskPlanDraft, TaskPlanNode, TaskToolSchema


class TaskPlanner(Protocol):
    """Port that turns a user goal into an editable linear task draft."""

    async def plan(
        self,
        *,
        user_id: str,
        goal: str,
        context: TaskContext,
        current_title: str | None = None,
        tool_schemas: list[TaskToolSchema] | None = None,
    ) -> TaskPlanDraft:
        """Return a validated title, intent and typed linear node list."""


class PreviewTaskPlanner:
    """Deterministic planner used when no AgentScope context is selected."""

    async def plan(
        self,
        *,
        user_id: str,
        goal: str,
        context: TaskContext,
        current_title: str | None = None,
        tool_schemas: list[TaskToolSchema] | None = None,
    ) -> TaskPlanDraft:
        """Create a small editable plan without making a model request."""

        del user_id, context, tool_schemas
        title = (
            current_title.strip()
            if current_title and current_title.strip()
            else goal.strip()[:32]
        )
        return TaskPlanDraft(
            suggested_title=title or "未命名任务",
            intent=goal.strip(),
            nodes=[
                TaskPlanNode(
                    name="分析任务目标",
                    prompt=(
                        "分析任务目标，提取关键对象、约束条件和需要完成的结果。"
                    ),
                ),
                TaskPlanNode(
                    name="形成最终结果",
                    prompt=(
                        "结合任务目标和上一节点结果，整理出可直接使用的最终答案。"
                    ),
                ),
            ],
        )
