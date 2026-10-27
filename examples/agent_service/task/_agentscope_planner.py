# -*- coding: utf-8 -*-
"""AgentScope adapter for structured Task planning."""

import json
from typing import TYPE_CHECKING

from ._models import TaskContext, TaskPlanDraft, TaskToolSchema
from ._plan_validation import format_plan_issues, validate_task_plan
from ._planning_contract import (
    TaskPlanningCandidate,
    TaskPlanningDecision,
    TaskPlanningRuntimePolicy,
    TaskPlanningTemplateContract,
)
from ._planning_decision import decide_task_plan

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
4. 节点必须是线性顺序，不生成分支、循环或并行；允许 InputStep 收集用户信息；
5. 每个节点必须根据实际职责选择一种 Step：
   - AgentStep：理解、分析、总结、生成和分类；
   - ToolStep：调用当前可用的 MCP/API/工具获取外部数据或执行动作；
   - PythonStep：参数转换、确定性计算和结构化数据处理；
   - InputStep：收集用户需要提供的参数、资料或选择，config.questions 列出 key、label、required、options；
6. AgentStep 必须填写可直接执行的 Prompt；
7. ToolStep 必须从“可用工具清单”中选择准确的 tool_name，并填写 arguments；
8. PythonStep 必须填写可执行的 Python code，可使用 previous_output 变量；
9. 不要为了凑数量添加无意义节点；
10. 后一个节点必须能使用前一个节点的输出；
11. 最后一个节点负责整理可交付的最终结果；
12. 缺少用户关键信息时，在依赖步骤之前添加 InputStep；不能用 AgentStep 输出待确认模板后继续执行；
13. 没有合适的外部数据工具时，使用 InputStep 请用户提供必要资料，不能把待获取数据当作成功结果；
14. AgentStep 不能用文字要求另一个 Agent “调用工具”；需要外部数据时必须生成真实 ToolStep；
15. ToolStep 的 tool_name 必须逐字匹配工具清单，不能猜测、改写或留空；
16. 最终 AgentStep 必须基于所有前序节点结果生成交付内容，而不是只描述下一步操作；
17. 需要多个外部数据时，按依赖顺序生成多个 ToolStep，再生成最终 AgentStep；
18. AgentStep 必须填写 acceptance_criteria，描述可检验的完成条件；max_attempts 通常为 3；
19. AgentStep 运行时可返回 needs_input 继续追问，因此不要在 Prompt 中要求缺数据时编造或自动假设；
20. 可用输入变量只有双花括号包裹的 user_input、previous_output、answers.字段名。字段名必须对应此前 InputStep 的 key；
21. ToolStep 参数中仅引用确定可取得的数据；PythonStep 可使用 previous_output、user_input、user_answers；

用户任务目的：
{goal}

用户已有任务名称（可能为空）：
{title}

当前会话可用工具清单：
{tools}

Agentscope 原生任务清单（必须先以此为计划骨架）：
{native_tasks}
"""


_MODE_PLANNER_PROMPT = """你是现有 AgentScope Task Planner 的模式规划步骤。

请依据用户目标和 Agentscope 原生任务清单，生成一个 TaskPlanningCandidate。
结构必须使用现有 Task Schema：nodes 使用 TaskNode 类型、config 和稳定 id；
edges 使用 source_node_id/target_node_id；input_bindings 使用
source_node_id/source_value(output 或 result)。只返回结构化结果。

规划规则：
1. 保留最少且完整的业务步骤，通常 2 到 8 个；最终步骤必须是 AgentStep。
2. 节点 id 使用简短稳定值（如 n1、n2），依赖边表示实际的数据或业务前置依赖。
3. 仅当不同只读检索 Tool 有相同合法前驱、输入来源明确，且后续 Agent 显式绑定全部分支时，才建议 dag。
4. 不同 Tool 的只读属性必须来自工具清单；你不能自行宣布 Tool 只读、可并行或有权限。
5. Tool 名称必须逐字匹配可用工具清单，arguments 必须符合该 Tool 的 input_schema。
6. 输入绑定的源必须是目标节点的前驱或祖先；source_value=result 仅用于有结构化输出契约的来源。
7. 不确定依赖是否安全并行时，生成保守的顺序依赖图并建议 sop；不要用理由文字代替真实边或绑定。
8. 若附有官方模板契约，nodes、节点 id、顺序、配置、能力、输出契约和人工确认要求必须逐项原样保留；只可提出候选边和绑定。
9. 不要输出 max_concurrency、validation_status、execution_allowed、模板来源 id 或模板版本；这些字段由 Task 服务端产生。
10. recommended_mode 只是你的建议，服务端会重新校验图、工具、输入和运行授权。

用户目标：
<<GOAL>>

当前任务名称（可能为空）：
<<TITLE>>

会话工具清单（只读提示仅作模型参考，最终以 Task 服务端检查为准）：
<<TOOLS>>

Agentscope 原生 TaskCreate 清单（用于任务骨架）：
<<NATIVE_TASKS>>

运行策略摘要（只供规划，不代表本计划已经获得授权）：
<<POLICY>>

官方模板冻结契约（若为 null 则无版本化模板来源）：
<<TEMPLATE>>
"""


_NATIVE_TASK_SYSTEM_PROMPT = """你是任务规划阶段的 Agentscope 原生任务清单助手。

请根据用户目标调用原生 TaskCreate 工具创建一个最小、按依赖顺序排列的任务清单。
要求：
1. 复杂目标拆成 2 到 8 个可执行任务；简单目标不要凑步骤；
2. 每个任务都必须有清晰的 subject 和 description；
3. 需要先获取数据再处理时，先创建数据获取任务，再创建处理任务；
4. 需要多个数据来源时，明确任务之间的依赖；
5. 必须实际调用 TaskCreate，不要只用文字回复计划；
6. 不要调用 MCP、浏览器或业务工具，只创建任务清单；
7. 创建完成后即可结束，不要输出长篇解释。
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
        native_tasks = await _create_native_task_list(
            model=model,
            goal=goal.strip(),
        )
        plan_prompt = _PLANNER_PROMPT.format(
            goal=goal.strip(),
            title=(current_title or "").strip() or "（空）",
            tools=_format_tools(tool_schemas or []),
            native_tasks=json.dumps(
                native_tasks,
                ensure_ascii=False,
                indent=2,
            ),
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
            "然后重新输出完整的结构化计划：\n" + format_plan_issues(issues)
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

    async def plan_with_decision(
        self,
        *,
        user_id: str,
        goal: str,
        context: TaskContext,
        runtime_policy: TaskPlanningRuntimePolicy,
        current_title: str | None = None,
        tool_schemas: list[TaskToolSchema] | None = None,
        template_contract: TaskPlanningTemplateContract | None = None,
    ) -> TaskPlanningDecision:
        """Propose and validate SOP/DAG mode using the existing AgentScope model.

        It reuses the selected session model and native TaskCreate skeleton.
        TaskService persists the result for review and independently repeats
        permission, Preflight, graph, and scenario gates before creating a Run.
        """

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

        from agentscope.app._service._model import get_model
        from agentscope.message import UserMsg

        model = await get_model(
            user_id,
            session.config.chat_model_config,
            self._access,
        )
        native_tasks = await _create_native_task_list(
            model=model,
            goal=goal.strip(),
        )
        tools = list(tool_schemas or [])
        template_payload = (
            None
            if template_contract is None
            else {
                "template": template_contract.template.model_dump(
                    mode="json",
                    by_alias=True,
                ),
                "step_bindings": dict(template_contract.step_bindings),
                "frozen_nodes": [
                    node.model_dump(mode="json", by_alias=True)
                    for node in template_contract.frozen_nodes
                ],
            }
        )
        plan_prompt = (
            _MODE_PLANNER_PROMPT.replace("<<GOAL>>", goal.strip())
            .replace("<<TITLE>>", (current_title or "").strip() or "（空）")
            .replace("<<TOOLS>>", _format_mode_planning_tools(tools))
            .replace(
                "<<NATIVE_TASKS>>",
                json.dumps(native_tasks, ensure_ascii=False, indent=2),
            )
            .replace(
                "<<POLICY>>",
                json.dumps(
                    {
                        "sop_execution_enabled": runtime_policy.sop_execution_enabled,
                        "dag_execution_enabled": runtime_policy.dag_execution_enabled,
                        "scenario_execution_allowlist": [
                            scope.model_dump(mode="json")
                            for scope in runtime_policy.scenario_execution_allowlist
                        ],
                        "max_concurrency_limit": runtime_policy.max_concurrency_limit,
                    },
                    ensure_ascii=False,
                ),
            )
            .replace(
                "<<TEMPLATE>>",
                json.dumps(template_payload, ensure_ascii=False, indent=2)
                if template_payload is not None
                else "null",
            )
        )

        response = await model.generate_structured_output(
            messages=[UserMsg(name="task-planner", content=plan_prompt)],
            structured_model=TaskPlanningCandidate,
        )
        candidate = TaskPlanningCandidate.model_validate(response.content)
        decision = decide_task_plan(
            candidate,
            tool_schemas=tools,
            runtime_policy=runtime_policy,
            template_contract=template_contract,
        )
        if decision.validation_status != "invalid":
            return decision

        repaired_prompt = (
            plan_prompt
            + "\n\n上一版候选未通过 Task 服务端校验。请仅修复列出的问题，"
            "重新返回完整 TaskPlanningCandidate；不得改变冻结模板契约：\n"
            + "\n".join(
                f"- {issue.path}: {issue.message}"
                for issue in decision.validation_issues
            )
        )
        repaired_response = await model.generate_structured_output(
            messages=[UserMsg(name="task-planner", content=repaired_prompt)],
            structured_model=TaskPlanningCandidate,
        )
        repaired = TaskPlanningCandidate.model_validate(
            repaired_response.content
        )
        return decide_task_plan(
            repaired,
            tool_schemas=tools,
            runtime_policy=runtime_policy,
            template_contract=template_contract,
        )


async def _create_native_task_list(*, model: object, goal: str) -> list[dict]:
    """Create the planning skeleton through Agentscope native task tools.

    The temporary state is intentionally owned by the Task adapter. Native
    TaskCreate writes into AgentState.tasks_context; the persisted Task graph
    is created only after the structured TaskPlanDraft passes validation.
    """

    from agentscope.agent import Agent
    from agentscope.message import UserMsg
    from agentscope.state import AgentState
    from agentscope.tool import (
        TaskCreate,
        TaskGet,
        TaskList,
        TaskUpdate,
        Toolkit,
    )

    state = AgentState()
    planner = Agent(
        name="task-native-planner",
        system_prompt=_NATIVE_TASK_SYSTEM_PROMPT,
        model=model,
        toolkit=Toolkit(
            tools=[TaskCreate(), TaskList(), TaskGet(), TaskUpdate()],
        ),
        state=state,
    )
    await planner.reply(
        UserMsg(
            name="task-goal",
            content=(
                "请为以下用户目标创建 Agentscope 原生任务清单：\n\n" + goal
            ),
        ),
    )

    tasks = state.tasks_context.tasks
    if not tasks:
        raise ValueError(
            "Agentscope 原生 TaskCreate 未生成任务清单，无法生成 Task 节点。",
        )
    return [task.model_dump(mode="json") for task in tasks]


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


def _format_mode_planning_tools(
    tool_schemas: list[TaskToolSchema],
) -> str:
    """Expose the current tool schemas and read-only evidence to the model."""

    if not tool_schemas:
        return "当前会话没有可用的 MCP/Tool 工具。"
    return json.dumps(
        [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "is_mcp": tool.is_mcp,
                "is_read_only": tool.is_read_only,
                "read_only_hint": tool.read_only_hint,
            }
            for tool in tool_schemas
        ],
        ensure_ascii=False,
        indent=2,
    )
