# -*- coding: utf-8 -*-
"""AgentScope adapter for structured Task planning."""

import json
from typing import TYPE_CHECKING

from ._models import TaskContext, TaskPlanDraft, TaskToolSchema
from ._plan_validation import format_plan_issues, validate_task_plan

if TYPE_CHECKING:
    from agentscope.app._service._access import ResourceAccessService
    from agentscope.app.storage import StorageBase


_PLANNER_PROMPT = """你是一个通用任务流程规划器。

请根据用户的任务目的，生成一个可执行、可复用的线性 Task 流。
只允许返回结构化结果，不要输出解释性文字。

规划要求：
1. 任务名称简短明确，通常不超过 20 个汉字；
2. intent 用一句话概括任务真正要完成的业务目标；
3. 节点数量选择完成目标所需的最少数量，通常为 2 到 8 个；
4. 节点必须是线性顺序，不生成分支、循环、并行或人工节点；
5. 每个节点必须根据实际职责选择一种 Step：
   - AgentStep：理解、分析、总结、生成和分类；
   - ToolStep：调用当前可用的 MCP/API/工具获取外部数据或执行动作；
   - PythonStep：参数转换、确定性计算和结构化数据处理；
6. AgentStep 必须填写可直接执行的 Prompt；
7. ToolStep 必须从“可用工具清单”中选择准确的 tool_name，并填写 arguments；
8. PythonStep 必须填写可执行的 Python code，可使用 previous_output 变量；
9. 不要为了凑数量添加无意义节点；
10. 后一个节点必须能使用前一个节点的输出；
11. 最后一个节点负责整理可交付的最终结果；
12. 不要虚构用户没有提供的关键信息；不确定时在 Prompt 或参数中明确列出待确认信息；
13. 如果当前没有合适的工具，不要生成 ToolStep，改用 AgentStep 明确说明数据待获取。
14. AgentStep 不能用文字要求另一个 Agent “调用工具”；需要外部数据时必须生成真实 ToolStep；
15. ToolStep 的 tool_name 必须逐字匹配工具清单，不能猜测、改写或留空；
16. 最终 AgentStep 必须基于所有前序节点结果生成交付内容，而不是只描述下一步操作；
17. 需要多个外部数据时，按依赖顺序生成多个 ToolStep，再生成最终 AgentStep；

用户任务目的：
{goal}

用户已有任务名称（可能为空）：
{title}

当前会话可用工具清单：
{tools}
"""


class AgentScopeTaskPlanner:
    """Generate Task drafts with the selected AgentScope session model.

    The adapter reads only the selected session's model configuration and
    calls the public structured-output model capability. It does not write
    planning prompts into the user's chat history.
    """

    def __init__(
        self,
        *,
        storage: "StorageBase",
        resource_access_service: "ResourceAccessService",
    ) -> None:
        """Bind the existing application storage and access policy."""

        self._storage = storage
        self._access = resource_access_service

    async def plan(
        self,
        *,
        user_id: str,
        goal: str,
        context: TaskContext,
        current_title: str | None = None,
        tool_schemas: list[TaskToolSchema] | None = None,
    ) -> TaskPlanDraft:
        """Ask the selected session model for a structured Task draft."""

        if not context.agent_id or not context.session_id:
            raise RuntimeError(
                "AI task generation needs both agent_id and session_id."
            )

        session = await self._storage.get_session(
            user_id,
            context.agent_id,
            context.session_id,
        )
        if session is None or session.config.chat_model_config is None:
            raise RuntimeError(
                "The selected session does not have a configured chat model."
            )

        # This application-level factory is the same credential/model path
        # used by AgentScope's ChatService. It is imported lazily so the
        # standalone Task domain remains testable without AgentScope.
        from agentscope.app._service._model import get_model
        from agentscope.message import UserMsg

        model = await get_model(
            user_id,
            session.config.chat_model_config,
            self._access,
        )
        plan_prompt = _PLANNER_PROMPT.format(
            goal=goal.strip(),
            title=(current_title or "").strip() or "（空）",
            tools=_format_tools(tool_schemas or []),
        )
        response = await model.generate_structured_output(
            messages=[
                UserMsg(name="task-planner", content=plan_prompt),
            ],
            structured_model=TaskPlanDraft,
        )
        draft = TaskPlanDraft.model_validate(response.content)
        issues = validate_task_plan(draft, tool_schemas)
        if not issues:
            return draft

        repaired_prompt = (
            plan_prompt
            + "\n\n上一版计划未通过管理系统校验。请只修复以下问题，"
            "然后重新输出完整的结构化计划：\n"
            + format_plan_issues(issues)
        )
        repaired_response = await model.generate_structured_output(
            messages=[
                UserMsg(name="task-planner", content=repaired_prompt),
            ],
            structured_model=TaskPlanDraft,
        )
        repaired = TaskPlanDraft.model_validate(repaired_response.content)
        repaired_issues = validate_task_plan(repaired, tool_schemas)
        if repaired_issues:
            raise ValueError(
                "Task plan failed validation after one repair attempt:\n"
                + format_plan_issues(repaired_issues)
            )
        return repaired


def _format_tools(tool_schemas: list[TaskToolSchema]) -> str:
    """Format available tools without exposing unrelated runtime details."""

    if not tool_schemas:
        return "当前没有可用的 Tool/MCP 工具。"
    return json.dumps(
        [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "is_mcp": tool.is_mcp,
            }
            for tool in tool_schemas
        ],
        ensure_ascii=False,
        indent=2,
    )
