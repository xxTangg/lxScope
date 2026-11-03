# P6-D-2G：S02 项目策划来源证据链与最终报告引用一致性修复报告

## 1. 验收结论

- 当前分支：`integrate/dev_l`。用户给出的开发基准为 `137c64e`；检查时工作区 HEAD 为 `1418f2b`。
- Task 专项全量测试：**151 passed，2 skipped，2 subtests passed**。
- `git diff --check`：通过；只有 Git 关于 LF/CRLF 的提示。
- Black 检查：`uvx --from black==23.3.0 black --check --line-length=79 examples/agent_service/task/_citation_verifier.py examples/agent_service/task/_s02_project_planning_evidence.py tests/task_project_planning_evidence_chain_test.py` 通过，三个文件未被改写。
- S02 v1 模板：未修改，模板文件 diff 为空。
- 真实知识库和真实 Workspace 端到端：**未验收 / BLOCKED**。本机打开 `http://localhost:8000/task` 后被重定向至 `/chat`，页面提示“无法连接服务器”；未取得已登录 Task 会话，也未读取用户所述失败 Run 的真实节点记录。测试中的知识库、Artifact Writer 均为隔离测试夹具，不能视为真实数据联调。
- 本阶段没有创建、下载或声称生成真实 Workspace DOCX。正式语义蕴含检查仍未实现；引用台账会明确要求人工复核。

## 2. 失败原因与数据传递确认

由于当前本机服务不可连接，无法核验用户那次运行的逐节点真实输出。本节将源码能够确认的原因与测试复现结果分开描述，不把推断当作运行日志。

### 2.1 代码确认的执行与持久化路径

1. `TaskSOPStep` 将 `StepExecutionResult.text` 和 `.result` 写入 SOP 状态（`examples/agent_service/task/_sop.py:84`）。
2. `NodeRunRecord` 持有每个节点的 `input`、`output`、`result` 和 `metadata`（`examples/agent_service/task/_models.py:448`）。`TaskService._checkpoint_sop` 将官方 SOP 每步的 `record.output`、`record.result`、metadata 和校验记录投影回 TaskRun（`examples/agent_service/task/_service.py:2604`）。Redis 保存 TaskRun JSON 快照；Postgres 保存 TaskRun snapshot JSON（`examples/agent_service/task/_redis_store.py:117`、`examples/agent_service/task/_postgres_store.py:261,373`）。本修复将来源证据保存在已有检索节点的 `result` 中，不改 TaskRun 模型或迁移。
3. `planning.template_retrieve` 运行时先检索模板，再独立检索项目委托资料；两类原文分别标记 `planning_template` 和 `project_material`，组合后保存于同一检索节点结果（`examples/agent_service/task/_service.py:2227`）。InputStep 选择的 `source_material_ids` 会合并进同一 TaskRun 并作为授权检索范围传入。
4. 后续节点通过同一 TaskRun 检索节点快照读取证据；专项集成测试断言输入节点中选中的文档 ID 已保存、引用核验节点收到前序草稿、最终整理输出再次验引（`tests/task_project_planning_rag_test.py:1062-1194`）。

### 2.2 A、B、C 三类核验必须分开

- **A：引用编号集合**：正文使用的 `[C1]` 等编号与 `task-citations` 列表一致性。正文和引用区块都包含 C1/C2 时，不会仅因两边集合相同而误报“编号集合不一致”。最终整理另行检查每个引用编号只在正文出现一次，并要求台账逐项映射。
- **B：来源身份与原文摘录**：确定性校验核对 KB ID、文档 ID、chunk index、内容哈希是否属于本次运行授权检索快照，并确认 `quote` 是对应保存原文的连续子串。
- **C：语义支持**：当前系统不能自动证明某原文确实支持该正文事实或 B 条款结论。结果显式返回 `semantic_support: not_automatically_evaluated`，逐条状态说明“语义支持需人工复核”。B 条款检查只要求对应 B 编号出现在模板引用原文，且去除编号后至少有 8 个字符；这是最低限度的条款内容检查，不代表语义蕴含验证。

源码依据：通用 A/B 确定性检查位于 `_citation_verifier.py:34`，编号提取/去除和重复 JSON key 检查位于同文件 `203`、`209`、`236`；S02 声明映射和台账门禁位于 `_s02_project_planning_evidence.py:231`。

### 2.3 本次修复确认的根因

- 原 S02 运行检索链只为 `planning.template_retrieve` 保存策划模板证据；模板证据不能支持面积、设备台账、预算、周期等项目事实。项目简报是 InputStep 输入，不是知识库原文，不能当作已检索来源。
- 引用核验 Tool 的 A/B 通过，只证明编号、来源定位和逐字摘录匹配；不证明 C，即不证明 C1/C2 足以支撑所有 B-01～B-15 条款或项目事实。失败不能笼统归为“编号不一致”。
- 最后一个 Agent 会生成/改写报告。只复用较早引用核验节点的通过结果会造成验收对象错位。因此最终整理节点现在基于本次 TaskRun 的同一份来源快照重新校验其最终输出，并由服务端生成来源台账；失败则返回 failed，SOP 不会进入 Artifact Writer。

上述根因是根据执行链源码及新增确定性测试得到的代码级结论；受本机服务连接失败影响，未能将其与用户所述某一次真实 Run 的日志逐字段对照。

## 3. 实际修改文件

本阶段实际改动均在 Task 白名单内。下列 `_knowledge.py`、`_service.py` 和 `tests/task_project_planning_rag_test.py` 在本阶段开始前已存在 P6-D-2F 未提交修改；本阶段在其上增量修改，未覆盖已有内容。

| 文件 | 修改内容 |
| --- | --- |
| `examples/agent_service/task/_knowledge.py`（`retrieve_project_material_evidence` 从 429 行起；模板检索从 623 行起） | 继续复用现有用户授权知识库适配和检索；调用独立 S02 策划模板与项目资料检索方法，保存来源原文及诊断。这里不再定义八章/B 条款/项目事实判定规则。 |
| `examples/agent_service/task/_service.py`（输入读取 202/218 行；检索 2227 行；最终校验 2404 行） | 从 InputStep/TaskRun 收集所选资料 ID；运行时执行模板与项目资料两种分离检索并将证据快照写入现有检索节点结果；S02 最终 Agent 输出进入 S02 专属台账检查，失败时使节点失败。 |
| `examples/agent_service/task/_citation_verifier.py`（34、203、209、236 行） | 仅处理通用引用编号、授权证据身份、精确原文摘录及 JSON duplicate-key 检查；提供可复用的引用 marker 提取/去除方法。无八章、B 条款或项目指标规则。 |
| `examples/agent_service/task/_s02_project_planning_evidence.py`（新增；查询意图 22 行，模板/项目判定 124/171 行，台账 231 行） | 隔离 S02 检索意图/相关性策略、项目资料分类、B-01～B-15 和项目事实规则、S02 输出声明映射及报告台账生成。 |
| `tests/task_project_planning_evidence_chain_test.py`（新增；检索类 157 行，台账类 378 行） | 覆盖授权项目资料检索、跨 chunk 去重、无匹配、权限拒绝、来源类型路由、B 条款摘录、待核实台账、A/B/C 区分、跨 chunk 引用定位、同一摘录被不同事实复用时拒绝。 |
| `tests/task_project_planning_rag_test.py`（端到端测试从 1062/1196 行起） | TaskService 隔离测试：InputStep 所选资料写入 Run，引用核验节点的输入与前序草稿一致，最终输出经二次核验；拒绝错误来源的最终正文且不调用 Artifact Writer。 |
| `examples/agent_service/task/docs/P6-D-2G-evidence-chain-report.md` | 本报告。 |

未修改 S02 v1 JSON，也未修改其他八个系统模板定义。

## 4. 项目资料检索与证据快照

项目资料检索新增三个固定意图查询，分别覆盖项目委托概况、面积/设备台账及预算/工期；第一条查询仅在有项目名称时添加截断后的项目名称，不拼接完整 brief。检索意图和 S02 相关性策略放在 `_s02_project_planning_evidence.py:22`；`TaskKnowledgeGateway` 在 `_knowledge.py:429` 复用已授权的通用检索入口。

实现复用 `TaskKnowledgeGateway.inspect_task_scope` 与 `retrieve_task_evidence`。每轮查询仍通过现有用户作用域知识库视图、文档状态及选定资料 ID 校验。候选片段按 `(knowledge_base_id, document_id, chunk_index, content_sha256)` 去重，再按同一已授权文档聚合判断；系统模板、历史案例、会议纪要和指南不会因“知识库中有任意文档”而被当成项目委托资料。无匹配返回 `not_found`、真实 query 和命中文档诊断；权限或服务错误保持失败关闭。

成功片段从现有检索结果保留 `knowledge_base_id`、`knowledge_base_name`、`document_id`、`source`、`chunk_index`、`content_sha256`、`source_text` 和 ready 状态。没有直接复制 brief 充当原文，也没有生成缺失的来源元数据。TaskRun 已有 `node_runs[].result` 可持久化这份证据，不需要新增字段或迁移。

## 5. 最终引用与台账协议

S02 最终整理 Agent 输出唯一的 `task-evidence-claims` JSON 映射，声明正文事实、章节、类型与引用 ID；Agent 不提供核验状态或来源元数据。S02 专属模块随后：

1. 先对最终 Agent 输出运行既有 `task-citations` 确定性 A/B 检查。
2. 要求每条已引用事实在映射中出现，编号与事实正文一致，正文无孤立编号或重复复用编号。
3. 项目事实必须映射到 `project_material` 证据；模板规则必须映射到 `planning_template` 证据。B-01～B-15 标记需由同一条模板原文引用对应编号和具体摘录内容支撑。
4. 项目面积、设备台账、预算、周期等无对应来源事实必须显式列为“待核实”；没有项目资料证据时四类必须全部有待核实条目。建议/拟定目标等需要在正文中明确标记。
5. 服务端根据本次检索快照生成“来源与证据台账”，逐项列章节/事实、引用号、资料名、原文片段、来源定位和 SHA-256，以及“已落条”或“待核实”状态。没有来源时不填造来源字段。
6. 台账物化后再运行一次确定性引用校验。任何失败均在最终节点返回失败，不进入既有 Artifact Writer。

项目事实识别和 B 条款内容检查属于确定性、保守的 S02 规则，可能无法识别所有隐含事实，也不能证明语义蕴含。报告的“已落条”严格只代表编号、授权来源身份与逐字摘录核验通过，语义判断仍需人工复核。

### 5.1 可复用组件

- 现有 `TaskKnowledgeGateway.inspect_task_scope`、`retrieve_task_evidence`：执行用户作用域、选定文档及文档状态检查；没有新增 RAG 或知识库权限路径。
- 现有 TaskRun `node_runs[].result` 来源快照：存储本次运行证据及 hash/chunk 元数据，Redis/Postgres 沿用现有序列化和快照持久化。
- `_citation_verifier.py`：通用引用 ID 集合、授权证据身份、原文精确摘录检查；`extract_task_citation_ids`、`strip_task_citation_ids` 和 `unique_json_object` 可供后续 Task 证据协议复用。
- 现有 Artifact Writer 与最终 SOP 完成门禁：最终节点失败时不进入文件生成路径，没有新增文件写入机制。

### 5.2 S02 专属逻辑

- 策划模板和项目委托资料的查询词、来源分类以及跨 chunk 相关性判断。
- B-01～B-15、八章大纲以及项目面积/设备台账/预算/周期的 S02 报告规则。
- `task-evidence-claims` 的 S02 声明类型、项目事实与模板规则来源映射、待核实清单和最终 Markdown 台账。

以上规则集中在 `_s02_project_planning_evidence.py`；通用 `_citation_verifier.py` 和知识库核心中不保留这些场景常量。测试文件中的目标样例名称仅用于 fixture，不进入生产规则。

### 5.3 下一阶段建议抽象的能力

本阶段不实施九场景通用化。后续确认进入通用证据链重构后，可先设计并评审：

1. 与业务场景无关的 `EvidenceSnapshot` 协议，包括授权主体、KB/文档/chunk/hash、原文截取边界、采集时间和快照版本。
2. 通用来源授权/完整性检查与引用编号一致性协议，明确来源类型由 Task/模板声明而非模型自报。
3. 可配置的业务证据策略接口，让场景策略声明 claim 分类、来源类别和人工复核要求，不将 S02 的字段/条款规则编入通用 verifier。
4. 独立的证据关联/语义充分性能力契约；在真实可验证实现前继续显示“需人工复核”，不把字符串规则升级为语义通过。
5. 基于真实 Redis/Postgres 以及登录 KB/Workspace 的持久化、用户隔离、恢复与文件验收矩阵。

## 6. 测试覆盖与结果

### 已运行命令

```powershell
$taskTests = rg --files tests | Where-Object { $_ -match '(^|[\\/])task_.*\.py$' }
.\.venv\Scripts\python.exe -m pytest $taskTests -q -rs
```

结果：**151 passed，2 skipped，2 subtests passed，40.96s**（S02/通用模块分离后的最终回归）。

另运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/task_postgres_sop_test.py -q -rs
```

结果：2 skipped；两项均因未配置 disposable PostgreSQL Task test URL。该结果不代表真实 PostgreSQL 集成通过。

```powershell
uvx --from black==23.3.0 black --check --line-length=79 examples/agent_service/task/_citation_verifier.py examples/agent_service/task/_s02_project_planning_evidence.py tests/task_project_planning_evidence_chain_test.py
git diff --check
```

两项均通过。没有修改项目依赖或构建配置。

### 覆盖项

- 固定意图查询未重复拼接项目 brief；从授权检索结果跨查询汇总同一文档的多个 chunk。
- 真实结构字段（在 fixture 中由知识库服务返回）逐项保留；未授权 KB/文档拒绝；只有模板/历史案例时项目资料状态为 `not_found`。
- 模板来源不能支持项目数值事实；模板条款引用必须包含 B 编号及实质摘录。
- 正文与清单均为 C1/C2 时 A 通过；来源身份/逐字原文属于 B；语义支持 C 显示未自动评估。
- 多 chunk 的引用保留各自 chunk/hash；同一精确摘录复用于不同事实会失败。
- 最终 Agent 改写后重新生成服务端台账并执行最终核验；错误来源的最终输出失败且 Artifact Writer 未被调用。
- TaskService 测试验证模板实例化的原有路径没有调用 Planner。

测试所用 KB API、数据和 Artifact Writer 是隔离 fixture；不能替代真实用户权限、真实语料质量或 Workspace 写入验证。

## 7. 真实知识库、Workspace 与未解决边界

- **真实 KB 检索：未验证。** 当前浏览器本机服务提示无法连接服务器，没有可用登录会话。本次不能报告测试资料文件的真实命中、真实 KB ID/文档 ID/hash，也不能确认生产语料会满足当前标题/正文相关性判定。
- **真实权限隔离：fixture 验证通过，真实用户隔离未联调。** 实现继续调用现有用户作用域 gateway；真实权限应在服务可用后复验。
- **真实 Workspace DOCX：未验证。** Fixture 仅确认 Task 在最终校验通过后调用既有 Artifact Writer，并在最终校验失败时不调用；没有真实 AgentScope Session/Workspace 绑定，因此不能宣称真实文件已写入或可下载。
- **语义支持：未自动验证。** 系统无法用当前确定性引用检查判断 C1/C2 是否充分支持正文中每个业务结论；台账明确要求人工复核，没有将 A/B 通过伪装成 C 通过。
- **PostgreSQL：未验证。** 两个专项测试因缺少一次性数据库 URL 而跳过。
- Preflight 仍只说明 Task 能力接线可用，不保证某个用户的授权知识库中实际存在合格项目资料；运行中项目资料未命中时，业务事实必须进入待核实台账。

真实闭环验收前，需要本机服务可连接、用户已登录且具备含真实授权委托资料的知识库，并绑定可写 Workspace；使用自定义 S02 Task 运行后逐项核对 Run 节点快照、来源字段和下载产物。以上条件不满足时，不能把当前测试结果升级成真实业务闭环通过。

## 8. 白名单与工作区保护

- 本阶段新增/编辑的后端、测试和报告文件均在 `examples/agent_service/task/**` 或 `tests/task_*` 范围内。
- 没有修改 `src/agentscope/**`、知识库核心、认证、`main.py`、迁移、共享 API、项目依赖或 Task 以外的业务代码。
- 检查时工作区还存在既有 Task 前端、销售台等未提交修改及未跟踪目录；本阶段未覆盖、暂存或提交这些内容，没有执行 `git add .`。
- 未提交代码；按本阶段要求完成报告后停止。
