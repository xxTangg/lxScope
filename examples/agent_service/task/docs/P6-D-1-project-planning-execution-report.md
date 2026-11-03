# P6-D-1：Task 浏览器验收与项目策划业务场景执行报告

## 基线与范围

- 分支：`integrate/dev_l`
- 基准：`c002f4d feat(task): support advanced editing for custom task flows`
- 本阶段只修复 Task 对 `document.render` 的条件化能力映射及 Preflight 报告；没有改动 AgentScope 官方源码、S02 v1、其他场景模板、认证、非 Task 模块或前端。
- `planning.template_retrieve` 与 `document.citation_verify` 没有找到经验证的业务服务，因此没有伪造绑定，也没有启动真实项目策划 Task。

## 1. 实际修改文件

| 文件与位置 | 修改 |
|---|---|
| `examples/agent_service/task/_artifacts.py:42` | 增加格式序列化器可用性检查：Markdown 内置；DOCX/XLSX 分别核对 `docx`、`openpyxl` 模块。 |
| `examples/agent_service/task/_agentscope_executor.py:281` | 新增只读 Artifact Writer 预检：以当前用户及会话解析 Workspace，确认最终写入、读取、stat 和路径解析接口存在；不写文件。 |
| `examples/agent_service/task/_template_validation.py:288,489` | 只有一个有效且位于最终非 Input 节点的 ArtifactConfig 时，才从静态模板编译缺失项中移除由 Task Artifact 提供的逻辑能力。模板原始 required capabilities 不变。 |
| `examples/agent_service/task/_service.py:593,677,934` | Preflight 按真实 ArtifactConfig、格式渲染器和 Writer/Workspace 预检结果报告 `document.render`；只有三项均满足才标为 `available`。自定义流程缺失的必需业务 Tool 也会准确进入 `missing_capabilities`。 |
| `tests/task_artifact_capability_test.py:1` | 新增 7 项 Task 测试，覆盖无产物配置、标准模板静态校验、Writer 可用/缺失/拒绝、渲染器缺失、用户 Workspace 解析及无预检写入。 |
| `examples/agent_service/task/docs/P6-D-1-project-planning-execution-report.md` | 本报告。 |

未修改 `s02_project_planning.v1.json`、其他 v1 模板、前端页面或 API、`main.py`、`src/agentscope/**`。没有修改依赖声明或锁文件。

## 2. P6-C-1 浏览器验收

用户提供的当前状态是：已选择项目策划模板、创建 Task、另存自定义流程、编辑保存并触发 Preflight；服务端显示缺少 `planning.template_retrieve`、`document.citation_verify` 和 `document.render`。这作为用户报告记录，本次未能独立复现全部步骤。

本次通过浏览器打开 `http://localhost:8000/task` 后被重定向到 `/login`。页面显示“无法连接服务器，请检查网络或服务器地址后重试”，并要求选择组织。没有已验证的 Task 登录会话；没有操作登录表单，也没有改动认证配置。因此以下浏览器检查均为 **BLOCKED**：自定义流程刷新持久化、原 Task/模板未变化、跨用户页面隔离、实际 Preflight 和下载交互。

对应的所有权、保存、模板隔离及 Preflight 门禁已有 Task 自动化覆盖，但不把这些测试称为浏览器通过。

## 3. S02 三项能力缺口

| 能力 | 源码/当前会话证据 | 结果 |
|---|---|---|
| `planning.template_retrieve` | S02 v1 将其列为必需 Tool 能力（`templates/s02_project_planning.v1.json:8-11,40-52`）。仓库 `TaskKnowledgeGateway.retrieve`（`_knowledge.py:67-110`）可按当前用户及显式选择的知识库检索文本，并附带来源名、分段号和相关度；Agent 节点能接收这类知识上下文（`_agentscope_executor.py:393-396`）。但它不是 S02 的已绑定 Tool，没有保证所选知识库包含经批准的历史策划模板，也没有 `source_material_ids` 到知识库文档的契约。当前用户报告的 Preflight 也列此项缺失。 | **BLOCKED**。通用用户知识库检索不能被冒充为已验证的策划模板业务服务。仓库本地 KB 样例内容包含“虚构示例”声明，不作为生产资料。 |
| `document.citation_verify` | S02 v1 要求 Tool 节点核对引用（`templates/s02_project_planning.v1.json:75-85`）。Task 的结构化输出校验器（`_output_validation.py:80`）校验 JSON Schema，不验证引用来源是否存在、引用内容是否一致。知识库检索返回来源标识不等于引用核验。仓库和当前 Preflight 均没有经验证的引用校验服务。 | **BLOCKED**。不能用模型自述、Schema 通过或格式检查代替来源校验。 |
| `document.render` | S02 v1 声明此能力为必需，但最终 `produce_planning_output` Agent 节点没有 `artifact` 配置（`templates/s02_project_planning.v1.json:8-11,99-108`）。Task 现有 ArtifactConfig/Writer 支持 Markdown、DOCX、XLSX，运行结束后生成文件并按 TaskRun 保存产物元数据（`_service.py:1362-1402,2105-2140`；`_artifacts.py:41-85`）。 | **PASS（条件化映射）**。见下一节；没有改写 v1 模板，也没有无条件加入白名单。 |

## 4. `document.render` 与 Artifact Writer

此前 `_capability_contracts.py:343-381` 已将 `document.render` 归类为 Task Artifact 能力，但 Preflight 没有消费该映射，所以它会一直作为缺少 Tool 能力报告。

本次修正只在以下条件全部成立时将能力标为 `available`：

1. 自定义/模板 Task 的最终非 Input 节点配置了唯一且通过安全文件名校验的 ArtifactConfig。
2. 所选格式对应的现有序列化器可用。检查通过 `find_spec`，不会提前生成文件。
3. 当前服务绑定了 Artifact Writer，并能通过当前 Task 用户、Agent 和 Session 解析 Workspace；Workspace 后端提供 Artifact 实际执行所需的路径解析、写入、读取和 stat 接口。

缺少 ArtifactConfig 时显示 `missing`；格式组件或 Workspace/Writer 不能确认时显示 `unverified`/`incompatible`，Preflight 仍拒绝运行。检查不会写入探测文件，也不调用业务 Tool。真实文件仍只在 SOP 完成后的既有产物阶段生成；写入失败继续走原 Task 失败路径，不创建虚假产物记录。

标准模板静态校验只在合法 ArtifactConfig 存在时忽略相应的 Task Artifact 逻辑能力。Preflight 随后再验证真实 Writer/Workspace，因此静态校验不会单独放行任务。自定义流程保留原模板来源、两个业务 Tool 能力及所有权限制。

## 5. Tool/MCP 绑定和项目策划运行

- 当前没有已验证的 `planning.template_retrieve` 或 `document.citation_verify` Tool/MCP 名称、输入输出 Schema、权限结果或真实数据源。检索仓库的非 Task 业务模块未找到这两个能力的实际注册实现；此前 P6-B-3A 报告也将它们列为未验证业务能力。
- 当前浏览器没有可用于读取真实会话 Toolkit/MCP 目录的登录状态。用户提供的 Preflight 状态将两项业务能力列为缺失；本次不声称验证了实时 Tool 注册目录。
- 新增 Task 测试使用显式标记的 fixture：配置 Markdown 和就绪的测试 Writer 后，`document.render` 为 `available`，而另外两个 Tool 仍列为缺失。调用 `create_run` 被拒绝，TaskRun 数量仍为零，Artifact Writer 未写入。
- 因两个真实业务能力缺失，**没有进入生产项目策划运行**。没有伪造检索结果、引用验证或策划报告，也没有声称 S02 已形成真实业务闭环。

## 6. 文件生成和下载验证

现有 `tests/task_template_output_test.py` 在本阶段 Task 全量测试中通过，覆盖 Markdown、DOCX、XLSX 实际字节生成、TaskRun 产物关联、用户隔离、写入失败及禁止虚假成功。它使用 Task 测试 Writer/磁盘适配器，不代表真实 AgentScope Workspace 与已认证 HTTP 下载链路。

下载路由继续通过 Task 所属用户读取 Run/Artifact，再从 Workspace 读取文件（`_router.py:491-547`、`_service.py:1153-1195`）。本次没有有效浏览器会话，真实 Workspace 写入、授权下载 HTTP 和文件内容浏览未能验收。

## 7. 测试与工程验证

| 命令/检查 | 结果 |
|---|---|
| 全部 `tests/task_*_test.py`：`$taskTests = rg --files tests \| Where-Object { $_ -match '(^\|[\\/])task_.*_test\.py$' }; .\.venv\Scripts\python.exe -m pytest @taskTests -q -ra` | **129 passed, 2 skipped, 2 subtests passed**。包含 `task_input_protocol_test.py`、S02、九模板、Task Artifact、Preflight、自定义流程及 SOP 回归。 |
| PostgreSQL 专项 | 2 skipped：未设置一次性 Task PostgreSQL 测试 URL；不代表真实 PostgreSQL 集成通过。 |
| `pnpm build`（`examples/web_ui/frontend`） | **PASS**，TypeScript 与 Vite production build 成功；Vite 报已有的大 bundle 提示。本阶段没有改前端。 |
| `git diff --check` | **PASS**；报告和提交前再次执行。 |
| Ruff / pre-commit | 当前 `.venv` 没有 Ruff，命令环境也没有 pre-commit；未运行。 |

`fakeredis[lua]` 原本已在仓库 `pyproject.toml` 的 `dev` 可选依赖中声明，但初始 `.venv` 缺失。本次只安装到本地 `.venv` 后完成包含 `task_input_protocol_test.py` 的全量 Task 回归；未改 `pyproject.toml`、锁文件或项目依赖配置。

## 8. 未验证事项与阻塞

1. 已认证浏览器的自定义流程刷新保存、TaskRun 产物展示和实际下载：被 `/login` 的服务器连接错误阻断。
2. 当前真实会话的 Toolkit/MCP 名称、Schema、权限及数据源：没有已认证会话，且用户报告的 Preflight 显示两项 S02 Tool 缺失。
3. 真实 S02 业务闭环：等待业务方提供经授权的策划模板检索服务及引用来源校验服务/契约。
4. AgentScope Workspace 真实后端及授权 HTTP 下载：仅通过 Task 测试适配器和 Workspace 边界单元测试；没有生产集成环境。
5. PostgreSQL 两项专项：因测试 URL 缺失而 skipped。
6. Ruff/pre-commit：环境未安装，未运行。

## 9. 白名单核对

本阶段代码和测试只改：

- `examples/agent_service/task/_artifacts.py`
- `examples/agent_service/task/_agentscope_executor.py`
- `examples/agent_service/task/_service.py`
- `examples/agent_service/task/_template_validation.py`
- `tests/task_artifact_capability_test.py`
- 本 Task 文档。

没有修改 `src/agentscope/**`、`main.py`、共享认证、Task 外后端、数据库迁移、共享 API Client、全局导航、依赖声明或构建配置。初始工作区的 `sales-console/**` 改动、`blobs/`、临时目录及 P6-A/P6-B-0 文档均保留且未纳入本阶段提交。

## 10. 最终验收

| 验收项目 | 结果 | 依据 |
|---|---|---|
| 自定义流程浏览器验收 | **BLOCKED** | 当前 `/task` 重定向至 `/login`，页面无法连接服务器。 |
| `document.render` 能力映射 | **PASS（Task 条件逻辑）** | 必须有合法 ArtifactConfig、格式渲染器和当前用户 Workspace Writer 预检；已有专项测试。真实 Workspace 集成仍待验。 |
| 项目策划 Tool 接入 | **BLOCKED** | 两项业务能力没有经验证的 Tool/MCP 和数据源。 |
| Preflight | **PASS（自动化）** | 准确报告缺少的两项 Tool；无 ArtifactConfig 时 `document.render` 缺失；配置且 Writer 就绪时仅映射该 Artifact 能力；缺失 Tool 时不建 Run、不写产物。 |
| 项目策划真实执行闭环 | **BLOCKED** | 检索与引用校验业务服务尚未接入。 |
| 九场景回归 | **PASS（自动化）** | Task 全量可收集测试 129 passed，2 个 PostgreSQL 用例 skipped。 |
| AgentScope 主代码保持不变 | **PASS** | `src/agentscope/**` 与 `examples/agent_service/main.py` 未修改。 |

本阶段只完成 Task 内 `document.render` 条件映射与测试；不宣称 S02 已生产可用。建议提交信息：`fix(task): map artifact capabilities in preflight`。
