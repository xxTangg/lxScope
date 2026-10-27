# Task DAG-E2：S01 非线性 DAG 试点验收报告

日期：2026-10-23  
分支：`integrate/dev_l`  
基线提交：`e63c3b80278dba2db5ddfd8e195b988481cabd1e`

## 结论摘要

- **S01 v3.0.0 非线性 DAG 的 TaskService 合成执行：PASS。** 仅通过 TaskService 显式精确模板测试授权运行；使用 TaskStore 内存隔离实例和确定性模型、工作区及授权知识库替身。分支并行、汇合等待、Artifact 门禁、同 Run 局部重跑和 Pause/Step/Continue/Stop 均有专项测试证据。
- **S01 浏览器另存、编辑、保存、重新打开：PASS。** 浏览器中的自定义副本保留了来源模板 ID/版本；保存后的 8 条边、9 个字段绑定和并发度 3 在重新打开后仍存在。
- **S01 浏览器实际 Run：BLOCKED。** 页面显示“暂不可运行”，服务端提示版本化场景 DAG 尚未通过场景交付终检等价审查。本阶段未绕过这一门禁、未创建浏览器 Run。
- **Redis/PostgreSQL 持久化与多进程：BLOCKED。** 未设置隔离测试连接 URL；真实数据库相关用例按设计跳过。TaskStore 内存测试不作为持久化验收。
- **其他 S02～S09 非线性图：BLOCKED。** 未扩大试点范围。
- **生产准入：NO-GO。** 不启用生产 DAG。外部真实知识库并发、Redis/PostgreSQL 持久化和浏览器实际 Run 尚未验收。

## 1. 范围、工作区保护与实际改动

开始时记录的 Git 状态快照位于 `%TEMP%\task-dag-e2-start-20261023-161030`，记录分支、HEAD、`git diff --name-only` 和 `git status --short`；当时工作区已有 110 项状态记录，且包含白名单外既有变更。本阶段没有清理这些变更，也没有提交。

本阶段新增或触及的文件：

| 文件 | 本阶段内容 | 起始快照状态 |
|---|---|---|
| `examples/agent_service/task/_service.py` | 增加仅针对 S01 v3.0.0 试点图的精确图匹配、显式输入绑定约束和输入快照解析；原文件在阶段开始前已是既有修改文件 | 已在开始快照中为 `M`，不能把整份工作树 diff 归为 E2 |
| `tests/task_dag_e2_non_linear_s01_test.py` | 新增 5 个 TaskService 专项测试 | 本阶段新增 |
| `examples/agent_service/task/task_dag_e2_non_linear_s01_acceptance_report.md` | 本报告 | 本阶段新增 |

没有修改官方模板、`main.py`、AgentScope 主框架、其他业务模块、前端、全局依赖、全局配置或数据库 Migration。浏览器操作只新建并编辑了已确认隔离测试环境中的一个用户自定义副本，没有修改官方模板或旧 Run。

最终 Git 校验：将当前 `git status --short` 与阶段开始快照逐行比较，新增的仅为 `tests/task_dag_e2_non_linear_s01_test.py` 和本报告；`git diff --name-only` 的受跟踪路径集合没有新增路径。`_service.py` 在开始时已是修改状态，本阶段又在其中增加了试点适配；由于没有开始时的该文件内容快照，Git 的整文件工作树 diff 混有此前改动，无法把整份 `_service.py` diff 全部归因于 E2。上表只列本阶段实际增加的逻辑，不把已有改动归到本阶段。完整状态仍保留开始时的无关修改。

## 2. S01 只读依赖分析

审查的官方来源为 `s01_project_init` **v3.0.0**（`examples/agent_service/task/templates/s01_project_init.v3.json`），七个冻结步骤为：

| 顺序 | step_key | 类型 | 输入/输出与业务作用 |
|---:|---|---|---|
| 1 | `collect_project_input` | Input | 收集 `project_name`、`project_brief`，以及可选 `industry`、`scope`；答案保存在 Run 的 InputStep 快照中 |
| 2 | `extract_project_profile` | Agent | 从已提供资料提取项目画像；输出供后续建议节点使用 |
| 3 | `search_similar_projects` | Tool | `project.similar_search`，只读授权资料检索，输出相似项目候选 |
| 4 | `check_project_rules` | Tool | `project.rule_check`，只读授权资料检索，输出规则候选，不作合规结论 |
| 5 | `draft_initiation_recommendation` | Agent | 汇合项目画像、相似项目和规则候选，形成待人工复核建议 |
| 6 | `review_project_facts` | Input | 人工核对或补充事实；不得被自动跳过 |
| 7 | `produce_initiation_card` | Agent + Markdown Artifact | 形成复核用立项信息卡；最终业务门禁通过后才登记产物 |

**并行判断：有条件通过。** 两个检索节点在冻结 SOP 中实际从同一个已保存 InputStep 答案构建查询；相似项目查询使用项目名/背景及可选行业，规则查询使用项目背景/行业/范围。Task 层原 SOP 的 `previous_output` 是相邻步骤输出传递；但这两个原生检索适配器的查询值来自持久化 InputStep 答案，并不消费画像 Agent 输出。因此，检索可在相同用户、知识库授权和来源范围下只读并行。并行的前提是先完成 InputStep，并以成功保存的答案构造字段级绑定；不能把未经结构化校验的 Agent 画像当作两个检索节点的唯一输入。

试点图保持以下必须顺序：

```text
收集项目资料 ─┬─> 识别立项字段 ───────────┐
              ├─> 检索相似项目 ───────────┼─> 形成立项建议 -> 核对项目事实 -> 输出立项信息卡
              └─> 核对立项规则 ───────────┘
```

汇合节点必须等画像、相似项目检索和规则候选三者成功；人工核对仍位于最终产物之前。失败分支重跑保留其他成功分支，只使受影响的汇合及下游失效。最终 Artifact 继续受 Task AI verifier、冻结输出契约及场景交付门禁约束。负向 verifier 测试证明最终验收拒绝时不生成 Artifact。

**外部并发限制：** 源码证明两个 Task 适配器都是授权范围内的只读检索，但没有通过真实知识库服务验证 SDK/后端并发安全；因此真实外部检索并发仍为 BLOCKED。

## 3. 试点授权、服务端约束和实现

`TaskService._is_s01_parallel_pilot_graph` 只匹配模板来源 `s01_project_init@3.0.0`、冻结的七个节点 ID 顺序、精确 8 条依赖、精确 9 个命名字段绑定、`max_concurrency=3` 且无资源限制。`_scenario_dag_test_blocker` 仍要求 TaskService 显式配置精确来源版本的测试授权，并核对模板来源、原节点配置、Prompt、类型、能力、Schema、输出契约、Artifact 配置及节点绑定。未授权的服务实例、S02～S09、不同 S01 版本、非本试点图和额外节点均 fail closed。

`_validate_template_dag_dependencies` 只对这张精确试点图豁免原 SOP 相邻步骤直接边校验；通用 Graph Validator 仍负责无环、悬空边、资源和输入绑定校验。`_s01_dag_retrieval_input_values` 将两个检索节点的绑定值与同一 Run 中成功的 InputStep 序列化答案逐字段核对，缺失/不一致即拒绝执行。`_dag_previous_output` 为试点中仅有一个前驱且没有显式命名绑定的节点保留前驱输出；显式绑定的检索和汇合输入走 DAG 结构化输入路径。

未修改 SOP 调度器、通用 DAG Scheduler、Run 状态机、CAS、Lease、Fencing、Checkpoint、Retry、Task API 路由或模板文件。浏览器服务实例没有精确 S01 测试执行授权；测试里的 allowlist 是直接传给隔离 TaskService 的，不会开放应用服务或生产 Run。

## 4. TaskService 完整调用链与运行证据

专项自动化测试使用真实 TaskService 路径：模板 Registry 读取并编译官方版本 → TaskTemplateService 另存自定义副本并保留来源 → `TaskService.update_task` 保存候选 DAG → Graph/模板依赖验证 → `preflight_task` → `create_run` 冻结 Run 图快照 → TaskService DAG 调度、节点执行、检查点/事件/Attempt 更新 → `retry_node` 和控制方法 → 场景交付门禁与 Artifact 注册。自动化测试直接调用真实 TaskService，而不是经 ASGI/HTTP 客户端；因此不声称专项自动化覆盖了 Router 序列化或 HTTP 传输。

浏览器的另存与保存操作走现有 Task 页面。对应 Router 契约可在源码中核对：`POST /tasks/{task_id}/custom-flow-copy`、`PATCH /tasks/{task_id}`、`POST /tasks/{task_id}/preflight`、`POST /tasks/{task_id}/runs`。本阶段没有捕获浏览器 Network 面板请求/响应；由于页面禁用了版本化 S01 DAG Run，后两个端点没有通过浏览器调用。此处列出的是源码路由契约，不冒充抓包结果。

隔离测试替身只覆盖模型与工作区和授权知识库边界；没有调用真实模型、MCP、ERP、生产 Redis 或 PostgreSQL。TaskService、模板解析、Graph Validator、DAG 调度、InputStep、输出验证、业务终检、Retry、控制和 Artifact 注册均为当前代码路径。

### 4.1 并行、汇合与成功产物

专项用例：`test_s01_parallel_retrievals_overlap_and_join_waits_for_both_branches`。通过 TaskService `preflight_task` 和 `create_run` 创建 Run；测试延迟相似项目分支，观察画像和规则分支成功、相似项目分支仍运行时，建议/人工核对/最终节点都保持 pending，且没有 Artifact。释放该分支后，Run 成功，所有七节点成功，Markdown Artifact 数量为 1。测试核对汇合节点的三个命名输入分别等于画像输出、相似项目结构化结果和规则候选结果；事件序列证明两个检索分支成功事件早于汇合节点成功事件。规则输出保持 `candidates_for_review`，`automated_compliance_conclusion=false`。

另一次诊断执行记录：Run `d12e2ecd0aae4d02b03ecbe717ee1995`，TaskService 返回 `engine=dag`，最终 `status=succeeded`、version 40；7 个冻结节点均为 succeeded、attempt 1；Artifact 数量 1，格式 Markdown。该 Run 使用内存 TaskStore，作为当次运行证据，不是持久数据库或浏览器 Run。

### 4.2 失败分支局部重跑

专项用例：`test_s01_failed_read_only_branch_retries_in_same_run_and_preserves_other_branch`。Run `519919f20d3b42498e575c7c09518990` 的首次终态为 failed、version 22：项目输入、画像和规则检索成功且各 attempt=1；相似项目检索节点 `17931dbb10ce44ecb8d128e2b764fd64` 失败、attempt 1、历史含 failed；建议、人工核对和最终产物节点均 pending；无 Artifact。

重跑后节点证据：

| step_key | Node ID | 最终状态 | Attempt/历史 |
|---|---|---|---|
| `collect_project_input` | `f2a4bd09f92d4fed9ccfca17193e24c3` | succeeded | 1；输出及快照保留 |
| `extract_project_profile` | `39a693ddf57c403b9d59125793de660a` | succeeded | 1；输出及快照保留 |
| `search_similar_projects` | `17931dbb10ce44ecb8d128e2b764fd64` | succeeded | 2；`[failed, succeeded]` |
| `check_project_rules` | `7df4dcb10a7e48d381e1f2116f030c29` | succeeded | 1；输出及快照保留 |
| `draft_initiation_recommendation` | `a2a3e15f5efe4fc3b47e905dd6f2cc30` | succeeded | 1；失败期间 pending，重跑后执行 |
| `review_project_facts` | `62ebc1a73490433da4c0dadef6f1feeb` | succeeded | 1；失败期间 pending，重跑后执行 |
| `produce_initiation_card` | `f5b19424db744cd186b5ad30ca784ece` | succeeded | 1；Markdown Artifact 1 份 |

通过 `TaskService.retry_node` 对该只读失败节点发出重跑后，同一 Run 最终成功、version 48。相似项目节点 `17931dbb10ce44ecb8d128e2b764fd64` attempt=2，历史为 `[failed, succeeded]`；项目输入、画像及规则节点均保留 attempt=1 和原输出，测试还比较了重跑前后的节点快照与画像/规则调用次数，确认未重跑。汇合和两个后继节点随后成功，Markdown Artifact 数量 1。相同 `request_id` 重复 Retry 返回相同 run_version，幂等；失败期间下游未执行。

### 4.3 Pause、Step、Continue、Stop

专项用例：`test_s01_pilot_pause_step_continue_and_stop_keep_dag_control_semantics`。

- Run `0bfc03f179f74b338fdd6a540dea5d6a`：输入/画像已完成，两个检索分支同时 running 时请求 Pause；在途分支完成并保存后进入 paused/queued，汇合节点仍 pending。随后 Step 只执行汇合节点；测试验证执行器调用计数恰好增加 1，人工 InputStep 和最终 Artifact 节点保持 pending。Continue 后从检查点继续并完成，不重新执行有效成功节点。
- Run `387cf32406354b9382f513ee5e1c2a2c`：两个检索分支在途时 Pause，安全边界状态保存后执行 Stop；Run 进入 `canceled`、control_mode=`stopped`，未派发的后继节点变为 canceled。后续 Continue 被终态冲突拒绝。

这些 Run 是内存隔离用例的临时 ID；Pause/Step/Continue/Stop 断点的持久数据库重建仍未通过真实 Redis/PostgreSQL 验收。

### 4.4 业务门禁负向验收

`test_s01_final_verifier_rejection_blocks_artifact_on_parallel_graph` 将最终 Agent verifier 设为拒绝，断言 Run failed、最终节点失败且 Artifact 为空。`test_s01_non_linear_pilot_is_exactly_scoped_and_remains_test_authorized` 还验证错误并发配置、错误字段绑定被 Validator 拒绝；未给 TaskService 精确测试授权时，版本化来源 DAG 不可执行。E1 的 `test_current_scenario_linear_dag_reuses_sop_gates_and_delivery`、`test_source_template_dag_requires_exact_test_allowlist_and_stays_fail_closed` 和 `test_scenario_dag_rejected_final_agent_does_not_write_artifact` 通过专项回归。

## 5. 浏览器验收

- 浏览器入口：`http://localhost:8000`；Task 页面 URL：`http://localhost:8000/task/300ef9d5233442c6b8293839d310d6d8`。使用现有本地登录会话，未绕过认证。
- 在官方“项目立项”来源 Task `dcd78446dd21419b8755e2cd636ac3b2` 的页面，通过“另存为 → 转换为 DAG”创建新的自定义副本 `300ef9d5233442c6b8293839d310d6d8`；UI 显示来源模板“项目立项 v3.0.0”，原来源信息保留。
- 在该副本的 DAG 编辑区以 UI 操作配置图结构和命名字段绑定，点击保存；随后从“我的任务”列表重新打开同一 Task URL。重新打开后 UI 仍显示 7 个节点、8 条依赖、最大并发 3，以及 9 个输入绑定。原官方 Task/模板和 Run 未编辑。
- UI 的“暂不可运行”按钮为 disabled，页面说明该版本化场景 DAG 尚未通过交付终检等价审查；本阶段没有通过浏览器启动 Run，也没有以 API 调用代替浏览器验收。
- 因此浏览器图编辑/保存/重开为 PASS，浏览器非线性 DAG 执行、浏览器 Run 状态/SSE、失败重跑与 Artifact 为 BLOCKED。工具检查/Preflight 的浏览器交互未完成，不记 PASS。浏览器截图通过 CUA 观察记录，未保存为仓库文件。

## 6. 测试结果及回归基线

### 6.1 E2 定向及 DAG/SOP 控制回归

执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/task_dag_e1_business_gate_test.py `
  tests/task_dag_template_copy_test.py `
  tests/task_dag_scheduler_test.py `
  tests/task_dag_control_test.py `
  tests/task_dag_retry_test.py `
  tests/task_sop_control_test.py `
  tests/task_sop_template_retry_test.py `
  tests/task_dag_e2_non_linear_s01_test.py
```

结果：**49 passed，9 subtests passed**。E2 文件的 5 个用例均通过。中间曾有一个 E1 测试因拒绝消息缺少其断言要求的“并发度为 1”子串而失败；只修正 Task 模块提示文案后同一专项集全部通过，业务拒绝行为未放宽。

### 6.2 完整 Task 套件与 E1 基线

完整套件命令对 `tests/task_*.py` 收集文件运行 pytest，并输出 JUnit XML 到 `%TEMP%\task-dag-e2-final-20261023.xml`。阶段开始基线 XML 为 `%TEMP%\task-dag-e1-final-20261023.xml`。

实际命令（PowerShell）：

```powershell
$testFiles = Get-ChildItem -LiteralPath tests -Filter 'task_*.py' -File | ForEach-Object { $_.FullName }
.\.venv\Scripts\python.exe -m pytest -q $testFiles --junitxml="$env:TEMP\task-dag-e2-final-20261023.xml"
```

| 指标 | E1 基线 | E2 结束 |
|---|---:|---:|
| 测试用例数 | 510 | 515（增加本阶段 5 个 E2 用例） |
| Passed | 489 | 494 |
| Failed | 3 | 3 |
| Skipped | 18 | 18 |
| Subtests passed | 30 | 30 |

逐项比较 JUnit nodeid、异常类别、主断言和稳定诊断码：失败集合完全相同，没有新增失败或消失的历史失败。

| 既有失败 nodeid | 基线与当前共同签名 |
|---|---|
| `tests/task_p7r1_output_contract_test.py::TaskP7R1ServiceOutputContractTest::test_sop_finalizer_builds_citation_index_then_runs_full_validation` | `AssertionError: 'failed' != 'succeeded'`；`TASK_EVIDENCE_LEDGER_FAILED`、`incomplete_claim_block`、`content_after_citation_block` |
| `tests/task_p7r1_output_contract_test.py::TaskP7R1ServiceOutputContractTest::test_sop_rejected_repair_keeps_body_and_fails_closed` | `AssertionError: 'task_protocol_repair' not found`；`TASK_EVIDENCE_LEDGER_FAILED`、`citation_block_missing`、`incomplete_claim_block` |
| `tests/task_s02_content_repair_test.py::TaskS02ContentRepairSOPTest::test_s02_v2_repairs_only_the_mixed_occurrence_then_runs_full_ledger` | `AssertionError: SOPPhase.FAILED != SOPPhase.COMPLETED`；`incomplete_claim_block`、`content_after_citation_block` |

这些均为 R9-A 保留的 SOP Claim–Evidence/历史协议问题，本阶段没有修改相应业务合同、测试断言或官方模板，也没有把失败归类成 E2 新回归。18 项跳过包含真实 Redis/PostgreSQL 环境门禁，详见下一节。

## 7. 真实 Redis/PostgreSQL 与生产风险

阶段结束时以下环境变量未设置：`LXSCOPE_TASK_TEST_DATABASE_URL`、`LXSCOPE_TASK_TEST_REDIS_URL`、`LXSCOPE_TASK_ISOLATED_TEST`。真实 Redis CAS/并发/租约/恢复和真实 PostgreSQL CAS/租约/原子事件/恢复用例按测试门禁跳过。没有用 Memory、SQLite 或 fakeredis 冒充真实存储通过；TaskService 专项内存用例只用于调度和业务语义。

尚未实测：真实 KB Gateway 并发安全；Redis/PostgreSQL 原子提交、CAS/事件一致性、Lease/Fencing、多进程 claim 和 checkpoint 持久化；浏览器实际服务 Run、SSE、业务 Artifact 文件写入；真实模型或外部工具行为。当前应用服务没有测试 allowlist，因此版本化 S01 DAG 继续 fail closed。

## 8. 验收矩阵与准入判断

| 项目 | 结果 | 依据/阻塞 |
|---|---|---|
| S01 安全并行依赖分析 | PASS（Task 层只读语义） | 两检索基于已保存的同一 InputStep 字段；实际 KB 后端并发仍 BLOCKED |
| S01 非线性 DAG TaskService 执行 | PASS（合成适配器、内存 TaskStore） | 5 个 E2 用例；成功 Run、局部 Retry 和控制状态证据见上文 |
| 节点 Schema/业务终检/Artifact fail-closed | PASS（合成适配器） | verifier 拒绝测试无 Artifact；E1 门禁回归通过 |
| 九场景线性 DAG 回归与 SOP 稳定性 | PASS（测试回归） | 完整套件只有基线 3 项失败；SOP 调度未修改 |
| 浏览器保存/重开 | PASS | 本地合法会话下保存 8 条边和 9 个绑定后重开仍保留 |
| 浏览器实际 Run / SSE / 浏览器 Retry | BLOCKED | 服务端的版本化场景交付门禁拒绝 Run；未绕过 |
| 真实 Redis | BLOCKED | 隔离 Redis URL 未设置，集成测试跳过 |
| 真实 PostgreSQL | BLOCKED | 隔离 PostgreSQL URL 未设置，集成测试跳过 |
| S02～S09 非线性图 | BLOCKED | E2 仅批准并验证 S01 v3.0.0；未扩展授权 |
| 生产启用 | **NO-GO** | 持久化和真实外部集成未完成；应用执行授权仍关闭 |

**是否允许进入后续非线性扩展或生产启用：否。** 先在专用隔离 Redis/PostgreSQL 完成真实存储验收，并为 S01 的实际服务 Run、KB Gateway 并发和浏览器端到端执行建立明确的非生产授权及证据；在此之前保持生产 DAG 关闭，也不自动开放 S02～S09。
