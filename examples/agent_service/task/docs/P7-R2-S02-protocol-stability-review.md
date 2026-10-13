# P7-R2：S02 输出协议一致性修复与结构化生成改造评审

日期：2026-10-13
分支：`integrate/dev_l`
基线 HEAD：`deae39b`
范围：Gate 0、Gate 1、Gate 2；Gate 3 仅做设计与现有能力验证。

## 结论

- **Gate 0：完成。** 根据源码和用户提供的脱敏 Run 诊断复核了规则冲突、协议分派、失败修复和结构化输出路径。
- **Gate 1：完成。** 引用物化器按 `EvidencePolicy` 决定强制引用类别；S02 待处理状态与新旧台账读取规则对齐；协议解析失败不再暗示来源校验已执行。
- **Gate 2：完成。** 增加脱敏失败样本回归，并验证旧、新协议经 TaskService/SOP、证据校验和 DOCX 门控的成功与失败路径。
- **Gate 3：设计与可行性验证完成，未实施结构化重构。** 建议以 S02 v3 发布新输出契约；v1/v2 及历史 Run 保持现状。
- 未连接真实认证会话重跑用户的 S02 Run；真实浏览器和真实模型联调为 **NOT TESTED**。

## Gate 0：根因与源码证据

| 现象 | 根因 | 源码依据 |
|---|---|---|
| 分析/建议在策略上可不引用，却被引用物化器拒绝 | 协议提示允许无引用 `analysis` / `recommendation`，但 `materialize_task_citations_from_bindings()` 曾把所有非 `unverified` Claim 都当作必须有引用。两处规则冲突。 | Claim 提示：`_evidence_contract.py:93-96`；`EvidencePolicy` 强制类别：`:258-305`；修复后的物化器：`:3281-3342`。S02 的必引类别为 `fact`、`rule`、`historical_case`：`_scenario_evidence_policies.py:35-40`、`:103-108`。 |
| 空 `task-citations` 被拒绝 | 引用验证器原来把空数组一概视为无效；纯分析/建议没有正文引用标记时仍需要保留唯一协议区块。现在只有“正文无引用编号且 `citations` 为空”才通过；有任何 `[C#]` 仍失败。 | `_citation_verifier.py:47-170`。 |
| “待补充/待确认”与台账状态不一致 | 新协议已能用 `UnverifiedRecord.status` 表达三个待处理状态，但 S02 旧台账扫描仍依赖正文关键词；旧版 `task-evidence-claims` 对 status 又必须向后兼容。现在新协议用结构化 status 决定状态，旧格式支持可选 status，缺省仍为历史 `pending_verification`；只有已经完成 Claim 映射的引用行才从旧版事实扫描中排除。 | `UnverifiedRecord`：`_evidence_contract.py:234-255`；状态展示：`_s02_project_planning_evidence.py:260-270`、`:500-540`；旧协议验证与已映射引用行判断：`:936-1270`。 |
| 新协议不完整时误走旧协议，出现“必须包含唯一 task-evidence-claims” | 分派若依赖完整 JSON 围栏，截断的 `task-claim-evidence` 会被误认为没有新协议。现在通过开围栏标识先分派；同时出现新旧标识时返回 `mixed`，由新校验路径失败关闭。 | `detect_claim_evidence_protocol()`：`_evidence_contract.py:2157-2174`；S02 分派：`_s02_project_planning_evidence.py:853-890`。 |
| 协议失败后来源校验结果表述不准确 | JSON 解析/Schema 失败时曾可能继续呈现下游校验状态。现在区分代码围栏、JSON、Schema 阶段；解析或 Schema 未成功，Claim 位置、来源身份/哈希、引用关联和语义状态明确为 `not_evaluated`。 | 新协议解析及早退：`_evidence_contract.py:2177-2430`；统一 `verification_scope`：`:3790-3860`；引用区块解析：`_citation_verifier.py:47-170`。 |
| 修复响应非法或夹带代码围栏 | 修复响应不是可安全解析的协议时，确定性物化器拒绝，不剥离围栏、不补写字段；完整原文和授权来源校验仍是成功前提。修复超长正文不进入重试。 | 修复入口及严格 JSON 解析：`_evidence_contract.py:460-550`、`:641-760`；长度限制：`:85`、`:523`、`:1344`。 |
| 同一行/段落的事实、引用与待核实内容产生级联诊断 | Claim/Occurrence 与正文位置无法唯一对应时，后续 Binding 不能靠相同引用号自动归属。解析器报告位置冲突；局部拆分仅在正文、引用区块锁定且连续原文唯一匹配时才可生成候选，随后还要通过完整授权和哈希校验。含义不清或改变无关正文时拒绝。 | `_anchored_blocks()` 与表格行检查：`_evidence_contract.py:2720-2760`、`:3519-3605`；锁定报价局部拆分：`:1390-1600`；回归：`task_s02_content_repair_test.py:175-334`。 |

### 真实失败形状复核

用户提供的运行编号 `fb0aaacf…`、`9c8d0b3…`、`c3ebc42…`、`98606e5…` 与脱敏诊断分别表明：锚点/Occurrence 和 Binding 级联错误、新旧协议分派错误、修复回复 JSON 不完整、以及同一 Occurrence 混合引用事实与待核实事项。

脱敏固定夹具保留原报告的 `29 结构 / 61 引用 / 5 待核实` 统计作为测试输入说明。当前校验器在该夹具上只报告 `15 结构 / 61 引用 / 5 待核实`，并不再产生未注册锚点和错误的“Binding 未声明”级联项；仍存在的失败继续拒绝输出。该数量变化是诊断去级联，不等于所有事实已获得证据。

P7-R1 已实现的实际修复包括：根据开围栏分派新协议；仅允许一次、仅针对缺失/不完整协议外壳的修复；锁定引用不得被模型改写；局部正文拆分必须保持目标内容并经过同一证据校验。诊断改进包括 JSON 错误阶段/位置、结构与来源失败的分组，以及解析未完成时的 `not_evaluated`。这些机制**不会保证每次模型修复都成功**；不可解析、过长、夹带额外内容或含义冲突时仍会失败关闭。

## Gate 1：实现变更

### Claim 引用规则

`materialize_task_citations_from_bindings()` 现在采用当前 Run 的场景 `EvidencePolicy.require_citation_claim_kinds`，S02 对 `fact`、`rule`、`historical_case` 仍要求引用；`analysis`、`recommendation` 可依策略无引用。带引用的分析/建议仍须对每个正文 Occurrence 提供独立 Binding，并通过来源、授权主体、哈希、连续原文及位置校验。`unverified` 不能关联正文引用。

TaskService 调用点将场景策略传给物化器：`_service.py:3246-3290`。物化仅生成引用索引候选，不代替完整验证。无引用文档的空引用索引支持由 `_citation_verifier.py:155-170` 限定；事实缺引用测试仍失败。

### 待处理状态和诊断

统一状态值为 `pending_verification`、`pending_supplement`、`pending_confirmation`，老记录未声明 status 时沿用 `pending_verification`。S02 展示状态分别为“待核实”“待补充”“待确认”。新协议的状态由结构化 `UnverifiedRecord` 决定，而不是从一个关键词推断；每项仍需独立的 ID、Claim/Occurrence、原因和预期证据类型。

诊断保留 `error_code`、阶段、位置及 Claim/Occurrence 或 Evidence 引用；安全摘要不回显模型完整原文。协议解析失败后来源身份、哈希和语义关联为未评估。旧协议兼容错误仍使用 `legacy_compatibility_validation` 汇总码，这是旧 Schema 没有稳定 ID 字段的限制。

### 修改文件

- `examples/agent_service/task/_evidence_contract.py`：按 EvidencePolicy 物化引用；细分协议诊断阶段；保持失败关闭；确认重试门仅接收安全的协议外壳失败。
- `examples/agent_service/task/_citation_verifier.py`：允许“无正文引用标记 + 空引用列表”，其他引用一致性/来源校验未放宽。
- `examples/agent_service/task/_s02_project_planning_evidence.py`：兼容待处理状态；避免把已映射且已校验的引用行重复报为未登记事实；保留解析范围状态。
- `examples/agent_service/task/_service.py`：按 Run 模板版本传入 EvidencePolicy，并在失败结果中保留验证阶段；失败账本仍阻止产物成功。
- `tests/task_p7r2_protocol_stability_test.py`：79 Claim 样本、诊断阶段、表格一行多锚点、额外修复围栏、过长正文和 S02 缺失事实引用。
- `tests/task_p7r1_output_contract_test.py`：无引用分析、带引用建议与 S02 策略验证。
- `tests/task_project_planning_rag_test.py`：旧 S02 路径与新 Claim–Occurrence 路径的 TaskService/SOP 和 DOCX 门控回归。
- 本报告。

以上文件均在用户授权的 Task 白名单内。本轮未修改模板 JSON、AgentScope 官方代码、共享认证、KnowledgeBase 核心、SOP 官方引擎、`main.py`、非 Task 页面、数据库迁移或依赖。保留了原有工作区变更；未暂存、提交或推送。

## Gate 2：回归覆盖

| 风险形状 | 回归依据 |
|---|---|
| 79 个 Claim/Occurrence/Binding | `task_p7r2_protocol_stability_test.py::test_large_claim_occurrence_binding_document_with_79_claims`。 |
| 非法 JSON、未闭合围栏、重复 JSON 键、Schema 错误及阶段状态 | `task_p7r2_protocol_stability_test.py::test_protocol_parse_failures_have_distinct_phase_diagnostics`；旧协议不回退：`task_p7r1_output_contract_test.py::test_incomplete_current_protocol_never_falls_back_to_legacy`。 |
| 修复回复夹带额外围栏、正文超限 | `task_p7r2_protocol_stability_test.py::test_protocol_repair_rejects_extra_code_fence_and_oversized_body`。 |
| 同一事实多处出现、每个 Occurrence 独立 Binding；表格事实定位 | `task_p7r1_output_contract_test.py::test_one_claim_can_have_two_explicit_occurrences`、`::test_anchor_text_accepts_before_and_after_marker_and_table_rows`。 |
| 表格单行多个 Claim 锚点 | `task_p7r2_protocol_stability_test.py::test_multiple_claim_anchors_on_one_table_row_are_rejected`，按结构异常失败关闭。 |
| 无引用分析、带引用建议、事实漏引 | `task_p7r1_output_contract_test.py::test_materializer_accepts_uncited_analysis_and_cited_recommendation`；`task_p7r2_protocol_stability_test.py::test_analysis_only_document_can_materialize_an_empty_citation_index`、`::test_s02_policy_still_requires_citations_for_project_facts`。 |
| 待核实、待补充、待确认及多个事项独立关联 | `task_project_planning_rag_test.py::test_new_occurrence_protocol_runs_through_task_service_and_sop`；`task_p7r1_output_contract_test.py::test_multiple_unverified_items_are_bound_to_distinct_occurrences`。 |
| 未授权来源、伪造哈希、来源摘录不匹配 | `task_evidence_contract_test.py::test_invalid_schema_unauthorized_source_and_forged_hash_fail_closed`；`task_s02_citation_block_protocol_test.py` 的 Evidence ID 与连续原文测试。 |
| 局部修复成功后完整校验；失败不生成 DOCX | `task_s02_content_repair_test.py::test_s02_v2_repairs_only_the_mixed_occurrence_then_runs_full_ledger`；`task_project_planning_rag_test.py::test_incomplete_new_protocol_fails_closed_through_task_service_and_sop`、`::test_failed_final_ledger_does_not_create_docx`。 |
| 老协议和新协议均经 TaskService/SOP，成功后产物受门控 | `task_project_planning_rag_test.py::test_s02_custom_copy_runs_rag_review_and_word_writer`（旧协议）；`::test_new_occurrence_protocol_runs_through_task_service_and_sop`（新协议）。 |
| 模板历史与 S07 v3/v4 | Task 全量中的 `task_template_version_test.py`、`task_template_test.py`、`task_s07_rag_integration_test.py`；本轮没有改模板或 S07 执行逻辑。 |

## Gate 3：S02 v3 结构化输出设计与可行性

### 当前能力与限制

- Task 已有可选 `NodeOutputContract`：`_models.py:271-340`；`TemplateStep` 支持它：`_template_models.py:106-167`；编译时只在显式配置时传递：`_template_compiler.py:431-435`。
- 对 `json_schema` 的定义和结果有安全校验：`_output_validation.py:81-132`；Agent 执行器为 Schema 增加输出要求并通过 `generate_structured_output(... structured_model=AgentStepOutcome)` 接收外层结果：`_agentscope_executor.py:342-410`、`:488-517`。当前 JSON Schema 是 Task 提示 + 服务端确定性验证；Provider 仍原生结构化的是外层 `AgentStepOutcome`，不能据此宣称 Provider 原生强制了内层业务 Schema。
- 现有契约不允许同一 `NodeOutputContract` 同时配置 JSON Schema 与 `citations/claim_evidence` 验证：`_models.py:311-340`。S02 v3 应将 JSON 结构校验作为节点输出契约，再由 Task 内 S02 适配器确定性生成现有 Claim–Evidence/引用格式，并调用现有来源校验和账本逻辑，不新建 Evidence Store。
- S02 v1/v2 模板最终步骤 `produce_planning_output` 分别位于模板文件第 99、176 行，未声明 `output_contract`。模型和模板模型把该字段定义为可选，编译器也按需写入。服务端后来用模板 ID 路由 S02 Claim–Evidence 处理：`_service.py:3246-3265`。当前工作树没有记录 v2 当时为何省略该字段的发布说明；能确认的是协议字段为可选并存在后加的兼容分支，不能从代码证明作者的具体历史动机。

### 建议的 v3 节点协议

Agent 只返回受 JSON Schema 约束的章节与业务内容：章节、独立事实/规则/历史案例/分析/建议、待处理事项及其状态和原因。来源候选只包含待匹配的逐字摘录（和可选的业务分类提示），不包含 `evidence_id`、文档 ID、授权主体、哈希、Citation ID 或“验证通过”状态。Agent 不生成 Claim/Occurrence 标识，也不自行声明来源已授权。

Task 服务端使用已有单次模型调用结果：

1. 校验结构化 JSON、字段/大小/深度约束；不做无界重试。
2. 将内容渲染成报告章节，在服务端为每条正文声明创建稳定 Claim/Occurrence 锚点。
3. 仅在当前 Run 的授权 Evidence 快照中，按业务类型及逐字连续摘录匹配来源。候选无法唯一解析、来源类型不符、无权限或原文不匹配时失败关闭；不得猜测来源。
4. 由服务端生成 Citation ID、Evidence ID 关联、Binding、UnverifiedRecord 和协议区块，再调用现有通用校验器及 S02 规则。语义支持仍明确保留人工复核。
5. 最终内容重新校验通过后，才进入现有 Artifact Writer；失败不能创建成功 DOCX。

该方案不增加模型调用或用户可见节点，但需要 Task 层新增结构化结果到 Markdown/协议的确定性适配。由于最终节点输入/输出协议改变，应按 Registry 发布 `s02_project_planning.v3.json`；不得改变 v1/v2 或历史 Run 的解释。发布前测试 Provider 不支持内层结构化 Schema 时的有限失败行为，并测试来源候选重复/歧义、权限隔离、错误证据、重写后二次校验和无产物门控。

## 验证记录

- 定向回归：`pytest tests/task_p7r2_protocol_stability_test.py tests/task_p7r1_output_contract_test.py tests/task_project_planning_rag_test.py tests/task_s02_content_repair_test.py tests/task_s02_citation_block_protocol_test.py -q` → **68 passed, 10 subtests passed**。
- Task 全量：按 `tests/task_*_test.py` 运行 → **260 passed, 2 skipped, 21 subtests passed**。
- 两项 PostgreSQL 测试因未设置 `LXSCOPE_TASK_TEST_DATABASE_URL` 而 skipped；没有连接数据库，不能称为真实 PostgreSQL 集成通过。
- Python：`.venv\Scripts\python.exe -m compileall -q examples/agent_service/task tests` → **通过**。
- `git diff --check`：报告生成后执行；结果见交付摘要。
- 前端没有修改，未运行前端 ESLint/build。没有真实登录浏览器或用户 Run 快照访问权，真实 S02 Run 为 **NOT TESTED**。

## 真实 Run 复验步骤

部署本轮 Task 后端代码后，以真实登录用户创建新的 S02 v2 TaskRun（不复用失败 Run），按流程提交项目资料、初稿复核反馈并运行到“整理策划输出”。检查最终节点：新协议解析阶段为 `passed`；项目事实只由该 Run 授权项目资料支撑；模板规则只由策划模板支撑；待处理事项的 status 与台账一致；`final_citation_verification`、`evidence_ledger` 均通过；最终 DOCX 与 Run 关联。若失败，提供脱敏的 `all_diagnostics`、`verification_scope`、最终节点输入/输出及 Artifact 列表。不得将语义支持状态当作机器通过结论。
