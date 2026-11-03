# Task R8：SOP/DAG 统一运行控制浏览器端到端验收报告

**日期：** 2026-10-23  
**分支 / HEAD：** `integrate/dev_l` / `e63c3b80278dba2db5ddfd8e195b988481cabd1e`  
**结论：** 前端静态构建与当前 Task 回归已检查。由于本次会话没有可识别的浏览器页面或可确认的合法登录会话，R8 浏览器端到端主目标 **BLOCKED**；未把 API/单元测试表述为浏览器验收。生产准入 **NO-GO**。

## 1. R8-A：环境、URL、认证和工作区

### 浏览器与认证状态

通过当前会话的浏览器清单检查，结果为：

- `ChatGPT in-app browser`：0 个标签页。
- `Codex MCP Apps`：0 个标签页。
- 可用应用窗口：0 个。
- 当前浏览器 URL / 页面标题：**不可观测**，没有当前页面。
- 合法测试登录会话：**无法确认**。本轮没有读取 Cookie、伪造身份、打开登录页或尝试登录。

Docker 只读状态检查显示 `lxscope2-agentscope-1` 和 `lxscope2-web-ui-1` 均为 healthy，分别发布端口 8001 和 8000；另有项目 PostgreSQL、Redis、Logto 容器运行。由端口映射推测的本地服务入口为 `http://localhost:8000` 和 `http://localhost:8001`，但它们不是已观测的浏览器 URL；当前无法确认这些容器属于隔离测试环境，也无法确认数据库、模型和其他依赖的用途。因此本阶段没有访问这些地址或调用这些服务。

根据 R8-A 的停止规则，页面 URL 与登录态无法同时确认时必须停止浏览器操作。故没有执行创建 Task、打开 S01～S09 页面、控制 Run 或 DAG 图编辑，也没有截图、浏览器请求/响应、真实 Run ID/Node ID/Event 记录。以下浏览器项目均 **BLOCKED**，不能用后端 API 测试补记为通过。

### Git 快照

R8 开始快照保存在 `%TEMP%\lxscope-task-r8-start-20261023-092925`，包括 branch、HEAD、`git status --short`、`git diff --name-only` 和未暂存 patch。起点有 90 条 status 项、39 个 tracked diff 路径。既有修改未清理或覆盖。

## 2. R8-B：普通 SOP Task 浏览器验收

**状态：BLOCKED。** 未在真实页面创建五节点线性 Task，未点击执行、暂停、单步、继续、Stop 或节点重跑；因此没有实际前端 Run/Node 状态、请求 ID、HTTP 响应、Attempt 或刷新恢复记录。R7 与 R8 的后端自动化测试不是本项浏览器证据。

## 3. R8-C：S01～S09 模板页面兼容

| 模板 | 浏览器实例/控制/产物状态检查 |
|---|---|
| S01 项目立项 | BLOCKED：页面未打开 |
| S02 项目策划 | BLOCKED：页面未打开；未操作 `draft_unverified` 产物 |
| S03 专家匹配 | BLOCKED：页面未打开；未检查人工复核展示 |
| S04 会议准备 | BLOCKED：页面未打开；未检查通知未发送状态 |
| S05 会议纪要 | BLOCKED：页面未打开 |
| S06 意见处理 | BLOCKED：页面未打开 |
| S07 报告生成 | BLOCKED：页面未打开；未检查引用/产物展示 |
| S08 归档移交 | BLOCKED：页面未打开 |
| S09 费用结算 | BLOCKED：页面未打开；未执行完整浏览器路径 |

因此 R8 未确认前端是否误报正式完成、旧产物是否仍被展示、Attempt/事件历史是否可见、Tool 副作用提示是否出现，也未能检查九大模板是否仍保持 SOP 模式。R7 的合成 TaskService 数据检查不能替代本页面检查。

## 4. R8-D：DAG 浏览器验收

**状态：BLOCKED。** 没有浏览器页面、测试 Run 或隔离执行服务身份，未编辑/展示 A → B/C → D → E 图，也未观察并行、汇合、SSE、局部重跑或控制按钮。未检查编辑开关与执行开关在实际 UI 上的分别授权。R8 没有打开 DAG 功能开关或生产功能。

## 5. R8-E：异常与安全测试

**浏览器侧：BLOCKED。** 重复点击、重复 request_id、stale version、HTTP 409/422、非 Owner、SSE 断线重连、刷新、Stop 后继续、未确认副作用重跑都没有在页面中操作或观测。

作为独立后端回归证据，R8 运行了既有 API/Router 与控制测试（见第 7 节），但没有把它们用来替代浏览器结果。前端按钮是否正确禁用、展示错误和刷新后重连，本阶段仍未知。

## 6. 允许范围内的最小修正

Scoped ESLint 首次运行发现 `examples/web_ui/frontend/src/pages/task/task-input-form.tsx` 的 import 排序违规（`./task-template-demo-inputs` 排在 `import type` 后）。该文件在 R8 开始快照中未修改。已仅移动 import 顺序，没有改变运行逻辑；重跑 Task scoped ESLint 通过。该修改在允许的 Task 页面路径中。

没有修改 Task 后端、全局认证、导航、模板或全局依赖。Build 生成物位于忽略目录 `dist/`，没有新增到 Git 状态。

## 7. R8-F：静态检查、API/Router、SOP/DAG 与完整回归

| 检查 | 命令 | 结果 |
|---|---|---|
| Task 范围 ESLint | `pnpm exec eslint src/pages/task src/api/task.ts` | 最初因 import 排序失败；完成上述单行排序修正后 **PASS**。 |
| TypeScript + 前端 build | `pnpm run build` | **PASS**（`tsc -b`、Vite v8.0.13；6919 modules）。Vite 提示主 bundle 约 5.55 MB，超过 500 kB 建议阈值。 |
| Task API/Router + SOP/DAG 控制专项 | `python -m pytest tests/task_dag_p2g_acceptance_test.py tests/task_dag_control_test.py tests/task_dag_retry_test.py tests/task_sop_control_test.py tests/task_sop_template_retry_test.py -q --tb=short` | **31 passed、9 subtests passed、1 warning**；warning 为 Starlette 422 常量弃用。它们是测试 harness，不是浏览器操作。 |
| 完整 Task 回归 | 收集 `tests/task_*.py` 后运行 `python -m pytest @taskTests -q --tb=short` | **433 passed、43 failed、18 skipped、1 warning、30 subtests passed**，124.30 秒。Redis/PostgreSQL 测试 URL 在该 PowerShell 进程中明确移除，18 项跳过不得计为真实数据库通过。 |
| R6 43 项失败签名对照 | `compare_task_failure_signatures_r6_v4.py` 对照 R6 完整日志 | **43/43 完全相同**：nodeid/顺序、测试名、异常类型、断言行、项目堆栈帧均相同；新增 0、消失 0。 |

R8 前端目录未发现专用 Task E2E/测试脚本。故没有自动化浏览器测试，也不声称有页面交互覆盖。R6/R7 的既有测试结果可参考此前报告，本轮没有接入或重验真实数据库、模型或其他业务依赖。

完整 R8 日志：`%TEMP%\lxscope-task-r8-full-20261023.txt`。R6 基线日志：`%TEMP%\lxscope-task-r6-full-final-20261022-215938.txt`。

## 8. 修改文件与 Git 白名单核对

R8 阶段开始 `git diff --name-only` 有 39 个路径；完成后仍保留这 39 个既有路径，另外只增加本次 Task 页面文件：

- `examples/web_ui/frontend/src/pages/task/task-input-form.tsx`：只调整 import 顺序。
- `examples/agent_service/task/task_unified_execution_r8_browser_acceptance_report.md`：本报告（新增文件）。

R8 开始状态与当前状态差异中的 tracked 新变化仅为上述 Task 页面文件；未跟踪新变化仅为本报告。其余既有工作区状态仍在，未被清理或改写。没有修改白名单外文件，没有提交代码。

## 9. 验收结论与 BLOCKED 项

| 验收项 | 结果 |
|---|---|
| 浏览器 URL 与合法测试会话确认 | **BLOCKED**：没有当前浏览器标签或可确认会话。 |
| 普通 SOP 五节点浏览器操作 | **BLOCKED** |
| S01～S09 页面和业务状态展示 | **BLOCKED** |
| DAG 五节点浏览器操作 | **BLOCKED** |
| 浏览器异常/安全交互 | **BLOCKED** |
| Task scoped ESLint | **PASS**（最小 import 排序修正后） |
| TypeScript / Vite build | **PASS**（有 bundle 体积警告） |
| API/Router、SOP/DAG 后端测试 | **PASS**（31 passed、9 subtests） |
| 完整 Task 回归 | **FAIL（既有基线）**：43 failed，与 R6 签名完全一致；18 skipped。 |
| 生产准入 | **NO-GO** |

后续要完成浏览器验收，需在 Codex 浏览器会话中打开隔离 Task 页面并建立合法测试登录，然后从 R8-B 开始执行；同时确认该隔离服务使用测试数据、测试模型和测试工具。当前没有依据判断 Docker 中运行的 PostgreSQL/Redis 或 AgentScope 服务属于隔离验收实例，故未连接。R8 到此停止，不进入 R9、不启用生产功能、不提交代码。

## 10. 补充要求：S01～S09 节点断点与恢复浏览器验收矩阵

**总状态：九个场景全部 BLOCKED。** 本次重新检查时，浏览器清单仍为 `ChatGPT in-app browser: 0 tabs`、`Codex MCP Apps: 0 tabs`、`apps: []`。因此没有可观测页面 URL、合法登录会话或可确认的隔离 Task 服务身份。按照 R8-A 的准入条件，没有导航到候选地址、调用浏览器/API、创建 Task 或启动 Run。Docker 容器运行状态及候选 localhost 端口不能证明它们是隔离测试环境，本报告不将其当作准入证据。

下表按场景逐项登记。由于未创建任何 Run，每项都没有可诚实填写的 Run ID、Node ID、状态快照、Attempt、事件序号、请求/响应或截图。`BLOCKED` 表示未执行且没有结果，不代表测试通过或失败。

| 场景 | 官方模板创建并启动 Run | Pause → Step → Continue | 中间节点失败及前端重跑 | Run ID、前驱执行次数、Attempt | 下游/产物失效与重生成 | InputStep、刷新恢复 | 副作用确认门禁 | 浏览器证据 / 结果 |
|---|---|---|---|---|---|---|---|---|
| S01 项目立项 | BLOCKED；未创建 | BLOCKED；未操作 | BLOCKED；未注入/点击 | Run ID、节点状态、Attempt：无 | 未验证 | 未验证 | 未验证 | 无 URL、页面、请求/响应或截图；BLOCKED |
| S02 项目策划 | BLOCKED；未创建 | BLOCKED；未操作 | BLOCKED；未注入/点击 | Run ID、节点状态、Attempt：无 | 未验证；未检查 `draft_unverified` 失效/重生成 | 未验证 | 未验证 | 无 URL、页面、请求/响应或截图；BLOCKED |
| S03 专家匹配 | BLOCKED；未创建 | BLOCKED；未操作 | BLOCKED；未注入/点击 | Run ID、节点状态、Attempt：无 | 未验证 | 未验证；未检查人工复核门禁 | 未验证 | 无 URL、页面、请求/响应或截图；BLOCKED |
| S04 会议准备 | BLOCKED；未创建 | BLOCKED；未操作 | BLOCKED；未注入/点击 | Run ID、节点状态、Attempt：无 | 未验证；未检查通知状态 | 未验证 | 未验证；不得推定通知已发送或可重试 | 无 URL、页面、请求/响应或截图；BLOCKED |
| S05 会议纪要 | BLOCKED；未创建 | BLOCKED；未操作 | BLOCKED；未注入/点击 | Run ID、节点状态、Attempt：无 | 未验证 | 未验证 | 未验证 | 无 URL、页面、请求/响应或截图；BLOCKED |
| S06 意见处理 | BLOCKED；未创建 | BLOCKED；未操作 | BLOCKED；未注入/点击 | Run ID、节点状态、Attempt：无 | 未验证 | 未验证 | 未验证 | 无 URL、页面、请求/响应或截图；BLOCKED |
| S07 报告生成 | BLOCKED；未创建 | BLOCKED；未操作 | BLOCKED；未注入/点击 | Run ID、节点状态、Attempt：无 | 未验证；未检查引用/报告产物失效与重生成 | 未验证 | 未验证 | 无 URL、页面、请求/响应或截图；BLOCKED |
| S08 归档移交 | BLOCKED；未创建 | BLOCKED；未操作 | BLOCKED；未注入/点击 | Run ID、节点状态、Attempt：无 | 未验证；未检查归档产物失效与重生成 | 未验证 | 未验证；不得推定移交副作用可安全重试 | 无 URL、页面、请求/响应或截图；BLOCKED |
| S09 费用结算 | BLOCKED；未创建 | BLOCKED；未操作 | BLOCKED；未注入/点击 | Run ID、节点状态、Attempt：无 | 未验证；未检查结算产物失效与重生成 | 未验证 | 未验证；不得推定费用/结算副作用可安全重试 | 无 URL、页面、请求/响应或截图；BLOCKED |

### 每个场景要求的操作结果

对上述每个场景，要求 1（从官方模板实例化 Task 并启动 Run）至要求 10（检查有副作用节点的确认门禁）均未在浏览器中执行，逐项状态均为 **BLOCKED**。所以本阶段没有宣称任何场景的 Pause 在中间节点安全边界生效、Step 只派发一个节点、Continue 命中检查点、局部重跑保留前驱、Run ID 不变、Attempt 增加、下游/产物正确失效并重建，或 InputStep 与刷新恢复正常。也没有证据证明失败节点在有副作用结果未知时受到确认保护。

### 证据类型分离

| 证据类别 | 本阶段结果 | 可支持的结论 |
|---|---|---|
| 后端合成/Task 自动化测试 | 仅引用本报告第 7 节既有结果；本补充未运行新测试 | 可说明所列自动化测试结果；**不能**证明九场景浏览器操作、真实业务外部集成或页面状态恢复 |
| 浏览器端到端 | **BLOCKED**；本次没有可操作标签页或合法登录会话 | 不支持任何 S01～S09 浏览器 PASS |
| 真实 Redis/PostgreSQL | 本补充未连接、未运行；R8 不能据此给出新的数据库结论 | 不支持本次 R8 的真实存储 PASS |
| 真实外部业务集成（模型、知识库、MCP、审批/业务 API） | 未连接、未运行 | 不支持真实外部集成 PASS |

### 恢复验收的继续条件

需要先在浏览器中打开**已确认隔离**的 Task 页面，并由用户/授权人员按现有认证流程建立合法测试会话；还需确认该服务实际指向隔离测试数据及非生产外部依赖。条件满足后，必须为 S01～S09 各自执行完整步骤并记录真实 Run ID、Node/Attempt/事件及页面证据。本矩阵不得以普通模拟任务、后端合成测试、Docker `healthy` 状态或模板页面静态展示补记为 PASS。


## 11. R8 浏览器验收环境修复复查（2026-10-23）

### 入口与容器检查（只读）

- Docker context 为 `desktop-linux`。当前 Compose 项目 `lxscope2` 使用 `docker-compose.yml` 和 `docker-compose.dev.yml`；前端容器 `lxscope2-web-ui-1` 将主机端口 `8000` 映射到容器 `80`，后端 `lxscope2-agentscope-1` 将主机端口 `8001` 映射到容器 `8000`。
- 对 `http://localhost:8000/` 发起只读 GET，返回 **HTTP 200**、`Content-Type: text/html`，页面 `<title>` 为“龙信助手”。因此 `http://localhost:8000` 已确认是当前运行前端入口。此次使用 HTTP 检查，没有创建 Task 或 Run。
- 后端 `LXSCOPE_DATABASE_URL` 指向 Compose 内部 `postgres:5432/lxscope`；PostgreSQL 容器没有发布主机端口，挂载持久卷 `lxscope2_postgres-data`。Redis 通过 Compose 内部 `redis:6379` 连接，也没有发布主机端口并挂载持久卷 `lxscope2_redis-data`。这些证据表明运行进程使用项目内的本地容器，不是配置成远程生产数据库地址；但卷是持久的，现有数据用途及是否仅含本次隔离测试数据无法由容器/端口信息证明。未连接或查询业务表，也未写入/清理数据。
- `lxscope2_default` 是 Docker bridge 网络且 `internal=false`。后端运行环境中 `SILICONFLOW_BASE_URL=https://api.siliconflow.cn`、`DEEPSEEK_BASE_URL=https://api.deepseek.com`，并且两者的 API Key 环境变量已配置。没有证据证明这些凭据属于隔离测试账号或只允许测试模型；网络也不是 Docker internal 网络。**没有向模型端点发出请求。**
- 运行时有本地 Logto 容器；Task Agent 的 MCP 配置来自 Agent/Session。容器环境显示 `PLAYWRIGHT_MCP_COMMAND` 已设置为本地 `playwright-mcp` 可执行项，但仅凭此不能证明实际 Agent 的所有 MCP/业务工具都指向测试服务。此前用户说明当前 Agent/Session 只连接本地测试 MCP；这属于用户提供的信息，本次没有启动 Run 去验证工具清单或调用目标。

### 浏览器和 Playwright 工具能力

- 当前仓库根目录、Task 前端 `node_modules` 均未安装 `playwright` / `@playwright/test`；Python 环境也未安装 Playwright，`playwright` 命令不可用。用户目录已有 Playwright Chromium 1148 浏览器缓存。
- Codex 的 `cua_repl` 浏览器能力可用：已读取 IAB 浏览器文档，确认可以主动创建独立可见浏览器标签，并通过页面 DOM、Playwright locator 和截图操作。本次工具清单仍没有打开标签。
- 因此**浏览器控制工具不是当前阻塞点**；Codex 可以在环境准入后启动独立测试浏览器。阻塞点是尚未证明持久化 DB/Redis 中数据仅为授权测试数据，且服务配置了身份/用途未知的外部 SiliconFlow/DeepSeek API Key，不能在未知凭据下执行可能触发模型调用的 S09 Agent 节点。

### 本轮分项状态

| 分类 | 状态 | 证据与边界 |
|---|---|---|
| 前端入口检查 | **PASS（只读）** | `localhost:8000` GET 200，标题“龙信助手”；不是浏览器 E2E |
| Codex 独立浏览器能力 | **PASS（能力确认）** | `cua_repl` IAB 可通过 `createBrowserTab` 创建独立标签，并支持 Playwright locator；本轮未创建标签 |
| Playwright 项目依赖 | **未安装** | 未改依赖；系统缓存 Chromium 不等同于项目 Playwright 包 |
| 隔离 PostgreSQL/Redis 数据确认 | **BLOCKED** | 本地 Compose 专属容器和卷已确认，但数据用途未知且卷持久；没有写入或清理 |
| 外部模型隔离确认 | **BLOCKED** | SiliconFlow、DeepSeek 端点及 API Key 已配置，凭据环境/授权范围未确认；未调用 |
| S09 页面登录 / 新建 Task / Run | **BLOCKED** | 为避免向未确认隔离环境写业务数据或调用外部模型，本轮未启动浏览器、未登录、未创建 Task/Run |
| 人工验收 / 浏览器 E2E | **BLOCKED** | 无用户人工登录或 UI 操作记录；无 Run ID、Node 状态、Attempt、截图或操作日志 |
| 真实 Redis/PostgreSQL 和外部业务集成 | **未重测** | 本轮仅检查容器元数据与环境配置，没有连接服务、执行数据库测试或调用业务工具 |

**准入决定：** 暂不进入 S09 浏览器操作。需要确认 `lxscope2_postgres-data` 与 `lxscope2_redis-data` 是可用于本次 R8 的测试数据/隔离卷，并确认 `SILICONFLOW_API_KEY`、`DEEPSEEK_API_KEY` 是获准的非生产测试凭据；或者提供一个已确认隔离且模型/MCP/业务工具均为测试目标的 Task 服务地址。确认前不创建/修改/删除 Task 数据、不登录新会话、不运行 S09，也不把只读 HTTP 检查计为 UI PASS。

本轮只更新本报告，没有修改应用源码、测试、Compose、认证配置、模型配置或依赖，也没有提交代码。

## 12. 隔离环境确认后的 S09 浏览器验收续测（2026-10-23）

### 准入与范围

用户明确确认 `lxscope2_postgres-data`、`lxscope2_redis-data`、配置的 SiliconFlow/DeepSeek 凭据和当前 Task MCP/业务工具均属于本次隔离测试环境，并确认已切换到测试账号、当前 Agent/Session 仅连接本地测试 MCP。用户还确认首次演示数据由其手动提交。依据该授权，继续使用已登录的本地 IAB 会话；未读取 Cookie、未绕过认证、未连接生产服务。

- 浏览器 URL：`http://localhost:8000/task/471f19a90a524f0b8247922b2132c877`
- 前端入口：`http://localhost:8000`（第 11 节已只读确认 HTTP 200）
- Task ID：`471f19a90a524f0b8247922b2132c877`
- 模板：官方 `报销结算 v3.0.0`，六步 SOP；本轮在该模板实例上执行，没有将其转成 DAG。
- 数据：仅合成 DEMO 票据/规则。成功的 Run 执行了 AgentStep，因此使用了用户确认的非生产测试模型凭据；不能表述为“全程未调用模型”。没有调用 OCR、ERP、审批、真实票据或生产 MCP/业务系统。失败重跑路径止于本地 `expense.invoice_import` 确定性 Python 适配器。
- Task 运行存储按本地服务配置使用 Redis。此次以 UI 状态及事件核验，没有读取数据库凭据，也没有对数据库执行清理或破坏性操作。

### 实际浏览器操作与状态证据

| 测试 | 页面操作和可见证据 | 结果 |
|---|---|---|
| 官方模板运行 | 在该隔离 Task 页面提交演示 InputStep，任务进入真实六步执行链；页面展示模板版本及 6 个 SOP 节点。 | **PASS**（模板实例及执行链） |
| Pause | Run 3 运行时点击“暂停”；页面进入“已暂停，可继续运行或单步派发一个 ready 节点”，显示 2/6 完成，后续节点等待。暂停发生在安全节点边界。 | **PASS** |
| Step | 在 Pause 状态点击“单步”；仅 `检查输入内重复票据` 完成，`核对费用规则` 及后续节点仍等待；完成后页面再次显示已暂停。 | **PASS**；可见状态证明本次只派发一个 ready 节点 |
| Continue | 点击“继续”；从检查点执行节点 4～6，前 3 个成功节点没有重新开始；最终 6/6 完成，生成 `待提交报销单草稿.xlsx`。控制版本由 v24 推进至 v42。 | **PASS** |
| 刷新恢复 | 对完成后的 Run 刷新 Task 页面，等待 Task 数据重新载入后，同一任务恢复为完成态；仍显示 6/6、草稿 XLSX 和执行轨迹 44 条。 | **PASS**；首次加载中的空态不作为最终状态 |
| 失败注入 | 对下一次运行，先提交 `invoice_records_json = "["`，UI 在提交前提示“请填写 JSON 数组或对象”，没有形成 Run 失败；改为合法 JSON 数组 `[{}]` 后提交，真实 ToolStep 因缺少 `invoice_id` 失败。 | **PASS**（受控失败到达后端节点执行） |
| 前端局部重跑及确认 | 对失败 ToolStep 点击“重跑节点”；页面显示外部/工作区副作用确认提示。接受 UI 确认后轨迹出现 `node.retry_requested`，然后点击“继续”派发重试。 | **PASS**（实际通过前端操作；未直接调用 API） |
| Run/Attempt/前驱 | Run 4 ID 始终为 `7e466e9e96f44261afe7bc842efdeff5`；失败节点 `42bbeb7472f44d139c224f08abaac779`。成功 InputStep 保持 attempt 1。失败 ToolStep 由自动 Attempt 1 增至手动 Attempt 2；历史区显示两条失败记录。事件轨迹记录 `node.retry_requested`、`run.continued`、`run.claimed`、第二次节点开始/失败。 | **PASS**（同一 Run、历史保留、成功前驱未重跑） |
| 下游与产物 | Run 4 重试前后，重复检查、费用规则核对、两个 Agent 节点均保持等待；该失败 Run 没有生成草稿产物。 | **PASS**（失败分支下游未执行；成功产物重生成未验证） |
| 刷新后的失败 Run | 本轮刷新恢复证据来自 Run 3 的完成态；没有另行刷新并核验 Run 4 的失败态。 | **BLOCKED** |
| Stop、重复点击、409/422、Owner 拒绝、SSE 断连恢复 | 本轮没有执行这些浏览器场景；客户端 JSON 检查仅验证了前端输入提示，不等同 HTTP 422 验收。 | **BLOCKED** |

Run 3 的页面只显示运行控制版本（v17、v24、v42），没有在浏览器可见内容中显示完整 Run UUID；因此不把控制版本冒充 Run ID。Run 4 的失败详情显示了完整 Run ID、Node ID 和错误类型 `TASK_TOOL_EXECUTION_FAILED`。其 InputStep 回答事件带有 `request_id=76c4bc819b8c484d950a5062b3865a53`，该值是人工输入请求 ID，不是重跑请求 ID。页面没有呈现 HTTP 状态码，故不报告未观测到的请求/响应码。

### 重跑结果边界

Run 4 使用的 `[{}]` 是为了稳定触发校验失败而构造的合成输入。局部重跑按相同 Run 快照再次执行同一节点，Attempt 2 因相同原因再次失败；UI 显示错误仍为“票据缺少 invoice_id。”，两个下游 ToolStep 和两个 AgentStep 均未执行。该结果证实了失败节点在同一 Run 内重新执行、Attempt 增长、历史保留及有效前驱保留，但**没有**证实修正输入后该 Run 可以成功完成下游及重新生成产物。该浏览器路径没有为原 Run 修改已保存的 InputStep 快照；因此将“同一 Run 修正输入并成功恢复”列为 **BLOCKED/未验证**，不宣称完整失败恢复通过。Run 3 的成功草稿来自另一个完整成功运行，不能用它替代 Run 4 的失败后恢复证明。

### 页面截图及操作日志

本续测截图由 Codex IAB `getScreenshot()` 捕获并在本对话工具结果中展示：Run 4 页面可见 S09 Task、失败节点、`当前 attempt 2`、Attempt 1/2 历史、`票据缺少 invoice_id。`以及重跑副作用确认提示。截图对应 2026-10-23 10:56 左右的页面状态。CUA 截图接口只返回并展示图像，没有提供本地文件导出路径；因此没有伪称截图文件已写入仓库。上表保存了从页面事件轨迹读取的操作摘要。Run 3 Pause/Step/Continue 和最终产物截图同样曾在本对话工具结果中显示，未落盘。

### S01～S09 阶段矩阵更新

本续测仅执行 S09；S09 尚未满足“失败节点修正后同一 Run 成功继续”的完整标准，故没有启动 S01～S08。以下是本次续测的逐场景结论；历史 BLOCKED 矩阵保留在第 10 节。

| 场景 | Task/Run | Pause | Step | Continue / 恢复 | 失败重跑 / Attempt | 刷新与产物 | 本次结论 |
|---|---|---|---|---|---|---|---|
| S01 项目立项 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | BLOCKED；S09 未完成完整恢复门槛，按顺序要求未扩展 |
| S02 项目策划 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | BLOCKED；同上 |
| S03 专家匹配 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | BLOCKED；同上 |
| S04 会议准备 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | BLOCKED；同上 |
| S05 会议纪要 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | BLOCKED；同上 |
| S06 意见处理 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | BLOCKED；同上 |
| S07 报告生成 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | BLOCKED；同上 |
| S08 归档移交 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | BLOCKED；同上 |
| S09 费用结算 | Task `471f19a90a524f0b8247922b2132c877`；Run 3 UUID 未显示、Run 4 `7e466e9e96f44261afe7bc842efdeff5`；失败节点 `42bbeb7472f44d139c224f08abaac779` | PASS，Run 3，暂停于 2/6 | PASS，单步仅完成节点 3 并再次暂停 | PASS，Run 3 从检查点完成至 6/6；Run 4 Continue 后重跑 Attempt 2 仍因测试输入失败 | PASS（机制）：同 Run、成功前驱保持、Attempt 1→2、历史保留；修正输入后成功恢复 BLOCKED | PASS：Run 3 刷新后恢复完成态并显示 XLSX；Run 4 无产物，恢复产物未验证 | **PARTIAL；不满足完整 S09 PASS** |

### 本轮 Git 与代码变更

本次续测没有修改 Task/前端源码、测试、配置、模板、依赖或数据库，也没有提交代码；唯一有意写入是向本文件追加本节验收记录。开始时工作区已有大量与本轮无关的修改及未跟踪文件，均保留。当前 `git diff --name-only` 中已有白名单外变更，不能归因于本轮浏览器操作；本轮没有新增对这些路径的编辑。报告本身是此前已存在的未跟踪 Task 报告，因此它仍显示为 `??`，Git 不会为未跟踪文件提供普通 tracked diff。

### R8 本地续测结论

| 项目 | 结果 |
|---|---|
| S09 官方模板页面与正常 SOP 执行 | PASS |
| S09 Pause / Step / Continue | PASS |
| S09 完成态刷新恢复 | PASS |
| S09 同 Run 局部重跑、前驱保留、Attempt 历史 | PASS（机制） |
| S09 重跑后输入修复、下游成功及产物重生成 | BLOCKED / 未验证 |
| S01～S08 浏览器断点与恢复 | BLOCKED；本轮未执行 |
| Stop 与浏览器异常/权限场景 | BLOCKED；本轮未执行 |
| 浏览器自动化类型 | Codex CUA IAB 实际浏览器操作 PASS；不是项目 Playwright 自动化套件 |
| 真实外部业务集成 | BLOCKED / 未执行；仅用户确认隔离用途，未调用 OCR、ERP、审批或生产业务系统 |
| 代码/自动化测试 | 本轮无源码改动、未运行测试 |
| 生产准入 | **NO-GO**；九场景验收、失败后同 Run 成功恢复及 Stop/安全异常场景仍未完成 |

## 13. R8-S09 失败后成功恢复补充核查（2026-10-23）

### 保留现状

本轮没有打开、更新、重跑或修改浏览器中的既有 Run 4 `7e466e9e96f44261afe7bc842efdeff5`，也没有改写任何 Run/Node 快照。Run 4 的 `[{}]` 业务输入和两次 `invoice_id` 失败记录保持原样。为了满足新增目标，必须建立新的合法输入 Run；但当前运行中的真实 Task API 没有可安全指定 Run+Node 的一次性故障注入接口，本轮没有在共享 API 服务中临时替换执行器、修改全局开关或创建新的浏览器 Run。

### 现有 Task 专属故障注入测试

代码审查发现并执行了现有 R7 测试：

```text
tests/task_r7_nine_scenarios_integration_test.py::test_current_scenario_failed_agent_retries_in_same_run_then_steps_and_resumes[s09_expense_settlement]
```

结果：**1 passed**（第二次记录运行 4.72s；首次运行 1 passed，6.64s）。该测试通过真实 `TaskService`、当前 `TemplateRegistry`、模板编译、SOP 调度、Task 原生工具、节点状态和产物流程；在隔离测试实例中注入合成 Agent 失败，并将其用于失败重跑验证。其输入来自 `examples/agent_service/task/docs/p7b-demo-data/inputs/s09-expense-settlement.v3.json`，包含 `DEMO-INVOICE-001`、`DEMO-INVOICE-002` 及合成费用规则（每票上限 CNY 2000、总额上限 CNY 5000）。它使用每测试新建的 `TaskStore()` 内存存储、合成 Agent/Workspace 和合成授权知识库；不访问当前浏览器服务、Redis/PostgreSQL、模型或外部业务系统，因此只能列为后端合成测试证据。

带状态 trace 的运行关键证据：

| 阶段 | Run ID | Run 状态 / 控制 | Run Version / Event Sequence | Node / Attempt 证据 |
|---|---|---|---|---|
| 故障后 | `ddf491a0aae64987a6aa295264d5a698` | failed / continuous | 25 / 26 | `整理报销单草稿` Node `1fecf023258a45d5ab85a0c562c1a510` failed attempt 1；前三个节点均 succeeded attempt 1；最终材料节点 pending attempt 0；无产物。 |
| 局部重跑重置后 | 同一 Run ID | queued / paused | 26 / 27 | 前三个节点状态、输出、结果及 attempt 与失败前相同；失败节点保留 failed attempt 1 历史并回到 pending；下游仍 pending、无产物。 |
| Step 检查点 | 同一 Run ID | queued / paused | 33 / 34 | 失败节点变为 succeeded attempt 2，历史为 failed/succeeded；末节点仍 pending；其余节点保持原 attempt。随后测试重建 TaskService 并 Continue。 |
| 最终 | 同一 Run ID | succeeded / continuous | 41 / 42 | 六个节点全 succeeded；目标 Agent attempt 2，最后 Agent attempt 1；共一项 XLSX 产物，6296 bytes，SHA-256 `7f2105e99b7f4117d339abf177085b55e98c265f10f6f74b20af0ec5f0b9496d`。 |

TestService 的 executor 调用 trace 为目标 Agent Node 两次（第一次合成失败，第二次成功）和最终 Agent Node 一次；目标节点的第二次调用前，测试清除了失败条件。测试断言 Run ID 不变、已成功前驱的 Node ID/output/result/attempt 不变、前驱 Agent 调用计数不增加、Attempt 1 失败和 Attempt 2 成功历史保留、下游在失败/Step 检查点期间 pending、服务重建后 Continue 最终 succeeded、事件包含 `node.retry_requested`、`run.step_completed`、`run.continued`、最终 `run.succeeded`，并存在 XLSX artifact record。该重试测试未单独比较前驱的 `started_at`/`finished_at`，也未由浏览器下载或渲染检查这次恢复生成的 XLSX 内容；不把这些细项列为已证实。

上述 trace 的 Run ID、Node ID 和产物散列属于内存合成测试 Run，**不是**浏览器或持久化服务中的业务 Run。S09 通用完整成功测试另有产物内容断言，但不是本次失败恢复 Run，因此不能替代恢复 Run 的浏览器产物核验。原浏览器 Run 3/Run 4 的证据仍见第 12 节。

### 真实浏览器注入能力审查

- `_get_task_service` 从当前应用 `app.state.task_service` 返回共享服务实例；Task API 没有 `fail_once`、故障注入或测试执行器切换路由/配置。
- 已有 R7 `_SyntheticModelAndWorkspace` 仅在测试代码创建的独立 `TaskService` 实例中用于故障注入；测试以 `fail_node_id` 指定节点，并在 retry 之后清除该条件。它没有浏览器可访问的入口，也不是按 Run ID 校验的运行时 hook。直接把这个 executor 装入当前 `app.state.task_service` 会改变共享服务执行行为，不符合本次隔离要求。
- Task API 的 retry 请求体是 `TaskRunControlRequest`（请求幂等 ID、期望 Run 版本和副作用确认），不能注入 executor 失败。

结论：已有机制足以证明**TaskService 后端合成恢复路径 PASS**，但不足以在不改动/替换共享服务的情况下触发**真实前端按钮恢复 Run PASS**。因此本次没有新建 S09 浏览器 Run；浏览器完成恢复项继续 **BLOCKED**，不将上述 pytest 结果包装成浏览器验收。

### 历史输入修正能力

现有 API 不支持修正失败 Run 的输入快照后继续同一 Run：`POST /tasks/runs/{run_id}/input` 只处理 `AWAITING_INPUT` 且必须匹配当前 `pending_input` 的 InputStep 回答；`POST /tasks/runs/{run_id}/nodes/{node_id}/retry` 只接受 `TaskRunControlRequest`，没有新输入字段；`POST /tasks/runs/{run_id}/retry` 的语义是创建新 Run。`update_task` 修改 Task 定义，不是历史 Run 快照修订接口。故“历史 InputStep 答案/Run 输入修正后在同一 Run 继续”是**新能力需求**，当前不支持；本次没有直接编辑持久化快照。

### 最小安全的浏览器补测方案

建议在授权执行下一步后新增一个只存在于 Task 专属测试代码中的隔离浏览器 harness，不为共享 TaskService 增加可被生产调用的故障 API：

1. 在单独 loopback 测试进程/应用中装配官方 S09 v3.0.0、`TaskService` 和独立测试存储；复用现有 Task API Router 与正常认证验证，不使用 `dependency_overrides`、伪造身份或生产 Cookie。若无法复用合法登录，浏览器项继续 BLOCKED。
2. 复用现有 Task executor 注入点，包装专用合成 executor；先让 `create_run` 暂停派发，得到真实 Run ID 后再 `arm(run_id, prepare_expense_draft_node_id)`，仅当 Run ID、Node ID、attempt=1 三者完全匹配时返回一次合成可重试失败，并原子消耗注入标记；attempt=2 走确定性成功合成 Agent/verifier。其他 Run/节点永不受影响。
3. 使用 R7 S09 DEMO 输入中的有效票据字段与规则。通过合法登录的 Task 前端创建新官方模板实例和 Run；页面等待指定 Agent 失败后点击“重新执行节点”，然后 Step/Continue；整个测试不连接外部模型、MCP、ERP 或审批服务。
4. 捕获浏览器截图、Task API 事件/Run 回包及 trace；逐项核验 Run ID、输入及前三个前驱的状态/输出/时间/调用次数、Attempt 历史、后继状态、XLSX 文件内容及刷新恢复。最后销毁仅由该测试 harness 创建的存储数据。

该方案不需要改 AgentScope、`main.py`、官方模板、全局认证或共享运行服务；但独立应用的认证复用、Task 前端指向该测试 API 的方式及所需测试专属 harness 尚未实现/验证。故此处只提交方案，不启动第二个服务，不接触共享服务状态。

### 更新后的 S09 结论

| 验收层 | 结论 |
|---|---|
| 同 Run 失败节点局部重跑机制 | **PASS**：原浏览器 Run 4 证明前端重跑和 Attempt 2；本次 R7 TaskService 测试证明 Attempt 1 失败后 Attempt 2 成功。两类证据分开记录。 |
| 失败后同 Run 成功恢复的 TaskService 业务链 | **PASS（后端合成）**：Run `ddf491a0aae64987a6aa295264d5a698` 最终 succeeded，生成 XLSX artifact record。 |
| 失败后同 Run 成功恢复的真实 Task 前端浏览器闭环 | **BLOCKED**：没有安全的 live-service 故障注入入口；未创建浏览器 Run。 |
| 历史输入修正后同 Run 继续 | **不支持/新能力需求**：现有 API 无此能力。 |
| 旧 Run 4 | **保持不变**：仍是原输入错误导致的两次失败，不作为新恢复测试 Run。 |
| S01～S08 | **未开始**：按要求先完成 S09 浏览器验收；S09 浏览器恢复仍未闭环。 |
| 生产准入 | **NO-GO**：浏览器恢复闭环、其余场景验收及安全/部署检查仍有阻塞。 |

本补充未修改源码、测试、模板、认证、全局配置、数据库或共享服务；只扩展本报告，并执行了上述既有 Task 专属测试。无代码提交。
