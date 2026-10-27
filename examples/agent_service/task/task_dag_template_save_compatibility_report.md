# Task DAG 模板另存兼容性报告

日期：2026-10-23  
分支：`integrate/dev_l`  
阶段开始提交：`e63c3b80278dba2db5ddfd8e195b988481cabd1e`

## 结论

- **S01～S09 另存为 DAG、默认依赖转换、Task API 保存与重新读取：PASS（TaskService/API 隔离测试）。** 九个当前模板均保留来源 ID、版本、输入和模板步骤绑定，原节点配置不变，副本节点 ID 独立重映射。
- **DAG 图编辑授权：PASS（服务端 API 测试及前端构建）。** 编辑权根据可验证的用户副本来源判断；不会通过删除模板来源字段取得权限。官方模板实例仍然不能保存 DAG。
- **S01～S09 模板来源 DAG 执行：BLOCKED。** 当前 `create_run` 有版本化业务模板的安全拒绝，Preflight 现在返回明确阻断。没有为通过本阶段验收而移除此门禁。
- **SOP 兼容：PASS（定向 SOP/API 回归）；完整 Task 套件未全绿。** 完整套件有 3 项既有 Claim–Evidence/S02 修复测试失败，详见“测试结果”。
- **浏览器实测：未执行。** 已完成 Task 页面实现、Scoped ESLint 和前端构建；本报告中的九场景保存/刷新证据来自真实 Task Router、TaskService 和隔离 TaskStore 的 API 测试，不冒称浏览器验收。
- **生产准入：NO-GO。** 版本化场景 DAG Run 仍被服务端阻断；未连接生产数据库、模型、MCP 或业务工具，也未开启生产 DAG。

## 实现与接口契约

### 另存接口

`POST /tasks/{task_id}/custom-flow-copy` 接受可选请求体：

```json
{"mode":"sop"}
```

或：

```json
{"mode":"dag"}
```

未传请求体时仍按 `sop` 处理，兼容已有 API 调用。前端的“另存为”对话框分别提供“保持 SOP”和“转换为 DAG”；DAG 选项只受 `editing_enabled` 控制，执行开关和版本化场景安全门禁仍由服务端分别校验。

### DAG 副本转换规则

- 新建独立 Task ID 和节点 ID；保留 `source_template_id`、`source_template_version`、来源 Task ID/修订、`template_inputs` 及完整 `template_step_bindings`。
- 深复制节点配置、Prompt、InputStep、Tool/Agent 配置、输出契约、Schema、Artifact 与资源需求。
- 新 DAG 使用 Schema v1；默认 `max_concurrency=1`，按模板 SOP 顺序生成相邻节点的直接依赖边。
- `input_bindings` 初始为空。SOP 原有的“上一节点输出”通过 DAG 直接前驱输出映射承接；测试使用 remapped node ID 调用 `build_inputs_by_node_id`，确认每个节点读到新的前驱输出引用。
- 资源容量按同名节点需求的最大值生成；执行仍按声明容量计算。非法容量、悬空绑定、循环边由现有 Graph Validator 拒绝。
- 模板绑定节点之间必须保留原顺序的直接数据依赖。用户可新增自定义节点并编辑分支/汇合，但不能在断开模板步骤数据交接后保存。
- 官方模板 Task 保持模板实例模式和只读；只有来源版本、来源 Task 修订均可验证的 `custom_flow` 用户副本才能编辑图。来源字段不会被删除或伪造为“普通自由 Task”。

## S01～S09 矩阵

所有行均通过当前 `TemplateRegistry` 的真实版本实例化，经 Task Router 调用另存接口创建副本。逐项验证节点 ID 重映射、配置/来源保留、线性图保存及重新读取、改边可保存、循环/输入绑定/资源错误被拒绝、Preflight 显示执行阻断。节点键列列出官方模板顺序。

| 场景 | 当前模板版本 / 节点数 | 官方 SOP 节点顺序 | DAG 编辑及持久化 | DAG 执行 |
|---|---:|---|---|---|
| S01 项目立项 | 3.0.0 / 7 | `collect_project_input` → `extract_project_profile` → `search_similar_projects` → `check_project_rules` → `draft_initiation_recommendation` → `review_project_facts` → `produce_initiation_card` | PASS（API） | BLOCKED：版本化场景 DAG 安全门禁 |
| S02 项目策划 | 5.1.0 / 5 | `collect_planning_inputs` → `retrieve_planning_template` → `draft_eight_chapter_report` → `review_planning_outline` → `apply_review_and_create_docx` | PASS（API） | BLOCKED：保留 `draft_unverified` / 核验报告门禁，DAG 收尾未证明等价 |
| S03 专家匹配 | 3.0.0 / 8 | `collect_matching_requirements` → `extract_project_profile` → `lookup_expert_candidates` → `check_expert_conflicts` → `rank_expert_candidates` → `explain_matching_reasons` → `collect_human_review` → `produce_expert_shortlist` | PASS（API） | BLOCKED：版本化场景 DAG 安全门禁 |
| S04 会议筹备 | 3.0.0 / 7 | `collect_meeting_parameters` → `calculate_meeting_costs` → `generate_meeting_materials` → `check_meeting_materials` → `collect_material_corrections` → `prepare_notification` → `assemble_meeting_preparation_pack` | PASS（API） | BLOCKED：版本化场景 DAG 安全门禁 |
| S05 会议纪要 | 3.0.0 / 6 | `collect_transcript_text` → `import_meeting_transcript` → `segment_meeting_topics` → `extract_meeting_actions` → `review_minutes_draft` → `produce_meeting_minutes` | PASS（API） | BLOCKED：版本化场景 DAG 安全门禁 |
| S06 意见处理 | 3.0.0 / 6 | `collect_recognized_opinions` → `recognize_opinion_text` → `classify_opinion_points` → `summarize_opinion_dimensions` → `review_opinion_summary` → `export_opinion_summary` | PASS（API） | BLOCKED：版本化场景 DAG 安全门禁 |
| S07 报告生成 | 5.0.0 / 5 | `collect_report_materials` → `retrieve_report_evidence` → `draft_report_chapters` → `collect_report_review` → `produce_report_draft` | PASS（API） | BLOCKED：版本化场景 DAG 安全门禁 |
| S08 档案移交 | 3.0.0 / 6 | `collect_archive_files` → `extract_archive_metadata` → `classify_archive_materials` → `check_archive_completeness` → `prepare_transfer_manifest` → `produce_transfer_manifest` | PASS（API） | BLOCKED：版本化场景 DAG 安全门禁 |
| S09 报销结算 | 3.0.0 / 6 | `collect_structured_invoice_data` → `recognize_invoice_fields` → `verify_and_deduplicate_invoices` → `check_expense_rules` → `prepare_expense_draft` → `produce_expense_submission_package` | PASS（API） | BLOCKED：版本化场景 DAG 安全门禁 |

“PASS（API）”不等于真实浏览器操作或真实外部系统集成。没有任何一行被表述为模板 DAG Run 通过。

## 业务门禁与 Run 安全

- TaskTemplateValidator 仍按精确模板 ID/版本校验副本；来源步骤绑定、必需 Tool 能力、工具权限/副作用 Preflight 和节点输出契约没有在复制时清除。
- 对来源模板 DAG 加入直接依赖约束，避免编辑图时切断相邻来源步骤原有的数据交接。官方 Task 仍不接受 DAG 图更新。
- 当前 Task DAG Run 收尾只具备通用 DAG 收尾路径，尚未证明对 S01～S09 分别执行 SOP 的场景终检、验证审计、产物状态和业务特定交付。尤其 S02 v5.1 必须保持 `draft_unverified`，不能因 DAG Run 成功而误报为正式核验。
- 因此，版本化场景图可以创建、编辑、校验和持久化；Preflight 对其返回 `graph.scenario_execution`，DAG 执行关闭时还返回 `graph.execution_enabled`。即使 DAG 总开关开启，`create_run` 仍拒绝带模板来源的 DAG Run。
- 通用自由 Task 的 DAG 功能没有因本次改动改变。DAG/SOP Pause、Continue、Step、Stop、Retry、CAS、租约与 Run 快照逻辑均未重构。

## SOP、模板和历史 Run 兼容性

- 旧客户端不传另存请求体仍创建 SOP 副本；新客户端 `mode=sop` 同样保持 `graph=null`。
- 官方模板编辑入口仍保持锁定；测试确认官方实例不能提交图定义。
- 九个官方模板的版本文件没有纳入本阶段修改。测试对每个源 Task 检查节点和 `graph=null` 未变化。
- 每个场景测试在隔离 TaskStore 中保存一个进行中的 SOP Run 快照，调用 Task API 创建 DAG 副本后逐字段比较 Run 快照，未发生变化。
- 自定义副本的执行历史仍按现有存储分离；复制不会把源 Task 的 Run 复制成副本的新 Run，也不会直接改写旧 Run。

## 测试结果

### 通过

- 定向 Task 回归：`tests/task_dag_template_copy_test.py`、`task_custom_flow_test.py`、`task_dag_editing_test.py`、`task_dag_scheduler_test.py`、`task_dag_retry_test.py`、`task_sop_control_test.py`：**36 passed**。
- Scoped ESLint：Task `index.tsx`、`task-dag-canvas.tsx`、`task-header.tsx`、`src/api/task.ts`：**通过**。
- `pnpm run build`：**通过**（TypeScript 与 Vite）。Vite 报现有应用包有超过 500 kB 的 chunk 警告；不影响构建结果。
- Task Python 修改文件 `compileall`：**通过**。

### 完整 Task 套件

执行命令：

```powershell
$taskTestFiles = Get-ChildItem tests -File -Filter 'task_*.py' | ForEach-Object { $_.FullName }
& .\.venv\Scripts\python.exe -m pytest $taskTestFiles -q
```

结果：**476 passed、3 failed、18 skipped、30 subtests passed**。3 项失败均落在既有 SOP Claim–Evidence 修复测试，本次新增条件只作用于 `task.graph is not None`，这些失败测试使用 SOP（`graph=None`），因此没有经过本次新增的模板 DAG 路径；它们与已有 R9-A 报告记载的 3 条未闭环 Claim–Evidence 修复问题一致。本报告不把完整套件标为全绿，也不在本阶段修改这些 SOP 证据修复逻辑。

失败项：

1. `TaskP7R1ServiceOutputContractTest.test_sop_finalizer_builds_citation_index_then_runs_full_validation`：最终 Run 为 `failed`；校验记录 `content_after_citation_block` 与不完整 `task-claim-evidence` 区块。
2. `TaskP7R1ServiceOutputContractTest.test_sop_rejected_repair_keeps_body_and_fails_closed`：预期的 `task_protocol_repair` 记录缺失；最终证据/引用区块仍不完整并 fail closed。
3. `TaskS02ContentRepairSOPTest.test_s02_v2_repairs_only_the_mixed_occurrence_then_runs_full_ledger`：最终节点仍失败，诊断为引用区块之后存在内容及 Claim–Evidence 区块不完整。

18 项跳过测试未作为真实数据库验收结果；本阶段没有连接 Redis/PostgreSQL，也没有连接模型、MCP 或外部业务工具。所有新增 Task API 测试使用隔离的 TestClient、TaskService 和 TaskStore。

## 本阶段修改文件

本阶段修改或新增的文件均在白名单内：

- `examples/agent_service/task/_models.py`：增加 SOP/DAG 另存请求模式。
- `examples/agent_service/task/_router.py`：兼容旧空请求体并接入模式参数。
- `examples/agent_service/task/_service.py`：按模式创建副本、保持溯源、生成线性图；仅允许有完整来源信息的自定义副本编辑 DAG；校验来源步骤直接交接；增加 Preflight 执行阻断说明。
- `examples/web_ui/frontend/src/api/task.ts`：另存模式 API 类型。
- `examples/web_ui/frontend/src/pages/task/index.tsx`：SOP/DAG 另存选择、模板来源副本编辑权限、执行阻断提示、按线性顺序初始化旧 SOP 副本图。
- `examples/web_ui/frontend/src/pages/task/task-dag-canvas.tsx`：复用图编辑器，增加节点顺序拖拽、清晰的执行阻断提示。
- `examples/web_ui/frontend/src/pages/task/task-header.tsx`：执行门禁统一显示“暂不可运行”，不把场景交付阻断误标为 DAG 开关关闭。
- `tests/task_dag_template_copy_test.py`：九模板另存、图映射、保存/重读、错误图拒绝、Run 快照不变和执行门禁测试。
- `examples/agent_service/task/task_dag_template_save_compatibility_report.md`：本报告。

### Git 快照核对

- 阶段开始已保存 `git status --short`、`git diff --name-only`、分支和 HEAD 快照，目录为 `%TEMP%\task-dag-template-save-start-20261023-150632`。
- 开始时工作区已有大量 Task、前端、Admin、Sales Console 和其他文件改动。本阶段没有恢复、清理或覆盖它们。
- 阶段开始和结束的 `git diff --name-only` 路径集合一致；被本阶段增量编辑的跟踪文件在阶段开始时已经是 dirty 状态。
- 与阶段开始相比，新增的 `git status --short` 路径仅为本报告和 `tests/task_dag_template_copy_test.py`；两者均在白名单。未发现本阶段新增白名单外路径。
- 未提交代码、未修改全局依赖/配置/数据库 Migration、官方模板或 AgentScope 主框架。

## 遗留项与准入判断

1. **BLOCKED：S01～S09 DAG 执行。** 必须先逐场景补齐并验证与 SOP 等价的业务终检、引用/证据审计、Artifact 写入和终态报告，再由后续明确阶段决定是否开放；本阶段不触碰共享 Run 执行主逻辑。
2. **BLOCKED：真实浏览器九场景操作。** 当前只完成页面构建和 API 持久化检查，没有声称真实浏览器刷新验收。
3. **遗留 FAIL：3 项 SOP Claim–Evidence 修复测试。** 属于 R9-A 已知的剩余问题；SOP 逻辑未在本阶段修改。
4. **风险：保存的 DAG 图可编辑不代表可执行。** UI、Preflight 和服务端 Run 入口均保持阻断，防止将图编辑误认为业务 Run 已通过验收。

**是否允许进入模板 DAG 的真实 Run 验收：否。** 可继续进行受控 DAG 最终门禁兼容性工作；在门禁等价测试通过前，不能执行这些版本化模板 DAG，也不能宣称生产就绪。
