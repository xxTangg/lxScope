# 龙信Scope Task R9-A 业务门禁缺陷治理验收报告

日期：2026-10-23  
分支：integrate/dev_l  
阶段基线提交：e63c3b80278dba2db5ddfd8e195b988481cabd1e  
结论：部分完成；生产准入 NO-GO。

## 1. 执行结论

本阶段针对 R7 报告中的 43 项 Task 测试失败逐项核对，并优先修复了结构化输出、Tool/Agent 输出审计、版本化输出契约、S02/S07 来源证据门禁、产物阻断及历史模板契约解析问题。

最终完整 Task 套件结果为 **473 passed、3 failed、18 skipped、30 subtests passed**。对照阶段开始时 43 项失败的测试名称：

- 40 项现通过。
- 3 项仍失败，且没有新增失败 nodeid。
- 3 项剩余失败均由不完整的 Claim–Evidence / 引用协议触发；当前服务端 fail closed，Run 保持 failed 且不生成产物。尚未完成这些旧版协议输出的自动修复流程，故列为 BLOCKED，不能宣称该能力完成。
- 当前版本 S02 v5.1 的 draft_unverified 策略保持不变。
- R9-A 仅完成 Task 隔离测试和本地测试替身验证。本阶段没有执行真实模型、MCP、Redis、PostgreSQL 或生产业务集成验证。

本阶段完成了高风险门禁的最小修复，但因协议修复路径仍未闭环、真实存储测试跳过，生产准入为 **NO-GO**。

## 2. 阶段开始快照与修改边界

阶段开始前记录了 Git 快照目录：

C:\Users\15452\AppData\Local\Temp\r9a-start-20261023-113236

快照记录分支、HEAD、git status --short、git diff --name-only 和 porcelain 状态。阶段开始时工作区已有多项前序 Task、Task 前端、Sales Console、部署文件及测试产物修改。本阶段没有清理、覆盖或重置这些已有改动。

路径级对照结果：

| 检查 | 阶段开始 | 报告生成前 | 差异 |
|---|---:|---:|---:|
| git diff --name-only | 40 条 | 52 条 | 新增 12 条 tracked 路径 |
| git status --short | 92 条 | 105 条 | 新增 13 条状态路径 |
| 新增路径是否越界 | — | — | 13 条全部在 Task 后端或 tests/task_*.py 白名单内 |

新增的 13 条阶段状态路径是：

- examples/agent_service/task/_execution_gates.py
- examples/agent_service/task/_evidence_contract.py
- examples/agent_service/task/_template_validation.py
- tests/task_input_protocol_test.py
- tests/task_p7b_scenario_runtime_test.py
- tests/task_p7r1_output_contract_test.py
- tests/task_project_planning_rag_test.py
- tests/task_s02_content_repair_test.py
- tests/task_s02_lightweight_template_test.py
- tests/task_s02_v4_delivery_test.py
- tests/task_s07_rag_integration_test.py
- tests/task_template_output_test.py
- tests/task_template_test.py

报告文件本身也在 Task 白名单内。阶段开始时已存在的白名单外状态路径在结束快照中仍存在；本阶段没有新增白名单外路径，也没有修改主框架、main.py、九大官方模板、全局依赖、配置或 Migration。

由于部分 Task 源码和测试文件在阶段开始前已经处于 modified 状态，当前完整 git diff 是此前阶段与 R9-A 的聚合差异，不能把这些文件的整个 diff 都归因于 R9-A。上面的路径差分快照用于识别本阶段新增路径；下面列出的“本阶段触及文件”说明本阶段实际调整过的 Task 文件。

## 3. 缺陷治理与源码证据

### 3.1 输出 Schema 与节点状态、审计统一

新增 Task 层执行门禁适配器 examples/agent_service/task/_execution_gates.py。它在节点被接受为成功前校验冻结的 JSON 输出 Schema 和能力输出 Schema，并写入 output_schema_validation 与 output_contract_validation 记录。

SOP 路径通过 TaskService 的 execute_with_output_gates 调用统一门禁，源码位置为 examples/agent_service/task/_service.py 中 _build_sop_engine 的节点执行回调。输出契约校验不通过时，不能把 Run 或节点投影为成功，也不能进入产物写入流程。

结构化 Agent 输出可以按原有有限重试策略重新生成；Tool/Python 输出不合格时不自动回放可能具有副作用的节点。失败的节点校验由 TaskSOPStep 保存为 task-output-contract 审计记录。相关逻辑位于 examples/agent_service/task/_sop.py。

### 3.2 冻结模板版本和旧 Run 契约 fail closed

examples/agent_service/task/_template_validation.py 按 Run 保存的模板 ID 和精确版本解析历史模板契约；examples/agent_service/task/_service.py 在恢复或运行 SOP 前检查冻结快照中的输出契约。无法从对应历史版本确认契约或发现契约缺失/不一致时，不以当前模板覆盖旧 Run 的契约，而是拒绝执行。

该策略避免旧 Run 因模板 Registry 的当前版本变化而获得较宽松的新契约，也避免按当前业务版本错误解释旧 Run 输出。

### 3.3 S02/S07 来源、引用和业务字段门禁

TaskService 的版本化输出门禁对 S02 v3/v4 系列结构化报告校验冻结 JSON Schema、必填字段、引用和本次 Run 的授权证据快照。校验失败时 Run 失败，DOCX 不生成。S07 的 Claim–Evidence 与引用核验使用当前 Run 的授权来源快照。

Claim–Evidence 共用协议提示在 examples/agent_service/task/_evidence_contract.py 中明确要求来源台账由服务端从已验证协议块和授权快照生成；协议缺失、截断、引用后还有正文、绑定不唯一或不能安全物化时，服务端拒绝验收，不根据模型文本自行猜测来源映射。

### 3.4 产物门禁与 S02 v5.1 策略

DOCX/XLSX 等最终产物写入仍位于 Run 成功及场景交付门禁之后。S02 v5.1 的轻量草稿仍明确为 draft_unverified，逐条 Claim–Evidence 台账状态保持 not_performed，语义支持仍需人工复核。报告不会因为检索成功或结构符合而标为“已核验”；这一冻结业务语义未更改。

### 3.5 测试夹具与历史版本断言

对缺少 Task Executor verifier 的 Task 专属测试替身补上 verify_node，使测试走到真实 TaskService 门禁，而不是在 fixture 接口错误处提前失败。历史模板相关测试改为读取其指定的冻结版本；S04 期望状态更新为 ready_for_ai_review，避免将尚待 AI/人工审核的材料断言成 passed。没有通过删断言、跳过测试或降低来源校验来消除失败。

## 4. 43 项基线逐项对照

基线和阶段结果来源：

- R7 基线完整日志：%TEMP%\lxscope-task-r7-full-20261023.txt（433 passed、43 failed、18 skipped、30 subtests passed）。
- R7 逐项分类：examples/agent_service/task/task_unified_execution_r7_nine_scenarios_acceptance_report.md，第 7 节。
- R9-A 完整日志：%TEMP%\r9a-task-full-final-20261023.txt。
- R9-A JUnit：%TEMP%\r9a-task-full-final-20261023.xml。

表中 PASS 表示该基线 nodeid 在 R9-A 完整 Task 套件中现已通过；BLOCKED 表示测试仍失败，虽然当前数据被 fail closed，但预期的协议修复/成功路径尚未实现或无法验证。

| # | 基线测试 | R7 分类 | R9-A |
|---:|---|---|---|
| 1 | task_capability_contract_test.py — test_tool_schema_failure_is_persisted_and_stops_run | 实现缺口：Tool Schema 审计及 Run 状态 | PASS |
| 2 | task_input_protocol_test.py — test_api_rejects_stale_answers_and_streams_waiting_then_completion | fixture：缺少 verify_node | PASS |
| 3 | task_input_protocol_test.py — test_redis_round_trip_resumes_after_rebuilding_service | fixture：缺少 verify_node | PASS |
| 4 | task_mvp_pipeline_test.py — test_agent_tool_python_agent_pipeline_without_mcp | fixture：缺少 verifier | PASS |
| 5 | task_p7b_scenario_runtime_test.py — test_s04_decimal_costs_and_materials_are_deterministic_drafts | 预期不匹配：审核中状态 | PASS |
| 6 | task_p7r1_output_contract_test.py — test_previous_result_is_typed_only_when_validated_value_exists | 历史下游类型约束 | PASS |
| 7 | task_p7r1_output_contract_test.py — test_s02_final_prompt_avoids_duplicate_report_and_keeps_both_protocols | 历史 S02 协议提示 | PASS |
| 8 | task_p7r1_output_contract_test.py — test_s07_final_uses_the_shared_strict_claim_schema | 历史 S07 Schema 提示 | PASS |
| 9 | task_p7r1_output_contract_test.py — test_invalid_output_is_bounded_and_server_status_is_authoritative | 实现缺口：结构错误及权威状态 | PASS |
| 10 | task_p7r1_output_contract_test.py — test_sop_finalizer_builds_citation_index_then_runs_full_validation | 实现缺口：引用索引/Claim–Evidence | BLOCKED |
| 11 | task_p7r1_output_contract_test.py — test_sop_rejected_repair_keeps_body_and_fails_closed | fixture 后暴露协议修复路径缺口 | BLOCKED |
| 12 | task_p7r1_output_contract_test.py — test_validated_result_is_persisted_and_bound_to_next_node | 实现缺口：验证值绑定 | PASS |
| 13 | task_project_planning_rag_test.py — test_failed_final_ledger_does_not_create_docx | 历史 S02 台账失败曾未阻断 | PASS |
| 14 | task_project_planning_rag_test.py — test_incomplete_new_protocol_fails_closed_through_task_service_and_sop | 历史引用协议未 fail closed | PASS |
| 15 | task_project_planning_rag_test.py — test_input_selected_project_material_is_saved_in_run_and_ledger | 历史授权材料台账 | PASS |
| 16 | task_project_planning_rag_test.py — test_new_occurrence_protocol_runs_through_task_service_and_sop | 历史 occurrence 台账 | PASS |
| 17 | task_project_planning_rag_test.py — test_s02_custom_copy_runs_rag_review_and_word_writer | 历史引用校验记录 | PASS |
| 18 | task_s02_content_repair_test.py — test_s02_v2_repairs_only_the_mixed_occurrence_then_runs_full_ledger | fixture 后暴露 S02 v2 局部内容修复缺口 | BLOCKED |
| 19 | task_s02_lightweight_template_test.py — test_s02_v5_taskservice_sop_generates_run_linked_draft_docx | 实现缺口：轻量草稿交付状态 | PASS |
| 20 | task_s02_v3_structured_output_test.py — test_s02_v3_taskservice_sop_verifier_and_artifact_gate | 历史 S02 v3 Schema 审计 | PASS |
| 21 | task_s02_v3_structured_output_test.py — test_real_run_missing_empty_arrays_passes_full_run_and_writes_docx | 历史 S02 v3 合法空数组 | PASS |
| 22 | task_s02_v3_structured_output_test.py — test_s02_v3_required_business_field_still_fails_and_blocks_docx | 历史必填业务字段 | PASS |
| 23 | task_s02_v3_structured_output_test.py — test_real_run_kind_conflict_fails_service_run_and_blocks_docx | 历史类型冲突 | PASS |
| 24 | task_s02_v3_structured_output_test.py — test_s02_v3_invalid_inner_json_stops_before_artifact_writer | 历史内部 JSON | PASS |
| 25 | task_s02_v3_structured_output_test.py — test_s02_v3_rejects_redacted_legacy_run_protocol_before_artifact_writer | 历史脱敏协议 | PASS |
| 26 | task_s02_v3_structured_output_test.py — test_s02_v3_policy_failure_does_not_retry_or_create_artifact | 历史政策门禁 | PASS |
| 27 | task_s02_v4_delivery_test.py — test_s02_v4_and_v41_historical_templates_keep_versioned_contracts | fixture/版本：v4/v4.1 冻结契约 | PASS |
| 28 | task_s02_v4_delivery_test.py — test_s02_v4_taskservice_sop_demo_draft_writes_marked_docx_after_ledger | 历史证据台账与草稿标识 | PASS |
| 29 | task_s02_v4_delivery_test.py — test_s02_v41_taskservice_sop_demo_writes_marked_docx_after_safe_downgrade | 历史安全降级交付 | PASS |
| 30 | task_s02_v4_delivery_test.py — test_s02_v41_adapter_pass_does_not_override_ledger_failure_or_write_docx | fixture：旧函数入口失效 | PASS |
| 31 | task_s02_v4_delivery_test.py — test_s02_v41_strict_association_failure_blocks_taskrun_and_docx | 历史关联校验 | PASS |
| 32 | task_s02_v4_delivery_test.py — test_s02_v4_strict_missing_source_blocks_success_status_and_docx | 历史来源缺失 | PASS |
| 33 | task_s07_rag_integration_test.py — test_invalid_quote_is_rejected_before_model_verifier_or_artifact | 历史无效引用阻断 | PASS |
| 34 | task_s07_rag_integration_test.py — test_valid_citation_reaches_deterministic_citation_tool | 历史引用校验审计 | PASS |
| 35 | task_s07_rag_integration_test.py — test_preflight_keeps_inaccessible_knowledge_base_as_permission_denial | fixture：测试读取错误版本 | PASS |
| 36 | task_s07_rag_integration_test.py — test_preflight_reports_document_not_found_as_data_error_not_permission | fixture：测试读取错误版本 | PASS |
| 37 | task_s07_rag_integration_test.py — test_preflight_reports_unindexed_document_separately | fixture：测试读取错误版本 | PASS |
| 38 | task_s07_rag_integration_test.py — test_report_rag_is_task_native_and_does_not_discover_session_tool | fixture：测试读取错误版本 | PASS |
| 39 | task_template_output_test.py — test_structured_schema_failure_is_persisted_and_fails_run | 实现缺口：Schema 不合格仍成功 | PASS |
| 40 | task_template_test.py — test_registry_returns_locatable_missing_and_invalid_file_errors | fixture：旧 S01 v2 路径 | PASS |
| 41 | task_template_test.py — test_template_api_reports_invalid_catalog_without_exposing_paths | fixture：旧 S01 v2 路径 | PASS |
| 42 | task_template_test.py — test_registry_rejects_invalid_template_schema_and_extra_files | fixture：旧 S01 v2 路径 | PASS |
| 43 | task_template_version_test.py — test_s07_v5_uses_ai_acceptance_and_keeps_v4_history | 历史 S07 v4 冻结契约 | PASS |

### 4.1 三项剩余失败的签名变化和影响

1. task_sop_finalizer_builds_citation_index_then_runs_full_validation：基线时失败表征是 citation index 未物化。当前测试输入包含 task-citations 区块后尾随正文，并带有截断的 task-claim-evidence 区块；TaskService 返回 TASK_EVIDENCE_LEDGER_FAILED，Run 为 failed，artifact_generated=false。该测试期望的“一次协议包络修复后继续验收”仍未闭环。
2. task_sop_rejected_repair_keeps_body_and_fails_closed：基线时测试替身缺少 verifier，未进入预期的协议修复路径。fixture 补齐后，当前输入仍为缺少 citation 区块且 Claim–Evidence JSON 截断。服务端拒绝并不产生 task_protocol_repair 元数据；Run failed 且没有产物。拒绝行为安全，但修复审计/受限修复流程仍 BLOCKED。
3. test_s02_v2_repairs_only_the_mixed_occurrence_then_runs_full_ledger：基线时同样在 fixture 的 verifier 接口处提前失败。现在真实 SOP 执行到来源门禁后，输入包含引用区块后的截断 Claim–Evidence JSON，得到 ledger/protocol 失败，未执行预期的局部 occurrence 修订与再次核验；没有产物。S02 v2 的内容修复闭环仍 BLOCKED。

这三项当前失败没有出现 Run 错误地 succeeded 或错误交付产物的证据。它们不能被标为 PASS，因为现有自动协议修复功能和测试期望没有通过。其异常原因与 R7 记录的 fixture/早期路径失败不同，故“nodeid 未新增”不等于“失败签名完全相同”。

## 5. 实际测试及结果

### 5.1 定向业务与控制测试

执行了以下业务门禁、九场景合成、SOP/DAG 控制及重跑测试集合：

    .venv\Scripts\python.exe -m pytest tests/task_project_planning_rag_test.py tests/task_template_output_test.py tests/task_capability_contract_test.py tests/task_s02_v3_structured_output_test.py tests/task_s02_v4_delivery_test.py tests/task_s07_rag_integration_test.py tests/task_s02_lightweight_template_test.py tests/task_template_test.py tests/task_template_version_test.py tests/task_input_protocol_test.py tests/task_mvp_pipeline_test.py tests/task_p7b_scenario_runtime_test.py tests/task_p7r1_output_contract_test.py tests/task_s02_content_repair_test.py tests/task_r7_nine_scenarios_integration_test.py tests/task_sop_control_test.py tests/task_sop_template_retry_test.py tests/task_dag_control_test.py tests/task_dag_retry_test.py -q --tb=short

结果：**289 passed、9 subtests passed，101.18 秒**。日志：%TEMP%\r9a-final-focused-control-20261023.txt。

这项定向集合包含九场景合成 TaskService 链路及 SOP/DAG 控制回归；它不是浏览器 E2E，也不代表真实模型、知识库、MCP 或业务工具集成。

### 5.2 完整 Task 套件

阶段末使用仓库中的全部 tests/task_*.py 文件执行：

    $taskTests = Get-ChildItem tests -File -Filter 'task_*.py' | ForEach-Object { $_.FullName }
    .venv\Scripts\python.exe -m pytest @taskTests -q --tb=short --junitxml="$env:TEMP\r9a-task-full-final-20261023.xml"

结果：**473 passed、3 failed、18 skipped、1 warning、30 subtests passed，120.48 秒**。完整日志见 %TEMP%\r9a-task-full-final-20261023.txt。

18 项跳过全部是显式要求隔离数据库 URL 的真实后端测试，包括：

- Redis 故障恢复、WATCH/CAS、多进程竞争、lease/fencing 和事件一致性：需要 LXSCOPE_TASK_TEST_REDIS_URL。
- PostgreSQL CAS、兼容升级和进程恢复：需要隔离 PostgreSQL URL。
- SOP R6 Redis/PostgreSQL 持久化及多进程故障恢复：需要 LXSCOPE_TASK_TEST_REDIS_URL 或 LXSCOPE_TASK_TEST_DATABASE_URL。

本阶段没有提供或连接这些服务，不能把跳过项视为通过；也没有用 Memory、SQLite 或 fakeredis 替代真实后端验收。1 项 warning 为 Starlette 的 HTTP_422_UNPROCESSABLE_ENTITY 弃用提示，不属于新增失败。

### 5.3 43 项差异判定

依据完整日志中的 nodeid 集合：

- R7 基线失败 nodeid：43。
- R9-A 当前失败 nodeid：3。
- 基线仍失败：3。
- 基线已通过：40。
- 新增失败 nodeid：0。

对 3 个残留 nodeid 检查了当前失败断言、异常/Run 状态和关键运行元数据。它们都现在到达 Task 服务端输出门禁并因输入协议不完整 fail closed；这与旧报告的 fixture 早期异常或旧引用索引表征不同。不得仅凭失败数量判断无回归；本报告逐项列出测试及当前状态。

## 6. 本阶段触及文件与 Git 差异说明

本阶段直接触及的 Task 后端源码：

- examples/agent_service/task/_execution_gates.py
- examples/agent_service/task/_service.py
- examples/agent_service/task/_template_validation.py
- examples/agent_service/task/_sop.py
- examples/agent_service/task/_agentscope_executor.py
- examples/agent_service/task/_evidence_contract.py

本阶段直接触及的 Task 专属测试：

- tests/task_capability_contract_test.py
- tests/task_template_output_test.py
- tests/task_project_planning_rag_test.py
- tests/task_s02_v4_delivery_test.py
- tests/task_s07_rag_integration_test.py
- tests/task_input_protocol_test.py
- tests/task_mvp_pipeline_test.py
- tests/task_p7b_scenario_runtime_test.py
- tests/task_p7r1_output_contract_test.py
- tests/task_s02_content_repair_test.py
- tests/task_s02_lightweight_template_test.py
- tests/task_template_test.py
- tests/task_template_version_test.py

其中部分文件在 R9-A 开始前已经有工作区修改；当前聚合 diff 会包含原有内容。不得将整个聚合 diff 误认为本阶段新写入。阶段开始与结束的 Git 快照均保留在上述临时目录/当前状态中，且未执行 commit。

git diff --check 没有报告空白错误；Git 输出的 LF/CRLF 提示是 autocrlf 行尾提示，不是 diff-check 错误。

## 7. 遗留问题与准入判断

### BLOCKED

- 三项协议修复测试仍失败。当前失败输出安全地被阻断，但 S02 v2 及 Claim–Evidence 旧版输出的受限协议修复/审计路径尚未通过完整 TaskService 测试。需要先明确修复协议与业务正文的边界，再实现并补齐正例、拒绝例和产物门禁验收。
- 18 项真实 Redis/PostgreSQL 与多进程测试在本阶段跳过；R9-A 不能据此重判 P2-H/R6 的既有实测，但本阶段也没有重新验证这些基础设施。
- 外部模型语义验收、真实授权知识库、MCP/业务工具副作用和真实产物交付未在本阶段测试；本地合成通过不等于外部集成通过。

### 生产准入

**NO-GO。** 40 项风险相关基线测试已通过，结构化输出和交付门禁显著收紧；但 3 项 Claim–Evidence 修复路径未闭环，18 项真实持久化测试跳过，且外部业务集成本阶段未验证。S02 v5.1 仍是 draft_unverified，不能作为已核验的正式业务交付。不得据此开启生产 DAG 或声明生产就绪。

R9-A 到此停止，不自动进入下一阶段，不提交代码。

