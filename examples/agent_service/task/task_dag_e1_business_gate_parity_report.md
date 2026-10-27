# Task DAG-E1 业务门禁等价适配报告

日期：2026-10-23  
分支：`integrate/dev_l`  
基准提交：`e63c3b80278dba2db5ddfd8e195b988481cabd1e`

## 结论

当前九个官方版本化模板都通过了**合成数据、真实 TaskService 调用链中的原线性 DAG 副本**与 SOP 对照执行。节点输入、节点输出、业务断言、最终输出、Artifact 内容及交付状态均通过测试比较。S02 v5.1 仍是 `draft_unverified`，证据账本保持 `not_performed`，并要求人工复核。

这不代表已开放任意场景 DAG。场景副本只有在精确模板版本获隔离测试实例显式授权，并且节点、契约、Artifact、依赖与原模板线性形状完全一致时才能执行。非线性场景 DAG 仍可编辑和保存，但 Preflight 与 `create_run` 均会阻断。应用默认不传测试授权，生产版本化场景 DAG 仍关闭。**生产准入：NO-GO。**

本阶段没有真实模型、外部 MCP/业务 API、Redis 或 PostgreSQL 验收。数据库集成用例因缺少专用测试 URL 而跳过，不能据此宣称真实存储通过。

## 1. SOP 与 DAG 实际调用路径

### SOP

`TaskService.create_run` 选择 SOP 引擎后，通过 Task 层 SOP 执行循环逐节点执行。节点结果经过现有 Agent 验收、Tool 结果检查、冻结输出 Schema/能力契约和场景业务适配；完成所有节点后，SOP 收尾检查节点成功状态、执行 S02 交付终检，并在满足 Artifact 门禁后写入产物。

### DAG

`TaskService.create_run` 先运行 TaskGraph Validator，再检查 DAG 执行开关及版本化场景安全门禁。Task DAG 调度器派发 ready 节点；节点仍复用 Task 的 SOP step/executor 路径和现有节点结果门禁。完成后 `_finalize_dag_run` 在终态提交前验证冻结模板来源、唯一终端节点、全部节点成功、Agent 验收记录、输出契约审计，以及 S02 场景终检。检查通过后才写 Artifact，并提交交付状态和 `artifact.created` 事件。

本阶段没有修改 DAG 调度算法、Run 状态机、CAS、Lease、Fencing、Checkpoint 或 Retry/Pause/Continue/Step/Stop 语义。

### 主要适配点

- `examples/agent_service/task/_service.py:1049`：增加 TaskService 内显式测试授权参数；默认空集合，未从环境变量读取，应用默认构造不传该参数。
- `examples/agent_service/task/_service.py:1683`：版本化场景 DAG 执行门禁验证模板来源及版本、用户自定义副本身份、冻结步骤绑定、节点顺序/类型/能力、Schema、输出契约、Artifact 配置、线性边、无额外输入绑定及并发度 1。
- `examples/agent_service/task/_service.py:1937` 与 `:2637`：Preflight 和 `create_run` 均调用同一场景门禁；前端或调用方不能通过伪造编辑状态绕过执行校验。
- `examples/agent_service/task/_service.py:5035`：符合原模板线性兼容剖面的 DAG 节点接收前序节点原始输出，与 SOP handoff 对齐；一般 DAG 仍通过 DAG 输入绑定解析结构化输入。
- `examples/agent_service/task/_service.py:526`：抽取 SOP/DAG 共用的 S02 交付状态投影。
- `examples/agent_service/task/_service.py:5069`：DAG 交付适配器检查冻结模板和终端、逐节点成功状态、Agent 验收记录、冻结输出契约审计；S02 v5.1 还要求来源完整性、章节结构、轻量 AI 验收通过，并保持 `draft_unverified`。
- `examples/agent_service/task/_service.py:5197`：业务门禁通过后才生成/写入 Artifact；S02 无冻结 DOCX 配置或产物状态与本次终检不一致时拒绝收尾。

## 2. SOP/DAG 门禁差异矩阵

| 项目 | SOP | DAG 当前实现及 E1 结果 |
|---|---|---|
| 节点执行 | Task SOP 顺序执行；每节点进入现有 Task step/executor 与验收路径 | Task DAG 按 ready 集合调度；执行仍复用 Task 节点路径；九个线性副本输入/输出与 SOP 对照通过 |
| 来源与快照 | 按冻结模板版本和 Run 快照验证 | 场景 DAG 必须保留精确来源及冻结节点映射；来源或版本无法解析即阻断 |
| Agent AI 验收 | 通过记录写入 NodeRun，失败则不能以成功结果完成 | 收尾逐节点要求 `task_ai_verification.passed=true`；S07 失败注入证明 Run failed 且无 Artifact |
| Schema/能力契约 | 节点执行期间验证冻结 Schema/契约 | 节点执行复用既有验证；场景 DAG 收尾要求相应 `output_contract_validation.status=passed` |
| S02/S07 证据 | 走 Task 内版本化来源与引用/Claim–Evidence 门禁 | 节点门禁复用现有业务适配；S07 使用合成授权知识库；S02 v5.1 保持未核验证据状态。真实知识库未验收 |
| Artifact | 场景最终验收后生成，业务交付状态随产物保存 | DAG 收尾先验证业务门禁再写 Artifact；九场景合成产物内容/格式及交付状态与 SOP 相符 |
| 非线性场景图 | 不适用 | 可保存编辑；当前 fail closed，不进入 Run，避免未证明的并行汇合语义改变业务门禁 |
| 生产执行权限 | 既有 SOP 行为保持 | 版本化场景 DAG 默认阻断；本阶段授权仅为显式注入 TaskService 的隔离测试参数 |

## 3. S01～S09 独立验收矩阵

以下执行结果均来自当前 TemplateRegistry 的精确版本，经 `TaskTemplateService.instantiate`、`TaskService.create_custom_flow_copy(mode="dag")`、TaskService Preflight、`create_run`、真实 Task DAG 调度和收尾完成；模型、知识库和工作区由确定性合成适配器提供。不是外部模型或业务工具集成验收。

| 场景 / 版本 | 当前节点与产物 | DAG 编辑/持久化 | 节点校验等价 | 业务终检等价 | 产物交付等价 | DAG 执行测试 | SOP 回归影响 |
|---|---|---|---|---|---|---|---|
| S01 `3.0.0` | 7 节点；Markdown | PASS：另存 DAG 且原线性边持久化；通用图保存/重开由 Task API 测试覆盖 | PASS：Input/Agent/Tool 节点执行；验收与契约审计通过 | PASS：R7 合成场景断言通过 | PASS：Markdown 内容与 SOP 对照一致；格式来自复制的冻结 Artifact 配置 | PASS：TaskService 合成 Run succeeded | PASS：全量回归无新增失败 |
| S02 `5.1.0` | 5 节点；DOCX | PASS：另存 DAG 且原线性边持久化 | PASS：冻结契约和轻量验证通过 | PASS：`lightweight_draft`、`draft_unverified`、`evidence_ledger_status=not_performed`、`human_review_required=true` 与 SOP 相同 | PASS：DOCX 内容/标签及 `draft_unverified` 状态与 SOP 相同；格式来自冻结配置 | PASS：TaskService 合成 Run succeeded | PASS：保留原 SOP 行为；3 项历史协议失败未改 |
| S03 `3.0.0` | 8 节点；Markdown | PASS：另存 DAG 且原线性边持久化 | PASS：人工 InputStep/Tool/Agent 节点及输出门禁通过 | PASS：推荐与回避规则的合成断言通过 | PASS：Markdown 内容与 SOP 对照一致；格式来自冻结配置 | PASS：TaskService 合成 Run succeeded | PASS：无新增失败 |
| S04 `3.0.0` | 7 节点；DOCX | PASS：另存 DAG 且原线性边持久化 | PASS：成本及材料相关节点门禁通过 | PASS：费用和会议材料合成断言通过 | PASS：DOCX 内容与 SOP 对照一致；格式来自冻结配置 | PASS：TaskService 合成 Run succeeded | PASS：无新增失败 |
| S05 `3.0.0` | 6 节点；DOCX | PASS：另存 DAG 且原线性边持久化 | PASS：转写、Agent 与人工审阅节点门禁通过 | PASS：纪要/行动项合成断言通过 | PASS：DOCX 内容与 SOP 对照一致；格式来自冻结配置 | PASS：TaskService 合成 Run succeeded | PASS：无新增失败 |
| S06 `3.0.0` | 6 节点；XLSX | PASS：另存 DAG 且原线性边持久化 | PASS：意见识别、分类、审阅节点门禁通过 | PASS：意见维度汇总合成断言通过 | PASS：XLSX 内容与 SOP 对照一致；格式来自冻结配置 | PASS：TaskService 合成 Run succeeded | PASS：无新增失败 |
| S07 `5.0.0` | 5 节点；DOCX | PASS：另存 DAG 且原线性边持久化 | PASS：合成授权知识库来源、Agent 验收及契约记录通过 | PASS：来源完整性/报告业务断言通过；Agent 验收失败时 Run failed | PASS：DOCX 内容与 SOP 对照一致；Agent 验收拒绝时无产物 | PASS：成功及拒绝两条 TaskService 合成链路 | PASS：无新增失败 |
| S08 `3.0.0` | 6 节点；XLSX | PASS：另存 DAG 且原线性边持久化 | PASS：清单提取、归类、完整性门禁通过 | PASS：归档清单合成断言通过 | PASS：XLSX 内容与 SOP 对照一致；格式来自冻结配置 | PASS：TaskService 合成 Run succeeded | PASS：无新增失败 |
| S09 `3.0.0` | 6 节点；XLSX | PASS：另存 DAG 且原线性边持久化 | PASS：票据识别、去重、费用规则与 Agent 门禁通过 | PASS：金额一致性/重复风险合成断言通过 | PASS：XLSX 内容与 SOP 对照一致；格式来自冻结配置 | PASS：同 Run Pause、Checkpoint 恢复、Continue 后完成 | PASS：无新增失败 |

**场景级边界：**以上 PASS 仅覆盖每个场景的原线性 DAG 兼容剖面，不代表编辑后的并行场景 DAG 已通过业务终检。另存与原线性图生成覆盖全部九个模板；单独的图编辑/保存/重开用例覆盖通用 Task Graph API（模板派生图的编辑验证有 S01 用例），不是九个场景逐一的浏览器画布验收。E1 对 S07 单独验证了 Agent 验收失败不产物；未为其余八个场景各自注入不同类型的不合格业务输入。异常门禁代码通过同一节点执行及冻结契约路径复用，但逐场景不合格数据矩阵仍未完全穷举，故不得将结果表述为完整负向业务验收。

## 4. 交付门禁、控制及恢复测试

### 已实测

- 九场景：每个版本均执行一次 SOP 与线性场景 DAG TaskService Run；比较各节点输入、输出、结果类型/结构、最终输出、业务断言、Artifact 内容和交付状态。Run 事件以 `run.succeeded` 结束。
- S02 v5.1：`draft_unverified` 与 SOP 一致；证据账本明确未执行完整核验，要求人工复核；产物带“未完成完整证据核验”标记。
- S07：最终 Agent 被确定性测试替身拒绝后，Run 为 failed，最终节点保留失败验收记录，`final.artifacts == []`，Artifact writer 未调用。
- 场景来源安全门禁：未注入精确模板版本测试授权时，Preflight 有 `graph.scenario_execution` 问题，`create_run` 抛出冲突且不创建 Run。
- 编辑后执行门禁：测试图改变 `max_concurrency` 后可保存；Preflight 和 `create_run` 均拒绝运行。
- S09：在一个 Agent 节点在途时请求 Pause，等待节点安全边界；成功前驱 NodeRun 快照不变。用同一隔离 TaskStore 重建 TaskService 后仍为 paused，Continue 后沿同一 Run 完成，已成功前驱的 attempt、输出和起止时间不变。
- 泛化 DAG/SOP 控制、Retry、状态、重启、模板复制/图校验等专项继续由现有 Task 测试覆盖。并行 DAG 调度和输入汇合是通用 DAG 回归，不是版本化场景图执行验收。

### 未覆盖或受限

- E1 未对九个场景分别执行失败节点重跑、Pause/Step/Continue/Stop 全套；S09 有暂停/重建/继续用例，其余控制由通用 SOP/DAG 回归覆盖。
- 版本化场景 DAG 当前禁止非线性图，所以“场景 DAG 并行、汇合及多前驱业务终检等价”仍为 BLOCKED。一般自定义 DAG 的调度器测试不替代该场景门禁证明。
- 没有浏览器操作或 Task 前端测试；E1 是后端 TaskService 合成验收。
- 没有真实模型、生产 MCP、ERP、审批或真实业务 API 调用。S01/S02/S07 相关知识检索使用 Task 专属合成授权知识库适配器。
- 没有专用 `LXSCOPE_TASK_TEST_REDIS_URL` 或 `LXSCOPE_TASK_TEST_DATABASE_URL`；Redis/PostgreSQL 持久化、CAS、事务及多进程验收为 BLOCKED，未使用 Memory/SQLite/fakeredis 替代真实数据库结果。

## 5. 测试结果及历史失败基线

### 本阶段测试

| 测试 | 结果 |
|---|---|
| `pytest tests/task_dag_e1_business_gate_test.py -q --tb=short` | 13 passed |
| E1 + DAG/SOP 控制、复制/编辑、调度、重跑、R7 场景、P2-G/P2-H 相关组合定向回归 | 160 passed，18 skipped；跳过项需要隔离 Redis/PostgreSQL URL |
| 完整 `tests/task_*.py`（阶段开始） | 476 passed，3 failed，18 skipped，30 subtests passed |
| 完整 `tests/task_*.py`（阶段结束，最终复跑） | 489 passed，3 failed，18 skipped，30 subtests passed（133.92s） |
| `compileall`（`_service.py` 与 E1 测试文件） | PASS |
| Ruff | 未安装，未执行 |

完整套件比阶段开始多 13 个通过项，正好是 E1 新增的 13 项测试。最终复跑 JUnit 与阶段开始报告比较后，失败测试 ID 集合及三项失败的异常类型、规范化断言摘要和首个测试源码堆栈行完全一致。

### 保留的 3 项历史 SOP 协议失败

| 测试 | 基线与结束签名 | 基线原因 |
|---|---|---|
| `tests/task_p7r1_output_contract_test.py::TaskP7R1ServiceOutputContractTest::test_sop_finalizer_builds_citation_index_then_runs_full_validation` | `AssertionError: 'failed' != 'succeeded'`，首个测试帧均为 `:2034` | 合成正文在 `task-citations` 块后仍有内容，触发 `content_after_citation_block`；Claim–Evidence envelope 不完整。当前 fail closed 行为将 Run 置 failed，与历史测试预期不一致。 |
| `tests/task_p7r1_output_contract_test.py::TaskP7R1ServiceOutputContractTest::test_sop_rejected_repair_keeps_body_and_fails_closed` | `AssertionError`：期望 `task_protocol_repair` 元数据，但结果中未出现；基线/结束测试帧相同 | fixture 引用块缺失且 Claim–Evidence 不完整，引用核验失败；历史断言预期修复审计记录。 |
| `tests/task_s02_content_repair_test.py::TaskS02ContentRepairSOPTest::test_s02_v2_repairs_only_the_mixed_occurrence_then_runs_full_ledger` | `AssertionError: SOPPhase.FAILED != SOPPhase.COMPLETED`，首个测试帧均为 `:441` | `task-citations` 封套后存在内容，且 Claim–Evidence 不完整；严格引用/协议核验拒绝完成。 |

本阶段没有修改上述测试、断言或历史业务规则。失败集合及异常/断言/测试堆栈签名与阶段开始一致；没有发现新增 Task 失败。

## 6. Redis/PostgreSQL 及外部风险

- PostgreSQL：BLOCKED。完整套件中依赖专用 PostgreSQL URL 的用例被跳过；本阶段没有连接任何容器或数据库。
- Redis：BLOCKED。完整套件中依赖专用 Redis URL 的用例被跳过；本阶段没有连接任何 Redis 实例。
- 外部模型/知识库/业务服务：BLOCKED。通过确定性合成模型、授权知识库和工作区替身验证 Task 门禁，不代表真实服务互操作或业务数据准确性。
- 浏览器：未执行；不构成本阶段的 E1 PASS 项。
- 可靠性风险：在真实 Redis/PostgreSQL、独立 Worker 故障恢复和真实 Artifact 持久化验证前，不得将版本化场景 DAG 扩展到生产非线性执行。

## 7. 修改文件与 Git 对照

### 本阶段新增/修改

- 修改：`examples/agent_service/task/_service.py`（该文件在阶段开始前已经有工作区改动；下列 E1 行为是本阶段补充，不将整份现有 Git diff 归为 E1）。
- 新增：`tests/task_dag_e1_business_gate_test.py`。
- 新增：`examples/agent_service/task/task_dag_e1_business_gate_parity_report.md`（本报告）。

没有修改模板、AgentScope 主框架、`main.py`、前端、全局配置、依赖或数据库 Migration；没有提交代码。

### 工作区快照

- 阶段开始快照：`%TEMP%\task-dag-e1-start-20261023-153117`，包含 `status-short.txt`、`status-porcelain-v2.txt`、`diff-name-only.txt`、`head.txt`、`branch.txt` 及完整 Task 基线日志/JUnit。
- 阶段开始已有 108 项 status 条目，其中包含多个 Task、前端及白名单外历史工作区改动；均未清理或覆盖。
- 比较阶段开始和结束的 `git diff --name-only`：已有 tracked diff 路径集合没有改变；`_service.py` 的路径在阶段开始已存在于 diff 清单中。
- 比较 `git status --short`：除本阶段新增的 E1 测试与本报告两项白名单内路径外，没有其他新增状态项。已有白名单外变更保持在原快照中。
- Git diff 未提交。由于 `_service.py` 阶段开始时已经 dirty，工作区没有阶段开始文件内容快照；因此不能把它相对 HEAD 的整份 diff 作为 E1 独立补丁。本报告按本阶段新增的函数/行为描述变更。

## 8. P2/E1 准入判断

**允许继续在隔离环境开展后续场景兼容验证；不允许进入生产启用。**

目前已证明九个当前版本的原线性场景副本能经 TaskService DAG 执行并复用节点及收尾业务门禁。测试授权是 TaskService 显式进程内参数，不来自环境变量；常规服务默认不携带授权。只有针对特定场景完成并记录非线性业务终检、Artifact/失效重跑、真实持久化并发和外部依赖验收之后，才可评估扩大该场景的隔离执行范围。所有未通过场景继续 BLOCKED；生产 DAG 继续关闭。
