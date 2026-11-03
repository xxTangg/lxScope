# 龙信Scope Planner-P2 执行流程集成报告

## 交付状态

Task 智能规划入口、TaskService 规划/确认接口、执行前确定性复核和 Task/Run 计划审计快照已实现。SOP、授权 DAG、未授权 DAG 三条定向服务链路通过测试；前端构建通过。

真实模型验收**未通过**：DeepSeek 端点拒绝当前配置凭据（HTTP 401）；随后使用配置的 SiliconFlow 模型调用同一个 `AgentScopeTaskPlanner.plan_with_decision()`，等待 120 秒后超时。两次均未返回规划结果，也没有创建 Run。当前本地 AgentScope HTTP 路由探测同样超时，因此没有完成真实页面到 TaskService 的端到端模型验收。不得将这部分记作通过。

## 规划入口与用户流程

- Task 页面在创建目标 Task 后调用现有 `AgentScopeTaskPlanner.plan_with_decision()` 所在的规划 API，并展示候选模式、节点、依赖、命名输入绑定、并发度、服务端原因、校验问题和待审批节点。
- 用户可载入现有编辑器调整节点/图，重新规划，或确认当前计划。确认接口保存用户确认的定义并返回只读 Preflight；它不创建 Run，也不自动启动有副作用的节点。
- 只有用户之后显式点击“执行任务”，前端才调用已有 Preflight/Run API。页面会在 Task 编辑后要求重新确认。
- 新增接口：`POST /tasks/{task_id}/planning` 提案并保存待审阅计划；`POST /tasks/{task_id}/planning/confirm` 保存已确认定义并返回 Preflight。接口仍使用现有 Task 用户身份依赖。

## SOP/DAG 决策和服务端授权

P2 复用现有 AgentScope Task Planner，不新增模型 Planner。模型只提交候选节点、依赖、绑定、推荐模式和理由。Task 层再调用 P1 `decide_task_plan()`，按 Task/DAG Schema、真实工具目录、输入输出契约、冻结模板和服务端运行策略重算模式、并发、校验状态及授权状态。

服务端 `TaskPlanningRuntimePolicy` 来源于当前 TaskService 实例：

- SOP/DAG 开关和场景执行授权范围取自服务端现行能力配置；模型不能声明或覆盖执行权限。
- 工具清单由当前用户、Agent/Session 对应的 Task 工具目录读取。确认/规划阶段还使用真实 Preflight 权限结果；只有工具状态为可用、权限允许且被证明无外部副作用的 Tool 节点才进入可授权集合。
- `LXSCOPE_TASK_PLANNER_MAX_CONCURRENCY` 限定最大并发（默认 8，夹取到现有 Schema 的 1–8）；`LXSCOPE_TASK_RESOURCE_LIMITS_JSON` 提供 Task 资源容量。模型输出的并发和资源声明不会覆盖服务端值。
- 有严格线性边且没有图输入绑定时推荐 SOP。只有合法输入来源、可证明独立的只读检索分支和完整汇合绑定才推荐可执行 DAG。不能证明并行安全的图保留为待审阅候选，执行阻断。
- Tool 能力缺失、循环依赖、悬空边、非祖先输入绑定或非法图均不能成为可执行计划。副作用节点保留现有审批和重跑门禁，Planner 不会授予绕过权限。

## 计划结构和确认后的执行边界

接口返回 P1 的 `TaskPlanningDecision`，包括 `recommended_mode`（仅 `sop`/`dag`）、服务端 `reason`、Task 节点、边、命名 `input_bindings`、受策略约束的 `max_concurrency` / `resource_limits`、`validation_status`、`execution_allowed`、校验问题、审批节点及模板 ID/版本。服务端结果初始为待校验/校验后值；不会采信模型候选中的授权字段。

P2 服务测试中的代表性结构（由确定性 Planner 测试替身提供，不是成功的真实模型输出）：

```text
SOP：analyze -> deliver
  确认后 graph = null，Run.engine = "sop"

DAG：projects ─┐
              ├-> merge
     policies ┘
  merge.project_records = projects.output
  merge.policy_records  = policies.output
  服务端并发上限结果 = 2
```

提案和确认结果保存在所属 Task，含 `planning_id`、`planning_version`、计划历史、目标指纹和提案/确认/编辑确认审计记录。确认时先重建候选并调用确定性校验，再调用现有 `validate_task_definition` / Graph Validator 与只读 Preflight。

Run 创建仍要求当前计划 ID、版本、Task 图/节点指纹匹配用户确认记录，并重新经过既有 Graph Validator、DAG Scheduler 开关、场景执行门禁及 Preflight；最后还会检查服务端规划授权结果。`execution_allowed=true` 不是 Run 创建的旁路。Run 记录冻结 `planning_id`、计划版本和完整规划快照；之后编辑 Task 不改变已有 Run 快照。

## 官方模板约束

规划时从现有模板校验器解析准确模板版本，并把冻结模板定义、步骤映射及 Task 当前冻结节点交给 Planner。缺失模板版本或冻结节点时规划阻断；模型不能清除来源、改版本、删必需节点或覆盖模板契约。

P1 确定性测试确认 S01 v3 仅识别精确的 E2 并行图和授权配置；S02–S09 未获准的版本化非线性图可以被保留为候选，但 `execution_allowed=false`，现有场景执行门禁仍会阻止 Run。P2 未修改官方模板、Scheduler 或 Run 状态机。

## 验收结果

| 验收 | 结果 | 证据 |
|---|---|---|
| 严格线性任务确认后创建 SOP Run | 通过 | `test_linear_plan_confirmation_creates_sop_run_with_frozen_plan`；Run 冻结计划 ID/版本/快照，后续 Task 编辑不改 Run |
| 两条独立只读检索分支汇合后创建 DAG Run | 通过 | `test_independent_read_only_retrieval_confirms_and_creates_dag_run`；断言模式、服务端并发 2、两个汇合绑定和 Run 图 |
| 未授权 DAG 可预览但不能创建 Run | 通过 | `test_unapproved_dag_plan_is_reviewable_but_cannot_create_run`；候选保留、Preflight 阻断、Run 创建冲突 |
| P1 决策规则和边界 | 通过 | P1+P2 定向集合合计 18 项；覆盖循环、缺失 Tool、绑定、汇合、副作用门禁、版本化模板及 S01 E2 精确图 |
| 真实模型规划 | 未通过 | AgentScope Planner 的 DeepSeek 请求返回 401；SiliconFlow 请求 120 秒超时；没有模型计划结果 |
| Task 页面前端构建 | 通过 | `pnpm --dir examples/web_ui/frontend build`（Vite 仅报告现有大 chunk 提示） |
| 本地 HTTP 端到端 | 未完成 | 容器状态显示运行中，但宿主机和容器内对 AgentScope HTTP 路由的只读请求都超时；当前 `docker-compose.dev.yml` 校验另报 `agentscope` 服务缺少 image/build 配置。未重启服务，也未触碰正在运行的 Run |

定向 Python 测试命令：

```powershell
.venv/Scripts/python.exe -m pytest -q tests/task_planning_decision_test.py tests/task_planner_p2_execution_test.py
```

结果：`18 passed`。测试导入真实 Task 包并使用确定性 Planner 替身，不调用真实模型。真实模型请求使用虚构目标、空工具目录和 SOP-only 策略，只调用规划方法；没有保存 Task 计划或创建 Run。

## 与 F1 的集成接口及剩余工作

P2 通过新增的 Task planning/confirm API 与现有 TaskService、Preflight、Graph Validator、DAG 能力/场景门禁衔接。F1 侧继续拥有 Scheduler 和 Run 执行授权；P2 不改其执行状态语义。建议 F1 完成后针对这两个 API 进行联合服务验收，特别验证真实用户工具权限、F1 运行开关和场景能力配置与 `TaskPlanningRuntimePolicy` 的映射一致。

剩余验收依赖可用的真实模型凭据/连通性、修复当前 Compose 配置并恢复可响应的本地 Task HTTP 服务。当前真实模型未通过、页面到服务端端到端未验证，因此上线前仍需完成这两项验收；本文不启用生产 DAG。

## 工作区保护

开始修改前已在 `C:\Users\15452\Documents\ChatGPT\lxscope2_p2_prework_snapshot_20261026_161555` 保存工作区状态、已有 diff 和未跟踪文件快照。既有修改未清理或回滚。本阶段未提交代码，未启用生产 DAG，也未修改官方模板、`main.py`、全局认证、DAG Scheduler 或 Run 状态机。
