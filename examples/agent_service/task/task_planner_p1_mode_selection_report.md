# 龙信 Scope Planner-P1：SOP/DAG 自动编排决策报告

## 交付范围

本阶段增加了 Task 专属的候选契约、依赖校验和模式决策，并在现有 `AgentScopeTaskPlanner` 上增加 `plan_with_decision()`。该方法复用当前会话模型和 AgentScope 原生 `TaskCreate` 规划骨架，未增加第二套模型 Planner。

规划输出包含 `planning_id`、创建时间、模型建议模式、服务端最终模式、Task 节点、依赖边、输入绑定、服务端并发度、校验状态、执行授权状态和模板来源。模型候选 Schema 不包含 `max_concurrency`、`validation_status`、`execution_allowed` 或模板来源字段。

开始前保存了工作区快照：`C:\Users\15452\Documents\ChatGPT\lxscope2_prework_snapshot_20261026_154955`。快照包括基线提交、分支、Git 状态、跟踪文件差异及全部 152 个未跟踪路径。没有提交代码。

## SOP/DAG 决策规则

- 依赖边组成严格单链且没有命名输入绑定时，服务端推荐 `sop`，并将并发度设为 1。该决定优先于模型建议模式。
- 具有明确分支和完整汇合绑定的图保留为 `dag`。只有 Task 规则能够证明并行安全时，计划才会得到 `validation_status="valid"`。
- 通用并行证明要求：同一就绪层仅有不同名称的只读 Tool；分支使用共同前驱及合法的用户输入或 InputStep 答案；没有跨分支依赖；后续 Agent/Python 汇合节点直接依赖并绑定每个分支；图中其他阶段保持顺序执行。
- 不确定的非线性候选保留为 DAG 供审查，状态为 `pending`、并发度为 1、不可执行。规划器不会把有分支或汇合语义的候选自动降级为 SOP。
- Tool 必须在当前 Task 工具清单中按名称精确匹配，并通过现有 Tool 参数 Schema 校验。Task 原生能力只从 Task 自有能力目录补充，不接受模型声明的工具能力。
- 运行策略提供 SOP/DAG 开关、精确模板授权、逐节点 Tool 权限结果、资源上限和并发上限。缺少运行模式授权或 Tool 权限结果时，`execution_allowed` 为 false。
- 外部副作用或没有只读证据的 Tool 会出现在 `approval_required_node_ids` 中；规划结果不会替代既有权限确认或 Run 重跑门禁。

## 计划结构示例

下面是确定性测试替身产生的双检索分支结构。该示例验证了 Planner 决策输出与现有 Task/DAG Schema 的映射；验收未调用真实模型。

```json
{
  "recommended_mode": "dag",
  "reason": "经 Task 校验的独立只读检索分支在汇合节点完整绑定。",
  "nodes": [
    {
      "id": "projects",
      "name": "检索相似项目",
      "type": "tool",
      "config": {
        "type": "tool",
        "tool_name": "project.search",
        "arguments": {"query": "{{user_input}}"}
      }
    },
    {
      "id": "policies",
      "name": "检索授权规则",
      "type": "tool",
      "config": {
        "type": "tool",
        "tool_name": "policy.search",
        "arguments": {"query": "{{user_input}}"}
      }
    },
    {
      "id": "merge",
      "name": "汇总检索结果",
      "type": "agent",
      "config": {"type": "agent", "prompt": "综合已绑定的两个来源。"}
    }
  ],
  "edges": [
    {"source_node_id": "projects", "target_node_id": "merge"},
    {"source_node_id": "policies", "target_node_id": "merge"}
  ],
  "input_bindings": {
    "merge": {
      "project_records": {"source_node_id": "projects", "source_value": "output"},
      "policy_records": {"source_node_id": "policies", "source_value": "output"}
    }
  },
  "max_concurrency": 2,
  "validation_status": "valid",
  "execution_allowed": true
}
```

示例中的 `execution_allowed=true` 需要运行环境启用 DAG，并向服务端规划策略提供两个 Tool 节点各自已通过的权限结果。没有这些结果时结构仍可审查，但计划不可执行。正式 Run 入口目前没有调用该方法。

## 依赖、绑定与模板校验

- 候选图交给现有 `validate_task_graph()` 校验节点引用、重复边、自依赖、循环、资源需求和输入绑定祖先关系。
- 多前驱汇合节点必须通过命名绑定覆盖全部直接前驱。`source_value="result"` 需要结构化输出契约；S01 v3 E2 的两个场景原生检索结果按其冻结场景契约单独识别。
- `answers.*` 必须对应某个祖先 InputStep 中声明的问题；`previous_result` 只接受唯一前驱的已验证结构化结果；不支持的输入变量会使计划无效。
- 官方模板来源来自服务端提供的冻结模板契约，不读取模型提供的模板 ID/版本。候选的节点 ID、顺序、配置、能力、输出契约及提示词必须与编译出的冻结节点一致，阻止删除必需节点或改写业务契约。
- S01 v3 仅识别 E2 的精确七节点图、八条依赖边、字段绑定、并发度 3 和精确的 `s01_project_init@3.0.0` 运行授权。S02～S09 及其他版本化模板的非线性图即使出现在候选中，也不会被此组件授权执行。
- `max_concurrency` 不接收模型值。服务端按已证明的并行宽度、运行策略上限和节点资源需求计算；任务资源需求缺少服务端容量时由 Graph Validator 拒绝。

## 测试结果

命令：

```text
.venv/Scripts/python.exe -m pytest tests/task_planning_decision_test.py -q
```

结果：`15 passed`。

测试覆盖严格线性 SOP、独立只读检索分支 DAG、合流输入解析、循环拒绝、缺失 Tool、非法绑定、逐节点权限结果、并发资源上限、副作用审批与重跑门禁、S02 版本化非线性阻断、S01 E2 精确图识别、Run 快照不变、模型不能设置服务端结果字段，以及不调用真实模型的确定性 Planner 替身。

## 与 DAG-F1 的接口和剩余工作

- `AgentScopeTaskPlanner.plan_with_decision()` 接收现有会话上下文、工具 Schema、服务端 `TaskPlanningRuntimePolicy`，以及可选的冻结模板契约；返回可审查的 `TaskPlanningDecision`。DAG 决策可用 `as_task_graph()` 投影为现有 `TaskGraph`。
- `TaskPlanningRuntimePolicy` 的执行开关、精确场景白名单、逐节点 Tool 权限结果及资源配置必须由服务端提供。决策器不以模型输出替代权限或 Graph Validator 结果。
- 与 F1 集成时，正式 Run 入口仍需调用现有 Task 保存/Graph Validator、场景阻断器和 Tool 权限检查，并仅在这些检查通过后创建 Run。Planner P1 本身未持久化计划历史；目前的 `planning_id` 和时间戳提供结果标识，后续集成需决定审计持久化位置。
- S01 E2 精确图判定在 P1 规划组件中按现有 TaskService 判定条件实现。为避免策略漂移，后续 F1/整合阶段应通过共享纯函数或一致性测试维护两侧规则。
- 本阶段未修改 `_service.py`、`_router.py`、DAG Scheduler、Run 状态机或 SOP 执行核心；没有将 Planner 接入正式 Run 入口，也没有启用生产 DAG。
