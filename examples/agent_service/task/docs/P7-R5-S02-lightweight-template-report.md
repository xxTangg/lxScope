# P7-R5：S02 轻量化模板开发报告

## 交付结论

S02 `5.0.0` 已新增为默认系统模板。模板保留现有 TaskService、AgentScope SOP、Task 原生授权知识库检索、TaskRun、Artifact Writer 和 Workspace 边界；不再要求模型输出 Claim、Occurrence、Citation Binding、Evidence ID、supports 或 Evidence Ledger。

自动化端到端测试使用明确标记的 `DEMO/SYNTHETIC` 数据和本地 Writer fixture，验证了流程、有效 DOCX、TaskRun 关联和用户隔离。**真实登录、真实 RAG 和真实 Workspace 下载均为 NOT TESTED**；fixture 通过不代表生产知识库或登录浏览器验收通过。

## 模板与执行流程

新增 [s02_project_planning.v5.json](../templates/s02_project_planning.v5.json)，默认公开版本改为 `5.0.0`，历史 `1.0.0` 至 `4.1.0` 文件及 Registry 条目保留。五个步骤为：填写项目需求、检索授权项目资料与策划模板、生成八章报告、用户复核、应用反馈并生成 DOCX。节点数为 5，未添加格式化 Agent 或 Output StepType。

正文保留八章业务结构及 B-01～B-15 写作提醒；B 条款不再作为规则对象输出或执行专用逐条分类校验。两个 Agent 均只要求 Markdown 业务内容，不要求 Claim/Evidence 协议字段。项目资料或模板未命中时，不能伪造来源；模板检索工具失败仍会使 SOP 失败。

模板编译和 Registry 位置：

- [模板注册表](../_template_registry.py:18)：登记 v5.0.0；[默认版本](../_template_registry.py:56)指向 v5.0.0。
- [版本化 EvidencePolicy](../_scenario_evidence_policies.py:112)：增加 v5.0.0 的 S02 规则类型目录；v5 运行不调用逐条 Claim–Evidence verifier。
- [默认运行分派](../_service.py:2059)：标准 S02 v5 自动采用 `lightweight_draft`；该模式不适用于自由 Task、自定义流程或其他版本。

## 轻量校验及安全边界

[轻量 Markdown 校验器](../_s02_lightweight_output.py:45)只检查非空、UTF-8 大小上限、八章标题及顺序、各章正文和待核实标记，并拒绝底层协议围栏/锚点；不声明事实语义或逐条证据审计通过。

运行时在 [S02 版本判断与来源校验](../_service.py:391)中只匹配标准模板实例。最终 Agent 输出交给现有 Evidence 记录校验，再检查来源属于该 Run 选定的知识库；若 Run 明确选择项目资料 ID，项目资料来源须位于该清单；并检查来源为 ready，以及完整原文可用时的 SHA-256。身份、来源状态、来源类型、KB 范围或哈希不合格都会阻断 DOCX；权限仍由现有 TaskKnowledgeGateway 和知识库服务执行。轻量模式只省略 Claim 与 Evidence 逐条关联和语义支持审计，不跳过登录主体、授权检索或来源快照校验。

模板和项目资料分别沿用 `planning.template_retrieve` 中已有授权适配器；结果作为当前 TaskRun 检索节点快照读取并记录状态、知识库、来源、文档、分块和哈希。报告中显示模板与项目资料检索状态；未匹配内容须在业务正文保留待核实/待补充/待确认说明。

交付阶段要求最终输出通过轻量结构检查且当前 Run 存在检索记录；随后沿用 `spec_from_markdown`、现有 `generate_artifact`、DOCX Renderer 和 Artifact Writer，无新增引擎或额外模型调用。TaskRun/Artifact 标记 `draft_unverified`，报告中 `evidence_ledger_status=not_performed`，不提供 `evidence_ledger_passed=true`。DOCX 首页及页眉使用“项目策划初稿 · 尚未完成正式核验”标识。产物写入失败时 TaskRun 不会记录成功交付。

主要运行位置：

- [轻量来源快照校验](../_service.py:462)
- [最终节点结构与来源处理](../_service.py:3540)
- [v5 交付报告及 Artifact 门控](../_service.py:2640)
- [Artifact Writer 生成与草稿标识](../_service.py:2720)
- [TaskRun 请求与记录的 delivery_mode](../_models.py:650)

## 前端显示

Task 页面识别标准 S02 v5 后提交 `lightweight_draft`；结果面板显示八章、模板/项目资料检索状态、来源完整性检查、未运行逐条 Claim–Evidence 审计的说明，以及“尚未完成正式核验”草稿状态。DOCX 下载继续使用现有产物列表和 Task artifact 内容接口。

修改位置：[Task 运行页面](../../../web_ui/frontend/src/pages/task/index.tsx:850)、[Task 运行结果面板](../../../web_ui/frontend/src/pages/task/task-run-panel.tsx:724)、[Task API 类型](../../../web_ui/frontend/src/api/task.ts:24)。

## 修改文件

- `examples/agent_service/task/_s02_lightweight_output.py`：新增八章 Markdown 确定性校验。
- `examples/agent_service/task/templates/s02_project_planning.v5.json`：新增五步轻量模板。
- `examples/agent_service/task/_template_registry.py`：注册并设为默认 v5.0.0，保留旧版本。
- `examples/agent_service/task/_scenario_evidence_policies.py`：为 v5 增补版本化 S02 policy 目录；没有改变旧版本 policy。
- `examples/agent_service/task/_models.py`：TaskRun 的 delivery mode 增加 `lightweight_draft`。
- `examples/agent_service/task/_service.py`：仅对 S02 v5 分派轻量协议、检查 Run 来源快照、跳过 S02 专用台账、保存草稿状态并调用既有 DOCX 路径。
- `examples/web_ui/frontend/src/api/task.ts`、`examples/web_ui/frontend/src/pages/task/index.tsx`、`examples/web_ui/frontend/src/pages/task/task-run-panel.tsx`：请求类型、版本分派和草稿状态展示。
- `tests/task_s02_lightweight_template_test.py`：新增 v5 TaskService/SOP/DOCX/授权边界集成测试。
- `tests/task_template_version_test.py`、`tests/task_s02_v4_delivery_test.py`、`tests/task_nine_scenarios_test.py`：更新默认 S02 版本断言并保留 v4.1 历史语义测试。
- 本报告。

本阶段没有修改 `src/agentscope/**`、`main.py`、共享认证、知识库核心、数据库迁移、依赖或 Task 外业务模块。没有执行 `git add`、commit 或 push。工作区中已有的其他未提交和未跟踪内容均保留。

## 验证结果

| 检查 | 结果 |
|---|---|
| S02 v5 定向测试 | PASS：6 passed |
| Task 全量 pytest（`tests/task_*_test.py`） | PASS：311 passed，2 skipped，21 subtests passed |
| 跳过项 | NOT TESTED：PostgreSQL 专项测试要求显式提供一次性测试库变量 `LXSCOPE_TASK_TEST_DATABASE_URL`；本次未配置，不代表真实 PostgreSQL 集成通过 |
| Task Python compileall | PASS：`python -m compileall -q examples/agent_service/task` |
| Task 前端 ESLint | PASS：针对 `task.ts`、`index.tsx`、`task-run-panel.tsx` |
| 前端生产构建 | PASS：`pnpm build` 成功；Vite 提示现有构建有大体积 chunk 警告，不影响退出成功 |
| `git diff --check` | PASS；Git 仅提示工作区 LF/CRLF 转换信息 |
| 真实用户登录 | NOT TESTED |
| 真实知识库 RAG | NOT TESTED |
| 真实 Workspace 文件下载/权限 | NOT TESTED；测试仅验证 TaskService 用户隔离和 fixture Writer 保存读取 |

专项测试验证：默认 v5 实例化不调用 Planner；五个 SOP 节点执行并接受用户复核；模型不需要 Claim/Quote/supports 字段；DOCX 可由 `python-docx` 重新打开；Artifact 关联正确 Run 且状态为 `draft_unverified`；其他用户无法通过 TaskService 读取该产物；错误授权主体、跨 KB、错误哈希或非 ready 来源不生成 DOCX。历史 v1/v2/v3/v3.1/v3.2/v4/v4.1 和其他八个场景纳入回归。

## 尚待真实环境验收

使用合法登录用户在 Task 页面创建默认“项目策划 5.0.0”模板，选择当前用户可访问的知识库并执行。确认检索节点显示实际模板与项目资料状态，提交复核意见，检查终节点显示“项目策划初稿／尚未完成正式核验”，然后通过现有产物下载入口下载 DOCX。需分别确认：真实 RAG 文档与授权范围、真实 Workspace 保存和跨用户拒绝；本地自动化 fixture 不替代这些验收。正式逐条 Claim–Evidence 审计、HumanApproval 和正式签发不属于 v5 轻量草稿能力。
