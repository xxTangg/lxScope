# Task R7：S01～S09 完整执行与节点恢复验收

**日期：** 2026-10-23  
**分支 / HEAD：** `integrate/dev_l` / `e63c3b80278dba2db5ddfd8e195b988481cabd1e`  
**结论：** 九个当前版本模板的隔离 TaskService SOP 调用链、同 Run 节点重跑和断点控制通过；真实外部业务集成未验收，旧版引用及输出契约失败仍在。**生产准入 NO-GO。**

## 1. 边界、阶段和证据

R7 开始前 Git 快照：`%TEMP%\lxscope-task-r7-start-20261023-085744`，保存 HEAD、`git status --short`、`git diff --name-only` 和已有 tracked patch。起点已有 88 条 status 项、39 个 tracked diff 路径，含白名单外既有变更。本阶段没有覆盖它们，也没有编辑官方模板。

| 阶段 | 本阶段产出 | 测试 / 风险 |
|---|---|---|
| R7-A 模板与失败审查 | 读取 `TemplateRegistry.get_current_template`，下表列明九模板当前版本和 56 个节点；复核 R6 完整日志中的 43 项失败。 | 完整失败分类见第 7 节。历史输出/引用门禁风险保留。 |
| R7-B 完整链路 | 每场景由 `TaskTemplateService.instantiate` 建 Task，执行 `TaskService.preflight_task → create_run → InputStep/Task 原生 Tool/Agent → 验收 → artifact`。 | 9/9 当前模板合成隔离链路通过；非真实模型/KB/MCP/Workspace 集成。 |
| R7-C 局部重跑 | 九场景各注入一次 Agent 失败并在原 Run 重跑；另对九场景成功 Tool 做经确认的上游重跑。 | 9/9 同 Run attempt=2、成功前驱保留；Tool 门禁 9/9 拒绝未确认请求，下游清除并再执行。 |
| R7-D 断点与人工门禁 | 九场景分别检查运行中 Pause/Continue、单节点 Step、运行中 Stop、InputStep 重建、损坏指纹/节点身份拒绝恢复和 Task 实例版本漂移。 | R7 专项 93 passed；合法冻结快照按旧版本继续，损坏快照 fail closed。 |
| R7-E 业务数据和产物 | 读取 Task 原生适配结果，并解析真实渲染的 Markdown/DOCX/XLSX 字节。 | 只证明明确标记的合成资料在隔离链路中流转；正式业务系统与外部来源 BLOCKED。 |
| R7-F 回归与白名单 | SOP、DAG 专项及完整 `tests/task_*.py`；与 R6 43 项失败做四维签名比较。 | 新增失败 0；第 8 节列出命令与数字。 |

新测试：`tests/task_r7_nine_scenarios_integration_test.py`。阶段生产代码改动 **0**。合成执行追踪：`%TEMP%\lxscope-task-r7-nine-scenarios-trace-20261023.jsonl`，144 条 JSONL，包含 Run ID、节点 ID/顺序、状态、attempt 历史、Run 版本、事件序号、快照指纹、Agent 调用和产物 SHA-256。追踪只记录合成测试数据。完整 pytest 输出：`%TEMP%\lxscope-task-r7-full-20261023.txt`。

## 2. R7-A：真实模板契约

以下步骤按当前 Registry 顺序排列；`I` 是人工 InputStep，`A` 是 Agent，`T` 是 Task 原生 Tool。线性 SOP 节点从上一步 `output` 取得 `previous_output`，原生 Tool 的结构化 `result` 同时进入后续执行历史。所有当前模板的 `TemplateStep.output_contract` 均为空；S02 v5 另有轻量报告来源/章节与 Task AI 验收规则。不得把历史版本 Claim–Evidence 契约视作当前九模板已通过。

| 模板 / 版本 | 必填实例化输入 | 实际节点顺序（类型；Tool capability） | 人工门禁 / 最终产物 |
|---|---|---|---|
| S01 `s01_project_init` 3.0.0 | `project_name`, `project_brief` | `collect_project_input` I → `extract_project_profile` A → `search_similar_projects` T `project.similar_search` → `check_project_rules` T `project.rule_check` → `draft_initiation_recommendation` A → `review_project_facts` I → `produce_initiation_card` A | 项目资料与事实复核；Markdown 立项卡。 |
| S02 `s02_project_planning` 5.1.0 | `project_name`, `project_brief` | `collect_planning_inputs` I → `retrieve_planning_template` T `planning.template_retrieve` → `draft_eight_chapter_report` A → `review_planning_outline` I → `apply_review_and_create_docx` A | 授权资料、八章草稿、复核意见；DOCX，状态 `draft_unverified`。 |
| S03 `s03_expert_matching` 3.0.0 | `project_name`, `project_brief`, `professional_fields`, `expert_records_json` | `collect_matching_requirements` I → `extract_project_profile` A → `lookup_expert_candidates` T `expert.user_data_lookup` → `check_expert_conflicts` T `expert.user_conflict_check` → `rank_expert_candidates` T `expert.user_rank` → `explain_matching_reasons` A → `collect_human_review` I → `produce_expert_shortlist` A | 候选数据、排除规则和人工复核；Markdown 推荐短名单。 |
| S04 `s04_meeting_preparation` 3.0.0 | `meeting_topic`, `meeting_date`, `participant_count`, `fee_rates_json` | `collect_meeting_parameters` I → `calculate_meeting_costs` T `meeting.cost_calculate` → `generate_meeting_materials` T `meeting.material_generate` → `check_meeting_materials` T `meeting.material_check` → `collect_material_corrections` I → `prepare_notification` A → `assemble_meeting_preparation_pack` A | 会议参数、费用规则和材料修改；DOCX 会议包。 |
| S05 `s05_meeting_minutes` 3.0.0 | `meeting_id`, `transcript_text` | `collect_transcript_text` I → `import_meeting_transcript` T `meeting.transcript_import` → `segment_meeting_topics` A → `extract_meeting_actions` A → `review_minutes_draft` I → `produce_meeting_minutes` A | 用户提交的已转写文本、纪要复核；DOCX 纪要。 |
| S06 `s06_opinion_processing` 3.0.0 | `project_name`, `opinion_records_json`, `classification_rules_json` | `collect_recognized_opinions` I → `recognize_opinion_text` T `opinion.text_import` → `classify_opinion_points` T `opinion.classify` → `summarize_opinion_dimensions` A → `review_opinion_summary` I → `export_opinion_summary` A | 已识别意见和分类规则、人工复核；XLSX 汇总。 |
| S07 `s07_report_generation` 5.0.0 | `project_name`, `source_material_ids`, `required_chapters` | `collect_report_materials` I → `retrieve_report_evidence` T `report.rag_retrieve` → `draft_report_chapters` A → `collect_report_review` I → `produce_report_draft` A | 授权来源、章节要求和人工复核；DOCX 报告草稿。 |
| S08 `s08_archive_transfer` 3.0.0 | `project_id`, `archive_metadata_json`, `archive_category`, `required_metadata_fields_json` | `collect_archive_files` I → `extract_archive_metadata` T `archive.document_metadata` → `classify_archive_materials` A → `check_archive_completeness` T `archive.completeness_check` → `prepare_transfer_manifest` A → `produce_transfer_manifest` A | 文件元数据与完整性清单；XLSX 移交清单。 |
| S09 `s09_expense_settlement` 3.0.0 | `project_id`, `invoice_records_json`, `expense_category`, `expense_rules_json` | `collect_structured_invoice_data` I → `recognize_invoice_fields` T `expense.invoice_import` → `verify_and_deduplicate_invoices` T `expense.invoice_verify_dedupe` → `check_expense_rules` T `expense.rule_check` → `prepare_expense_draft` A → `produce_expense_submission_package` A | 结构化票据、查重和费用规则；XLSX 报销包草稿。 |

测试以仓库 `task/docs/p7b-demo-data/inputs` 的合成输入为起点，按当前模板输入 schema 过滤旧示例字段；S01/S02/S07 的 source ID 换成本测试专用授权合成 ID。外部模型调用由确定性 Agent/AI 验收替身承担，授权知识库与 Workspace 写入由严格限定 owner/source 的合成替身承担。Task Registry、实例化、Preflight、Run 调度、InputStep、Task 原生 Tool 业务适配、运行控制、持久化状态模型、产物渲染均是当前 Task 代码。执行替身断言 **只有 Agent 节点**能调用它；Tool 没有用预填 Run 快照或直接调度器结果冒充。

## 3. R7-B/E：九场景完整结果与业务数据

每个最终 Run 均为 `engine=sop`、`succeeded`，全部节点 `succeeded/attempt 1`，一个产物；所有非 Input 节点写入通过的 `task_ai_verification`，Input 节点写入通过的 `task_input_validation`。测试核对每个 Agent 的 `previous_output` 等于前一真实节点输出，事件由 `run.created` 连续至 `run.succeeded`，最后事件序号等于 Run 记录。

| 场景 | 合成完整执行 Run ID | 业务适配与校验观察 | 渲染产物 |
|---|---|---|---|
| S01 | `55b0155119be4944b62e5023cb043148` | 相似项目仅来自授权 KB；规则候选标记 `candidates_for_review`、`automated_compliance_conclusion=false`；证据 subject 等于 Run owner。 | Markdown 3692 B，立项卡。 |
| S02 | `9e5db43956cb4ee98bae14ecdc17bf7c` | 同次 Run 检索策划模板及项目资料；来源完整性 `passed`；DOCX 正文包含八章标题及合成来源。最终 `draft_unverified`，逐条证据台账仍 `not_performed`、语义支持需人工复核。 | DOCX 38255 B。 |
| S03 | `63d13c2864c042ccac83eefabaf5150f` | Task 原生候选读取/回避/排序：合成候选 2 人合格、`DEMO-EXPERT-003` 排除、`requires_human_review=true`；官方专家库及官方回避校验均为 false。 | Markdown 3850 B。 |
| S04 | `9c2bfcde9a344e10b7d7994350b6e38a` | Decimal 费用合计 `3560.00` 等于各项之和；材料状态 `ready_for_ai_review`、`notification_sent=false`、官方费用政策未核验。 | DOCX 37969 B。 |
| S05 | `3701ec6080d34e9bb31503bcd50b8ea1` | 导入用户提供的 transcript，与输入逐字一致；`transcription_performed=false`；会议 ID 传递到最终输出。 | DOCX 37845 B。 |
| S06 | `e10b51660eea4f0f968f25072542e762` | 原始意见 4 条、唯一 3 条、重复 1 条，`requires_human_review=true`。 | XLSX 6515 B。 |
| S07 | `fb0387ebd8784de1905abb99ad197580` | `report.rag_retrieve` 返回授权来源 ID/subject；DOCX 包含要求的章节和来源文字。这里只证明来源传递，引用语义仍需人工核实。 | DOCX 37047 B。 |
| S08 | `82f6a68d5001407abdbd7319886c1958` | 元数据清单非空、必填检查缺失 0；`file_content_read=false`、`archive_system_write_performed=false`。 | XLSX 6321 B。 |
| S09 | `c1d14785cb104c88bc48de94cc0463ab` | Decimal 票据合计 `2120.00` 与逐票金额一致，合成输入重复数 0；未做 OCR、真实性核验、正式审批或 ERP 提交。另既有 `task_p7b_scenario_runtime_test.py` 的重复票据/金额不一致适配器测试在本轮套件通过。 | XLSX 6297 B。 |

DOCX 通过解析 `word/document.xml` 核对正文，XLSX 通过 `openpyxl` 读取工作表单元格，Markdown 直接解码；测试记录每份产物的字节数及 SHA-256。真实渲染器生成字节，写入的是测试内存 Workspace 替身，因此不等于外部 Workspace 或交付系统验收。没有调用真实模型、生产 MCP、真实 KB、审批、归档、ERP 或生产数据库。

## 4. R7-C：局部重跑、Attempt 和副作用

九个当前模板分别在下列 Agent 注入确定性失败：S01 `extract_project_profile`、S02 `draft_eight_chapter_report`、S03 `explain_matching_reasons`、S04 `prepare_notification`、S05 `segment_meeting_topics`、S06 `summarize_opinion_dimensions`、S07 `draft_report_chapters`、S08 `classify_archive_materials`、S09 `prepare_expense_draft`。失败时此前节点均成功、此后节点均 pending、无最终产物。`retry_node` 后同 Run ID 保留；失败 attempt 1 留在历史，新 attempt 2 成功，前驱输出/结果/attempt 不变，后续节点只执行一次。重复 request_id 幂等，陈旧 `expected_run_version` 拒绝。Step 仅派发被重跑节点并保存 `queued/paused` 检查点，然后重建 TaskService、Continue 完成。

| 场景 | 代表性失败重跑 Run ID | 最终失败节点 attempt | 检查点 / 事件 |
|---|---|---:|---|
| S01 | `d0f2c22554364744ba132d1ce3f5516a` | 2，历史 failed/succeeded | `node.retry_requested`、`run.step_completed`、`run.continued`、`run.succeeded` |
| S02 | `e5444f80c57448399251fc86786d812e` | 2，同上 | 同上 |
| S03 | `2811be3de44f44efa15f6b4bef256b55` | 2，同上 | 同上 |
| S04 | `099f7a3ccdd446a9ac925d55398c6556` | 2，同上 | 同上 |
| S05 | `ac5265b42fd14e249983b819546fa74b` | 2，同上 | 同上 |
| S06 | `1f1bffc5d18f449598a58d2d331d6fc5` | 2，同上 | 同上 |
| S07 | `8e3127e45c7a42c5aabeca4b6437119f` | 2，同上 | 同上 |
| S08 | `9ec92f1fa5354c37a99b3b6b54d93925` | 2，同上 | 同上 |
| S09 | `ce173b37d7d04b66a18d636eee41067a` | 2，同上 | 同上 |

再以真实完整执行至最终 Agent 失败的九场景 Run，选择此前已成功的首个 Tool 重跑。未带 `side_effects_verified` 的请求全部被 `TaskWorkflowConflictError` 拒绝；明确确认后 Tool 及所有后继节点输出/状态被清为 pending，成功前缀原样保留；事件中的 `invalidated_node_ids` 精确等于线性后缀。恢复后 Tool attempt=2、最终失败 Agent attempt=2，产物重新生成。测试替身没有真正外部副作用；此项只证明 **Task 安全门禁生效**，不证明真实 Tool/MCP 副作用已经核对。正常的失败 Run 在最终产物写入前失败，无法自然形成一份“既已交付又仍可 retry 的失败 Run”；测试验证重跑时 `artifacts/final_output` 清空及源码清理逻辑，未伪造该不可能状态来声称真实旧文件已被外部系统撤回。

## 5. R7-D：断点、人工输入和快照

| 操作 | 九场景观察 |
|---|---|
| 运行中 Pause | 首个 Agent 被测试事件阻塞时，`pause_run` 先将 `control_mode` 置为 `pause_requested`；释放该 Agent 后保存成功检查点、Run `queued/paused`、后继 pending，未交付产物；重复 request_id 不增加版本。S01 代表 Run `5b6e0cc6a8c14a40a1782e099c99667a` 暂停时 version/event=17/18。 |
| Continue | 新 TaskService 用相同隔离 store 读取暂停 Run，不自动派发；显式 Continue 后同 Run 成功，暂停前成功节点的 output/attempt 不变，Agent 未重跑。 |
| Step | 九场景失败节点 retry 后执行一次 Step：仅所选节点从 pending 到 succeeded，所有后继仍 pending，状态再次 `queued/paused`；重复 Step request_id 幂等。S01 Step 检查点 version/event=24/25。 |
| 运行中 Stop | 九场景均得到 Run `canceled/stopped`，尚未开始的下游为 `canceled/attempt 0`，无产物，Continue 拒绝，重复 Stop request_id 幂等。 |
| InputStep 重建 | 九场景的首次 `awaiting_input` 问题 ID 在 Service 重建后保持；不会自动跳过，回答后同 Run 继续，Input attempt=1，事件含 `run.awaiting_input` 与 `input.answered`。 |
| 损坏检查点 | 每场景分别篡改测试自有 Run 的节点快照指纹或 SOP 节点身份列表；继续时 Run failed、后继 attempt 仍 0、无产物，原已完成 Agent 未重跑。共 18 个 fail-closed 用例。 |
| 模板版本变化 | 每场景只修改隔离 store 中**已实例化 Task** 的版本元数据，不改 Registry 官方模板；已暂停 Run 的原 `source_template_version`、节点快照和指纹保持，恢复仍用冻结旧快照完成。确定性可证明时版本漂移不应错误拒绝；若冻结快照损坏则上一行拒绝恢复。 |

本阶段 Service 重建发生在同一测试进程；R6 已以隔离 Redis/PostgreSQL 和独立进程验证通用 SOP 存储/租约路径。**R7 没有对九个官方场景分别在真实数据库、真实多进程和真实外部副作用系统重做故障注入**，不能把本节写成该项通过。

## 6. 验收门禁边界与遗留问题

- TaskService Preflight 对九场景均 `ready=true` 才创建 Run；S01/S02/S07 另将合成 KB owner 改为未授权身份，Preflight 变为 `ready=false`，`create_run` 被 `TaskValidationFailure` 拒绝且未留下 Run。
- 九场景的最终 Agent AI 验收被确定性替身拒绝时，Run 全部 failed，失败节点写入 `task_ai_verification.passed=false`，没有产物或最终输出。InputStep 未被跳过。这里证明 Task 门禁调用与负例，不证明真实模型判断质量。
- S02 当前 v5.1 的最终状态明确是 `draft_unverified`，验收报告载明逐条 Claim–Evidence 台账 `not_performed`、`human_review_required=true`。S07 当前 v5 的合成 DOCX 仅核对来源传递/章节存在；不能等同于引用语义或逐条出处已被外部核验。
- S03 的候选来自用户给定结构化数据，官方专家库/回避服务未连接；S04 无官方费用规则，通知未发送；S05/S06 不执行 ASR/OCR；S08 不读取文件也不写归档；S09 不验票据真伪、不审批、不提交 ERP。当前结果是业务草稿链，不是正式业务闭环。
- 真实外部模型、授权知识库、MCP、专家/审批/归档/ERP、真实 Workspace 和真实业务副作用核验环境未提供，本阶段相应集成均 **BLOCKED**。没有用假数据冒充真实外部验证。

## 7. R7-A/F：R6 的 43 项失败逐项分类和差异

R6 基线：`%TEMP%\lxscope-task-r6-full-final-20261022-215938.txt`。R7：`%TEMP%\lxscope-task-r7-full-20261023.txt`。以下按 R6/R7 相同顺序列出唯一的测试文件与测试名；分类是基于当前断言、关键堆栈及源码的**风险分流**，不代表已修复根因。`夹具`=测试夹具或预期不匹配；`实现`=当前可观察的业务校验/状态实现缺口；`历史`=旧模板/协议兼容问题；外部依赖不可用 0，原因待确认 0。历史类中若出现“不合格结果仍 succeeded”，仍是必须处理的门禁风险，不可视为无害测试失败。

| # | 失败测试（文件 `task_*.py` / 测试名） | 分类 | 关键断言/原因及业务影响 |
|---:|---|---|---|
| 1 | `capability_contract_test` / `test_tool_schema_failure_is_persisted_and_stops_run` | 实现 | 缺 `output_schema_validation` 记录；工具输出 schema 失败的状态/审计不可确认。 |
| 2 | `input_protocol_test` / `test_api_rejects_stale_answers_and_streams_waiting_then_completion` | 夹具 | 假 Executor 无 `verify_node`，期待成功但 Run failed；旧人工输入 API 测试夹具需适配门禁。 |
| 3 | `input_protocol_test` / `test_redis_round_trip_resumes_after_rebuilding_service` | 夹具 | 同类 verifier 夹具失配，旧恢复测试期望 succeeded。 |
| 4 | `mvp_pipeline_test` / `test_agent_tool_python_agent_pipeline_without_mcp` | 夹具 | 假 Executor 未实现 AI verifier，旧 Agent/Tool/Python 串行成功断言失配。 |
| 5 | `p7b_scenario_runtime_test` / `test_s04_decimal_costs_and_materials_are_deterministic_drafts` | 夹具 | 期望 `passed`，实际材料检查返回 `ready_for_ai_review`；S04 尚需 AI/人工复核，不能改称正式通过。 |
| 6 | `p7r1_output_contract_test` / `test_previous_result_is_typed_only_when_validated_value_exists` | 历史 | 旧“已验证结构化值”绑定断言不符；旧下游类型约束风险。 |
| 7 | 同文件 / `test_s02_final_prompt_avoids_duplicate_report_and_keeps_both_protocols` | 历史 | 旧 S02 Claim–Evidence 提示文字/协议断言不符。 |
| 8 | 同文件 / `test_s07_final_uses_the_shared_strict_claim_schema` | 历史 | 旧 S07 严格 Claim schema 提示断言不符。 |
| 9 | 同文件 / `test_invalid_output_is_bounded_and_server_status_is_authoritative` | 实现 | 结构错误出现重复项，服务端错误/修复界限需核对。 |
| 10 | 同文件 / `test_sop_finalizer_builds_citation_index_then_runs_full_validation` | 实现 | 期望 citation index，实际为 None；严格引用链不能声称通过。 |
| 11 | 同文件 / `test_sop_rejected_repair_keeps_body_and_fails_closed` | 夹具 | 执行替身缺 AI verifier，先因该门禁失败，未测到期望 repair metadata。 |
| 12 | 同文件 / `test_validated_result_is_persisted_and_bound_to_next_node` | 实现 | 期望失败，实际 succeeded；校验后绑定/拒绝路径需复核。 |
| 13 | `project_planning_rag_test` / `test_failed_final_ledger_does_not_create_docx` | 历史 | 旧 S02 ledger 失败却 succeeded；历史交付门禁风险。 |
| 14 | 同文件 / `test_incomplete_new_protocol_fails_closed_through_task_service_and_sop` | 历史 | 旧引用协议不完整却 succeeded；历史引用门禁风险。 |
| 15 | 同文件 / `test_input_selected_project_material_is_saved_in_run_and_ledger` | 历史 | 缺 `evidence_ledger`。 |
| 16 | 同文件 / `test_new_occurrence_protocol_runs_through_task_service_and_sop` | 历史 | 缺 `evidence_ledger`。 |
| 17 | 同文件 / `test_s02_custom_copy_runs_rag_review_and_word_writer` | 历史 | 缺 `citation_verification`。 |
| 18 | `s02_content_repair_test` / `test_s02_v2_repairs_only_the_mixed_occurrence_then_runs_full_ledger` | 夹具 | 旧替身缺 AI verifier，先抛该运行错误。 |
| 19 | `s02_lightweight_template_test` / `test_s02_v5_taskservice_sop_generates_run_linked_draft_docx` | 实现 | 最终 `delivery_profile` 实为 `ai_verified_draft`，测试与中间 `s02_lightweight_draft` 标签不一致；交付标签需统一解释。 |
| 20 | `s02_v3_structured_output_test` / `test_s02_v3_taskservice_sop_verifier_and_artifact_gate` | 历史 | 缺 `s02_v3_structured_output` 记录。 |
| 21 | 同文件 / `test_real_run_missing_empty_arrays_passes_full_run_and_writes_docx` | 历史 | 同上。 |
| 22 | 同文件 / `test_s02_v3_required_business_field_still_fails_and_blocks_docx` | 历史 | 旧必填业务字段错误却 succeeded；严重历史门禁风险。 |
| 23 | 同文件 / `test_real_run_kind_conflict_fails_service_run_and_blocks_docx` | 历史 | 旧类型冲突却 succeeded。 |
| 24 | 同文件 / `test_s02_v3_invalid_inner_json_stops_before_artifact_writer` | 历史 | 旧内部 JSON 错误却 succeeded。 |
| 25 | 同文件 / `test_s02_v3_rejects_redacted_legacy_run_protocol_before_artifact_writer` | 历史 | 旧脱敏协议错误却 succeeded。 |
| 26 | 同文件 / `test_s02_v3_policy_failure_does_not_retry_or_create_artifact` | 历史 | 旧政策校验失败却 succeeded。 |
| 27 | `s02_v4_delivery_test` / `test_s02_v4_and_v41_historical_templates_keep_versioned_contracts` | 历史 | 预期 v4/v4.1，实际当前 v5.1；旧版合同兼容未证。 |
| 28 | 同文件 / `test_s02_v4_taskservice_sop_demo_draft_writes_marked_docx_after_ledger` | 历史 | 缺 `evidence_ledger_passed`。 |
| 29 | 同文件 / `test_s02_v41_taskservice_sop_demo_writes_marked_docx_after_safe_downgrade` | 历史 | 旧 demo 标识/交付正文与现有渲染不符。 |
| 30 | 同文件 / `test_s02_v41_adapter_pass_does_not_override_ledger_failure_or_write_docx` | 历史 | 测试 patch 的旧 `materialize_s02_project_planning_ledger` 接口已不在 `_service`。 |
| 31 | 同文件 / `test_s02_v41_strict_association_failure_blocks_taskrun_and_docx` | 历史 | 旧关联校验失败却 succeeded。 |
| 32 | 同文件 / `test_s02_v4_strict_missing_source_blocks_success_status_and_docx` | 历史 | 旧来源缺失却 succeeded。 |
| 33 | `s07_rag_integration_test` / `test_invalid_quote_is_rejected_before_model_verifier_or_artifact` | 历史 | 旧无效 quote 断言期待 `awaiting_input`，实际 failed。 |
| 34 | 同文件 / `test_valid_citation_reaches_deterministic_citation_tool` | 历史 | 缺 `output_contract_validation`。 |
| 35 | 同文件 / `test_preflight_keeps_inaccessible_knowledge_base_as_permission_denial` | 夹具 | 测试预期当前 S07 v4，Registry 当前 v5。 |
| 36 | 同文件 / `test_preflight_reports_document_not_found_as_data_error_not_permission` | 夹具 | 同上。 |
| 37 | 同文件 / `test_preflight_reports_unindexed_document_separately` | 夹具 | 同上。 |
| 38 | 同文件 / `test_report_rag_is_task_native_and_does_not_discover_session_tool` | 夹具 | 同上。 |
| 39 | `template_output_test` / `test_structured_schema_failure_is_persisted_and_fails_run` | 实现 | 结构 schema 应阻断而 Run succeeded；输出契约门禁风险。 |
| 40 | `template_test` / `test_registry_returns_locatable_missing_and_invalid_file_errors` | 夹具 | 旧目录预期 `s01_project_init.v2.json`，实际 Registry 当前 v3。 |
| 41 | 同文件 / `test_template_api_reports_invalid_catalog_without_exposing_paths` | 夹具 | 旧 v2 文件前提导致 `template_file_missing`，未到预期 invalid JSON。 |
| 42 | 同文件 / `test_registry_rejects_invalid_template_schema_and_extra_files` | 夹具 | 同样因旧文件前提得到 `template_file_missing`。 |
| 43 | `template_version_test` / `test_s07_v5_uses_ai_acceptance_and_keeps_v4_history` | 历史 | 旧 v4 步骤存在 `output_contract`，与测试的“全为空”历史断言不符。 |

分类合计：**夹具/预期 13、实现缺口 6、历史兼容 24、外部依赖不可用 0、原因待确认 0**。这是对失败表征和代码路径的归类，不能据此断言 24 项历史问题不会触达仍可运行的旧 Run。尤其 S02 v2～v4、S07 v3/v4 的严格引用、schema 与交付门禁，在旧版运行前必须单独解决或隔离。R7 当前版本九场景测试没有通过降低断言或跳过这些 43 项来获得通过。

差异比较脚本：`%TEMP%\compare_task_failure_signatures_r6_v4.py`。结果为失败 nodeid 43/43 相同且顺序一致；测试 header 43/43、异常类型 43/43、断言行 43/43、项目堆栈帧 43/43、归一化完整签名 43/43 相同；新增/消失 nodeid 均为 0。R7 的 93 项新测试全部通过，没有新增失败。R6 的完整套件为 358 passed/43 failed，包含当时隔离 Redis/PostgreSQL 实测；R7 不连接数据库，相关项按环境变量跳过，因此通过数不直接相减判断回归。

## 8. R7-F：命令、测试统计和 Git 白名单

```powershell
.\.venv\Scripts\python.exe -m pytest tests/task_r7_nine_scenarios_integration_test.py -q --tb=short
.\.venv\Scripts\python.exe -m pytest tests/task_sop_control_test.py tests/task_sop_template_retry_test.py tests/task_sop_r6_integration_test.py -q --tb=short
$dagTests = Get-ChildItem tests -File -Filter 'task_dag_*.py' | ForEach-Object { $_.FullName }
.\.venv\Scripts\python.exe -m pytest @dagTests -q --tb=short
$taskTests = Get-ChildItem tests -File -Filter 'task_*.py' | ForEach-Object { $_.FullName }
.\.venv\Scripts\python.exe -m pytest @taskTests -q --tb=short
.\.venv\Scripts\python.exe -X utf8 "$env:TEMP\compare_task_failure_signatures_r6_v4.py" "$env:TEMP\lxscope-task-r6-full-final-20261022-215938.txt" "$env:TEMP\lxscope-task-r7-full-20261023.txt"
git diff --name-only
git status --short
```

| 组别 | 结果 |
|---|---|
| R7 九场景专项 | **93 passed**（九场景 × 完整、失败重跑、Pause/Continue、Stop、Input 恢复、AI 拒绝、Tool 重跑、两种损坏快照、版本漂移，加 3 个 KB 权限负例）。 |
| SOP 控制/重跑/R6 | **10 passed、4 skipped、9 subtests passed**；4 个真实后端用例因未设置隔离测试 URL 跳过，R6 结果另见 R6 报告。 |
| DAG 专项 | **53 passed、11 skipped、1 warning**；需要隔离 DB 的 11 项未在 R7 重跑，warning 是 FastAPI/Starlette 422 常量弃用提示。 |
| 完整 `tests/task_*.py` | **433 passed、43 failed、18 skipped、1 warning、30 subtests passed**，111.13 秒；43 项签名与 R6 完全一致。 |

阶段完成前 `git diff --name-only` 与起点一致，仍是此前存在的 39 个 tracked 路径；本阶段只新建上述 R7 测试与本报告两个白名单内文件。`git status --short` 相对起点只应多这两个未跟踪项。所有白名单外已有文件和 Git 修改保持原状；没有提交。没有修改前端、AgentScope 主框架、`main.py`、其他业务模块、九大官方模板、依赖、全局配置或数据库 Migration。

## 9. 结论与生产准入

| 判定对象 | 结果 |
|---|---|
| 当前 S01～S09 模板的 TaskService 合成隔离完整链路 | **PASS**，每场景真实实例化、Preflight、调度、业务适配、状态/事件和产物渲染。 |
| 九场景同 Run 安全节点重跑、Pause/Continue/Step/Stop、Input 和快照恢复 | **PASS**（Memory 隔离链路；R6 的通用 Redis/PostgreSQL 持久化证据另列）。 |
| 真实业务模型/KB/MCP/审批/归档/ERP/Workspace 集成 | **BLOCKED**，未提供隔离授权外部环境。 |
| 历史 S02/S07 严格引用、输出契约及交付门禁 | **FAIL / 未关闭**，43 项基线中的相关测试仍失败，部分表征为应拒绝却 succeeded。 |
| DAG 回归 | **PASS**（当前环境可运行的 53 项）；需要隔离数据库的 11 项本轮 skipped。 |
| 生产准入 | **NO-GO**。 |

进入生产前至少需要处置 43 项中涉及真实校验门禁的失败，明确旧模板/旧 Run 的隔离或修复策略；在授权隔离外部系统中复测九场景真实来源、真实模型验收、引用语义与业务副作用核验，并对正式产物/审批/归档/ERP 写入建立确认与审计证据。本阶段到此停止，不进入 R8、不启用生产功能、不提交代码。
