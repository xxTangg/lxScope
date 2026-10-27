# 龙信Scope Task DAG-F1 受控执行验收报告

日期：2026-10-26  
分支：`integrate/dev_l`  
开始提交：`e63c3b80278dba2db5ddfd8e195b988481cabd1e`  
最终结论：**BLOCKED；生产准入 NO-GO**

## 1. 范围与结论

本阶段确认了已有 DAG 服务端安全门禁、Task 页面能力判断和隔离存储测试。Task DAG 专项及完整 Task 回归已执行；真实 Redis/PostgreSQL 的隔离恢复测试也通过。

但本阶段未能从浏览器创建并启动新的 Run：隔离页面使用 `localhost:18000` 时，Logto 拒绝未注册的回调 URI。当前隔离 TaskService 仅授权 S01 v3.0.0，不能据此宣称 S02～S09 另存的 DAG 已在页面上可执行。本阶段也没有开放生产 DAG。

用户希望九大场景另存的 DAG 流程可执行。本报告不重复声称既有 E1/R7 合成验收可替代浏览器执行；它们只作为已有 TaskService 回归证据。

## 2. 执行开关与服务端阻断来源

| 检查项 | 源码证据与行为 | F1 结论 |
|---|---|---|
| DAG 总执行开关 | `_configured_dag_scheduler_enabled()` 只接受显式的 `LXSCOPE_TASK_DAG_ENABLED` 真值；未设置时为关闭。TaskService 构造参数可显式覆盖。见 `_service.py:919-923, 1048-1069`。 | 默认关闭；编辑开关与执行开关分离。 |
| 场景授权 | `dag_scenario_test_allowlist` 是仅由进程内 TaskService 注入的精确模板 ID/版本测试授权；不读取环境变量，正式应用不传该参数。见 `_service.py:1050-1072`。 | 正式应用的版本化场景 DAG 没有该测试授权。 |
| Preflight | 先执行 Graph Validator，再分别报告总执行开关和场景授权阻断。见 `_service.py:2035-2069`。 | 关闭开关、未授权场景和图错误是不同阻断。 |
| Run 创建 | `create_run` 再次执行 Graph Validator、检查执行开关并调用场景门禁；Router 映射校验失败为 422、状态冲突为 409。见 `_service.py:2737-2761`、`_router.py:836-869`。 | 前端状态不能绕过服务端门禁。 |
| 版本化线性图 | 需有完整来源、用户自定义副本身份、冻结节点/契约/产物配置一致、模板原节点及顺序一致、相邻线性边一致、无额外绑定且并发度为 1。见 `_service.py:1699-1828`。 | 不符合冻结契约时 fail closed。 |
| S01 非线性图 | 唯一特殊分支为 `s01_project_init@3.0.0` 的精确 E2 图匹配。见 `_service.py:1812-1817, 1831-1860`。 | 仅隔离 TaskService 的精确授权可用。 |
| S02～S09 非线性图 | 当前门禁仅认可冻结线性图；未匹配的非线性图拒绝执行。 | 保持 BLOCKED。 |

普通无模板来源的自定义 DAG 不经过版本化场景授权分支，但仍须通过 DAG 总开关、Graph Validator、Task Preflight、所有者权限和原有交付门禁。

## 3. Task 页面变更

本阶段唯一源码修改为 `examples/web_ui/frontend/src/pages/task/index.tsx`：

1. 将“无法读取服务端能力”“DAG 总执行开关关闭”和“当前模板版本没有精确授权”拆成不同的阻断说明；能力接口失败时继续 fail closed。
2. 执行按钮仍需经过服务端 Preflight 和 Run 创建校验，没有仅靠前端启用执行。
3. 节点重跑受理后若 Run 处于暂停状态，提示用户点击“继续”从检查点恢复。
4. “另存为 DAG”提示依据当前 TaskService 能力动态展示，不再无条件声称所有版本化场景 DAG 都不能运行。

没有改动 Task 后端、Task API、AgentScope 主框架、`main.py`、官方模板、其他业务模块、全局配置、依赖或 Migration。后端已有安全注入点足以支撑隔离测试；没有为生产应用增加授权。

## 4. 隔离环境及浏览器结果

### 4.1 环境确认

- 隔离 Task API：`http://127.0.0.1:18002/health` 返回 `version=e2f-isolated-test`，API 与 Task 状态均为 ready。
- 独立 PostgreSQL 容器标有 `lxscope.task-e2f=true`，仅 loopback 暴露 `1911`，Docker mounts 为空。E2F 服务通过 R6 测试设施创建随机 `lxscope_task_test_r6_*` 临时数据库。
- 独立 Redis 容器标有同一隔离标签，仅 loopback 暴露 `9629`，Docker mounts 为空。真实测试限定 Redis DB 15，使用随机测试 owner/Run 键；不执行 `FLUSHDB`/`FLUSHALL`。
- E2F 浏览器服务使用真实 Task Router、隔离 PostgreSQL、独立 Redis 身份存储及合成 S01 知识库/Executor；其 TaskService 只注入 `s01_project_init@3.0.0`。未连接生产模型、MCP、ERP、审批服务或业务工具。

### 4.2 登录失败的实际证据

1. 首次启动 Vite 时没有注入前端 `VITE_LOGTO_*` 公共配置，页面显示本地用户名/密码表单。隔离服务运行 Logto 模式，OpenAPI 中不存在 `/auth/login`，所以该表单显示 `Not Found`。
2. 从仓库 `.env` 只读取本地公开 Logto 前端配置后重启 Vite，页面改为正常跳转到本地 Logto `localhost:3001`。
3. Logto 页面实际返回 `oidc.invalid_redirect_uri`，明确拒绝 `http://localhost:18000/auth/callback`，原因是该 URI 未在该客户端注册。该回调错误是浏览器 UI 的可见错误证据，不是 Task API 的 404。
4. 仓库配置的公开应用入口为 `http://localhost:8000`，该端口当前被既有 `lxscope2-web-ui-1` 容器占用。没有停止或替换该共享服务，也没有修改 Logto/全局认证配置。通过现有 UI 将 API 切换至隔离 E2F 服务的路径未能建立。

因此本阶段没有合法登录后的 Task 页面，没有点击执行按钮，没有浏览器创建的新 Run/API 请求，也没有新的浏览器截图或 Run ID。浏览器验收为 **BLOCKED**。不能将之前的 API/TaskService 测试表述为浏览器 PASS。

### 4.3 可复用的先前 S01 证据

既有 `task_dag_e2f_real_execution_acceptance_report.md` 第 9 节记录：S01 v3.0.0 非线性 DAG 在隔离浏览器中的 Run `d600e711c020419085212dbc124596b6` 最终为 `succeeded`，Run version 55、event sequence 57；目标节点 `376df46a54614a839fa6eac752763b3d` 的 Attempt 1 失败、Attempt 2 成功，旧 Attempt 保留，最终有 1 个 Markdown Artifact。这是 E2-F 先前验收，不是本阶段重新操作或新建的 Run。

## 5. 各类 DAG 状态

| DAG 类型 | TaskService/API | 当前隔离浏览器服务 | 浏览器实际执行 | 结论 |
|---|---|---|---|---|
| 普通自定义 DAG | 既有 Task/DAG 专项覆盖通过；无模板来源的图走普通 DAG 调度。 | 总执行能力开启，普通无来源 DAG 不需要场景 allowlist。 | 未登录后实际创建 Run。 | 服务端能力 PASS；页面执行 BLOCKED。 |
| S01 v3.0.0 原线性 DAG | 既有 E1 线性兼容测试通过。 | 精确模板版本在测试 allowlist。 | 本阶段未执行；沿用 E2-F 先前浏览器记录。 | E1 TaskService PASS；本阶段浏览器 BLOCKED。 |
| S01 v3.0.0 精确非线性 DAG | DAG-E2/E2-F TaskService 专项通过。 | 唯一获授权的场景非线性图。 | 沿用 E2-F Run `d600e711c020419085212dbc124596b6` 的既有 PASS。 | 先前浏览器 PASS；本阶段未重跑。 |
| S02～S09 原线性 DAG | E1 的既有线性兼容测试在本轮 DAG 专项及 Task 全套中通过。 | 当前 E2-F 服务只授权 S01；S02～S09 不在 allowlist。 | 未逐个创建页面 Run。 | TaskService 兼容 PASS；当前服务执行授权与浏览器验证 BLOCKED。 |
| S02～S09 非线性 DAG | 无本阶段执行证据。 | 服务端只接受获准的冻结线性图。 | 未执行。 | BLOCKED。 |
| 生产环境 DAG | 默认开关未设置时关闭；正式服务没有场景测试 allowlist。 | 本阶段未连接生产服务。 | 未执行。 | **NO-GO**。 |

九大场景官方模板、SOP Run 和已有 Run 均未被修改。没有关闭引用校验、Schema、业务终检或 Artifact 门禁。

## 6. 测试结果

| 检查 | 命令/结果 | 状态 |
|---|---|---|
| Task 页面 ESLint | `pnpm exec eslint src/pages/task/index.tsx`：退出码 0。 | PASS |
| 前端构建 | `pnpm run build`：构建成功，6919 modules transformed；存在单个 chunk >500 KB 警告。 | PASS |
| DAG、E1、E2、F1 能力及 P2-G 专项 | `pytest -q tests/task_dag_config_test.py tests/task_dag_e1_business_gate_test.py tests/task_dag_e2_non_linear_s01_test.py tests/task_dag_e2f_capabilities_test.py tests/task_dag_test.py tests/task_dag_p2g_acceptance_test.py --tb=short`：**44 passed**。 | PASS |
| 真实隔离 Redis/PostgreSQL E2-F 存储恢复 | `pytest -q tests/task_dag_e2f_storage_integration_test.py --tb=short`，连接 loopback 隔离实例；PostgreSQL 使用测试设施随机临时库，Redis 使用 DB 15 和 `protocol=2`。组合复跑：**2 passed**。 | PASS |
| 完整 Task 回归 | 对 46 个 `tests/task_*_test.py` 文件执行 `pytest -q`：**493 passed、6 failed、20 skipped、30 subtests passed**。测试服务集成项在未设置测试 URL 的全量运行中按设计跳过；之后单独连接隔离实例得到上行 2 passed。 | 与基线一致 |
| 真实浏览器 Task Run | Logto 明确拒绝 18000 回调 URI，未到 Task 页面。 | BLOCKED |

### 6.1 六项失败与阶段前基线

阶段开始时先保存工作区状态并运行相同完整 Task 套件。基线也是 **6 failed**。基线与结束 JUnit 按 nodeid、异常类别、断言消息、完整失败堆栈归一化后逐项比较：**6/6 签名一致，没有本阶段新增失败**。不同仅为运行时 `created_at`、随机 request ID 等动态值。

| Node ID | 基线/结束差异 |
|---|---|
| `tests.task_dag_editing_test.TaskDagEditingApiTests::test_capabilities_are_separate_and_custom_graph_is_saved_through_api` | 同为 AssertionError：旧期望漏掉新增的 `scenario_execution_allowlist: []` 响应字段。 |
| `tests.task_dag_editing_test.TaskDagEditingApiTests::test_editing_flag_template_provenance_and_run_state_are_enforced` | 同为 AssertionError：能力响应字典期望漏掉同一新增字段。 |
| `tests.task_dag_template_copy_test::test_all_current_official_templates_can_be_saved_as_provenance_preserving_dags` | 同为 AssertionError：精确响应断言漏掉同一新增字段。 |
| `tests.task_p7r1_output_contract_test.TaskP7R1ServiceOutputContractTest::test_sop_finalizer_builds_citation_index_then_runs_full_validation` | 同为 AssertionError：Claim–Evidence/Citation Envelope 校验拒绝输出（包含 citation block 后追加内容、claim-evidence 结构不完整）；Run 为 failed。 |
| `tests.task_p7r1_output_contract_test.TaskP7R1ServiceOutputContractTest::test_sop_rejected_repair_keeps_body_and_fails_closed` | 同为 AssertionError：旧断言期待 `task_protocol_repair` 元数据，当前 fail-closed 的 Citation/Claim–Evidence 结果不含该标记。 |
| `tests.task_s02_content_repair_test.TaskS02ContentRepairSOPTest::test_s02_v2_repairs_only_the_mixed_occurrence_then_runs_full_ledger` | 同为 AssertionError：修复后输出仍因 Citation Envelope/Claim–Evidence 结构校验失败，SOP 未完成。 |

这些既有失败未通过改测试断言或降低合同标准处理。

## 7. Git 工作区快照与本阶段增量

阶段开始快照保存在当前机器临时目录 `%TEMP%\\task-dag-f1-start-20261026`，记录 branch、HEAD、完整 `git status --short`、`git diff --name-only` 以及关键源码副本。

| 对照 | 阶段开始 | 阶段结束 |
|---|---:|---:|
| `git status --short` 项数 | 116 | 117（新增本报告 1 项） |
| 跟踪文件 `git diff --name-only` 项数 | 52 | 52（路径集合未变） |
| 阶段开始至结束 status 路径变化 | — | 无既有路径新增或消失 |

当前完整 `git diff --name-only` 与开始快照路径集合一致；包含多项阶段前 Task、前端、Sales Console、Docker 及测试改动，均为既有工作区内容。本阶段没有覆盖或重置它们。

与阶段开始的 `index.tsx` 内容快照比较，本阶段源码增量仅为该 Task 页面文件。结束时新增的唯一工作区路径为本报告。非白名单既有差异在前后快照中均存在；没有发现本阶段新增的白名单外路径。

本阶段文件：

- 修改：`examples/web_ui/frontend/src/pages/task/index.tsx`
- 新增：`examples/agent_service/task/task_dag_f1_controlled_execution_acceptance_report.md`

## 8. 用户目标与阻塞项

当前代码可以在 TaskService 显式设置 DAG 开关后运行普通自定义 DAG；版本化场景副本还必须有 TaskService 中精确的场景授权。当前 E2-F 隔离服务只注入 S01 v3.0.0，因此不能让 S02～S09 副本在该服务中运行。

要在不开放生产权限的前提下让九个场景原线性 DAG 可测试执行，尚需一个连接到合法浏览器登录入口的隔离 UI/API 组合，并由隔离 TaskService 对九个冻结模板版本逐项注入授权。当前浏览器 blocker 是 Logto 客户端未注册 `http://localhost:18000/auth/callback`；本阶段按约束没有修改全局认证。现有 `localhost:8000` 服务未被替换或改造为隔离 API 前端。

要在正式应用中允许运行九个版本化 DAG，则需要另行确定生产策略、场景授权及部署注入方式；这不属于 F1 的隔离开放范围。不能把 `LXSCOPE_TASK_DAG_ENABLED=true` 单独视作版本化场景授权。

## 9. 最终准入

- 普通自定义 DAG TaskService/API：**PASS**（现有专项测试）；浏览器点击执行：**BLOCKED**。
- S01 v3.0.0 精确非线性 DAG：先前 E2-F 浏览器证据 **PASS**；本阶段新浏览器执行：**BLOCKED**。
- S01～S09 原线性 DAG：E1 TaskService 合成兼容回归 **PASS**；S02～S09 当前隔离浏览器服务授权及页面 Run：**BLOCKED**。
- S02～S09 非线性 DAG：**BLOCKED**。
- Redis/PostgreSQL 隔离恢复专项：**PASS**（2 passed）。
- 完整 Task 回归：**基线一致**（493 passed、6 个既有失败、20 skipped）。
- 生产 DAG：**NO-GO**；保持默认关闭，本阶段未启用。

本阶段在浏览器完成登录并通过 Task 页面新建/启动隔离 DAG Run 之前，整体受控开放验收不满足成功标准。
