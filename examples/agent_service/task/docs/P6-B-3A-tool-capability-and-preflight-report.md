# P6-B-3A：Task 九场景 Tool/MCP 能力契约与运行前校验报告

## 1. 结论摘要

- **能力契约：PASS**。建立 Task 自有的逻辑能力分类和 S03 `expert.lookup`、`expert.conflict_check`、`expert.rank` 输入/输出候选契约；实际 Tool 名称仍由现有模板编译器和当前会话 Toolkit/MCP 目录解析，没有新增 Tool Registry。
- **运行前校验：PASS**。复用现有 `/tasks/{task_id}/preflight`，分别报告工具存在、Schema 兼容、当前权限、业务数据源验证和副作用。校验只枚举 Tool、校验 Schema 并调用 AgentScope `PermissionEngine`，不会调用业务 Tool；失败时拒绝创建 Run。生产执行器仍会在真实调用前复查权限。
- **九场景回归：PASS（模板读取与 Task 专项自动化）**。九个 v1 模板均可读取和分类；本阶段没有修改模板 JSON。所有真实业务数据源未验证，不能据此声称九场景已在生产环境可运行。
- **真实业务 Tool 接入：BLOCKED**。没有专家目录、回避规则或评分系统的已验收接口，也没有可供本次验证的生产会话和真实数据源。测试使用的受控 Tool 与专家记录均为 fixture。
- **正式 HumanApproval：BLOCKED**。本阶段未开发审批身份、审批授权、审计或正式等待/恢复机制；S03 v1 的 `collect_human_review` 仍是普通 Input 语义。

## 2. 基线、边界与工作区

- 分支：`integrate/dev_l`；基准 `HEAD`：`0fbbd85`。
- 已遵循附件白名单；未修改 `src/agentscope/**`、`examples/agent_service/main.py`、认证/身份/持久化/迁移、Task 外业务模块、共享 API Client、非 Task 页面、导航、依赖或构建配置。
- 未修改九个系统模板 JSON、S03 v1，也没有新增 StepType、Planner、Toolkit、MCP 调用器或 SOP 调度逻辑。
- 工作区原有无关改动保留且不纳入本阶段提交：`sales-console/apps/admin-web/src/App.tsx`、`sales-console/services/sales-api/src/app.ts`、`sales-console/compose.dev.yaml`、`.pnpm-store/`、`examples/agent_service/.playwright-mcp/`、`examples/agent_service/blobs/`、`output/`、`tmp/`，以及此前未提交的 P6-A、P6-B-0 文档。

## 3. 修改文件与源码位置

行号为当前工作树行号。

| 文件 | 位置 | 修改目的 |
|---|---:|---|
| `examples/agent_service/task/_capability_contracts.py` | 1–601，特别是 258–378、384–477 | 新增 Task 逻辑能力目录、分类、副作用标记和 S03 严格输入/输出契约；不承载实际 Tool 注册。 |
| `examples/agent_service/task/_models.py` | 243、595–637 | 为 Task 节点增加可选 `capability_key`，增加结构化能力检查结果字段。 |
| `examples/agent_service/task/_template_compiler.py` | 78–115、196–290、351–365 | 复用服务端环境别名与会话 Tool 目录绑定；S03 检查注册 Tool 的输入 Schema，并把能力键和严格输出 Schema 编译进现有 Tool 节点。 |
| `examples/agent_service/task/_template_validation.py` | 403–443 | 从模板服务端快照重新导出能力身份/Schema；兼容未包含新增字段的旧 Task 副本。 |
| `examples/agent_service/task/_agentscope_executor.py` | 160–245、382–388、624–729、993–1025 | 枚举当前会话 Toolkit/MCP、无副作用地预检 PermissionEngine、执行时继续校验权限和参数，并向后续 Agent 暴露有界执行历史及结构化 Tool 结果。 |
| `examples/agent_service/task/_output_validation.py` | 85–121 | 复用已有 Schema 验证器安全解析并验证 Tool 输出 JSON 文本，沿用大小和 Schema 限制。 |
| `examples/agent_service/task/_service.py` | 497–606、607–805、824–930、1497–1529、1589–1620 | 实现只读 Preflight、运行创建前强制复查、能力错误结构化返回、TaskRun/节点输出验证留痕和结构化执行历史。 |
| `examples/web_ui/frontend/src/api/task.ts` | 192–211、352–366、401–402 | 增加能力结果类型与 Task 专属 Preflight API。 |
| `examples/web_ui/frontend/src/pages/task/index.tsx` | 208、632–646 及能力状态展示区域 | 运行前展示服务端检查结果；未就绪时不调用 Run 创建接口。 |
| `tests/task_capability_contract_test.py` | 111–650 | 新增能力契约、权限、重复绑定、目录异常、副作用禁止、用户隔离、输出失败留痕与证据传递测试。 |
| `tests/task_template_test.py` | 本次差异位于模板实例化/预检断言 | 调整 S03 fixture 为新契约 Schema，并断言生产默认数据源未验证时运行被阻止。 |
| `tests/task_template_version_test.py` | S03 Tool fixture | 让历史版本兼容测试使用 S03 明确契约。 |
| `tests/task_mvp_pipeline_test.py` | Tool fixture / Preflight adapter | 让已有 Task pipeline fixture 显式提供真实形状的测试 Schema 和只读权限预检。 |
| `tests/task_template_output_test.py` | Tool fixture / 测试执行器 | 继续覆盖 P6-B-2 Artifact 回归；使用隔离 fixture 输出，不代表真实专家服务或生产数据源。 |
| `examples/agent_service/task/docs/P6-B-3A-tool-capability-and-preflight-report.md` | 全文 | 本阶段交付报告。 |

`_router.py` 与 `_sop.py` 未修改：复用现有 Preflight 路由及官方 `SOPEngine`/TaskSOPStep 适配。

## 4. 现有机制复用情况

1. **能力绑定**：模板只写稳定的逻辑 `capability_key`。`TaskTemplateCompiler` 读取服务端 `LXSCOPE_TASK_CAPABILITY_TOOL_MAP`（若设置）将逻辑键映射为实际 Tool 名；未配置别名时仅接受当前认证会话中名称精确相同且唯一的 Tool。实际 Tool 目录由 `AgentScopeTaskExecutor.list_tools()` 调用会话 Toolkit 的 Schema 接口取得。模板未硬编码业务 URL、密钥或数据库连接。
2. **执行与权限**：继续由已有 `ToolStepConfig` 和 AgentScope Toolkit/MCP 执行。Preflight 调用现有 `PermissionEngine.check_permission()`，不调用 Tool；运行时 `_execute_tool_step_in_scope()` 再校验参数 Schema、当前会话权限，并拒绝需要交互确认或 AgentState 注入的 Tool。`is_read_only` 以及 Task 能力目录中的副作用标记共同阻止写入类能力在本阶段运行。
3. **输出校验**：只对声明了输出 Schema 的节点调用现有 Task verifier。S03 Tool 的原始文本必须能解析为 JSON 并满足能力 Schema；输出失败进入已有 SOP 步骤失败状态，验证状态、原因和 Tool 输出存入节点/TaskRun，不返回通过结果。
4. **结果传递**：Task 从成功的 `NodeRunRecord` 构建最多八个前序节点历史；结构化 Tool JSON 作为 `structured_result` 传给后续 Agent。Agent 执行提示明确附上按顺序排列的完整前序结果，模板 Tool 参数的 `{{previous_output}}` 仍遵循既有线性语义。
5. **用户隔离**：TaskService/TaskStore 继续以 `(user_id, task_id/run_id)` 读取任务和 Run。新增测试确认他人不能预检 Task 或读取包含 Tool 结果的 Run，且越权预检不会枚举工具、检查权限或执行 Tool。

## 5. 九场景 Tool/MCP 能力矩阵

模板参数列出当前 v1 JSON 中实际的 `config.arguments` 形状；除 S03 外，这些 Tool 节点没有模板级输出 Schema。非 S03 的 Tool 参数和结果必须由后续真实服务的 Tool JSON Schema 及业务验收补齐，不能仅凭下面的占位参数视为契约已就绪。

| 场景 / 模板步 | 逻辑能力 / 类别 | 当前参数形状 | 输出结构 | 绑定、权限、只读及数据状态 |
|---|---|---|---|---|
| S01 `search_similar_projects` | `project.similar_search` / 数据查询 | `query={{previous_output}}` | 无专用输出 Schema | 实际 Tool 名运行时解析；权限由会话 PermissionEngine；只读属性读取 Tool 元数据；数据源未验证。 |
| S01 `check_project_rules` | `project.rule_check` / 规则校验 | `project_profile={{previous_output}}` | 无专用输出 Schema | 同上；规则版本、证据格式和数据源未验证。 |
| S01 必需但无 Tool 步骤 | `project.authorization.read` / 数据查询 | 无节点参数 | 未定义 | 编译/预检判为缺失；当前没有对应节点或业务授权接口。 |
| S02 `retrieve_planning_template` | `planning.template_retrieve` / 数据查询 | `query={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；会话权限；真实模板库未验证。 |
| S02 `verify_planning_citations` | `document.citation_verify` / 规则校验 | `draft={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；引用规则/来源未验证。 |
| S02 必需但无 Tool 步骤 | `document.render` / 文件处理 | 无节点参数 | 由 Task ArtifactConfig/Writer 承担，但 v1 无 ArtifactConfig | 当前模板未配置产物，能力不能由“存在 Artifact Writer”推定为已满足；实例化/预检会阻止缺失能力。 |
| S03 `lookup_expert_candidates` | `expert.lookup` / 数据查询 | `query={{previous_output}}` | `task.expert-capability.v1`；候选 ID、授权字段、证据引用、数据源与版本、查询时间、查询上下文 | 仅候选只读契约；要求专家目录查询权限标签；业务数据状态固定为未验证，实际 Tool 缺失/兼容性/权限状态由当前会话检查。 |
| S03 `check_expert_conflicts` | `expert.conflict_check` / 规则校验 | `candidates={{previous_output}}` | 明确 `clear/conflict/unknown`、规则版本、检查时间和证据引用 | 仅候选只读契约；要求回避规则查询权限标签；`unknown` 及格式/证据不合法失败关闭；业务数据状态固定为未验证。 |
| S03 `rank_expert_candidates` | `expert.rank` / 确定性计算 | `candidates={{previous_output}}` | 排名、分数、分项、评分算法版本、来源和证据 | 仅候选只读契约；要求评分规则查询权限标签；输入 `unknown` 被拒绝；权重、分值区间和算法尚待业务方确认；业务数据状态固定为未验证。 |
| S04 `calculate_meeting_costs` | `meeting.cost_calculate` / 确定性计算 | `meeting={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；数据源/规则版本未验证。 |
| S04 `generate_meeting_materials` | `meeting.material_generate` / 文件处理 | `meeting={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；生成格式/来源未验证。 |
| S04 `check_meeting_materials` | `meeting.material_check` / 规则校验 | `materials={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；校验规则未验证。 |
| S04 `send_meeting_notification` | `notification.send` / 消息通知 | `draft={{previous_output}}` | 无专用输出 Schema | 能力目录声明外部副作用；Tool 存在时 Preflight 判为 `unsupported`，缺失时仍报告 `missing`；不会执行发送；真实授权、幂等和回执机制不存在。 |
| S05 `transcribe_meeting_audio` | `meeting.audio_transcribe` / OCR/ASR | `asset={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；音频资产授权、ASR 版本和数据源未验证。 |
| S05 必需但无 Tool 步骤 | `meeting.minutes_export` / 文件处理 | 无节点参数 | Task ArtifactConfig/Writer 可承载文件，但 v1 无产物配置 | 当前模板没有声明文件 ArtifactConfig；不能声称已生成会议纪要文件，缺项会被报告。 |
| S06 `recognize_opinion_text` | `opinion.image_ocr` / OCR/ASR | `assets={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；图片资产权限、OCR 能力/置信度未验证。 |
| S06 `classify_opinion_points` | `opinion.classify` / 规则校验 | `opinions={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；分类标签/规则版本未验证。 |
| S06 `export_opinion_summary` | `opinion.export` / 文件处理 | `summary={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；实际导出位置和权限未验证。 |
| S07 `retrieve_report_evidence` | `report.rag_retrieve` / 数据查询 | `request={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；索引、访问控制和来源未验证。 |
| S07 `check_report_citations` | `report.citation_check` / 规则校验 | `draft={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；引用校验规则未验证。 |
| S07 `check_report_consistency` | `report.consistency_check` / 规则校验 | `draft={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；一致性规则未验证。 |
| S07 `check_report_compliance` | `report.compliance_check` / 规则校验 | `draft={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；合规规则/版本未验证。 |
| S07 必需但无 Tool 步骤 | `document.render` / 文件处理 | 无节点参数 | Task ArtifactConfig/Writer 可承载文件，但 v1 无 ArtifactConfig | 不能仅因 Task 有 Artifact Writer 就宣称模板配置了报告产物；缺项会阻止运行。 |
| S08 `extract_archive_metadata` | `archive.document_metadata` / 文件处理 | `assets={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；真实档案元数据接口未验证。 |
| S08 `check_archive_completeness` | `archive.completeness_check` / 规则校验 | `manifest={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；规则版本未验证。 |
| S08 `write_archive_system` | `archive.write` / 业务系统写入 | `manifest={{previous_output}}` | 无专用输出 Schema | 能力目录声明外部副作用；Tool 存在时 Preflight 判为 `unsupported`，缺失时报告 `missing`；写权限、授权、幂等和回执未实现。 |
| S09 `recognize_invoice_fields` | `expense.invoice_ocr` / OCR/ASR | `assets={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；发票资产授权/OCR/验真源未验证。 |
| S09 `verify_and_deduplicate_invoices` | `expense.invoice_verify_dedupe` / 规则校验 | `invoices={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；验真、去重规则及证据未验证。 |
| S09 `check_expense_rules` | `expense.rule_check` / 规则校验 | `invoices={{previous_output}}` | 无专用输出 Schema | 实际 Tool 运行时绑定；财务规则版本未验证。 |
| S09 `submit_expense_to_erp` | `expense.erp_submit` / 业务系统写入 | `expense_draft={{previous_output}}` | 无专用输出 Schema | 能力目录声明外部副作用；Tool 存在时 Preflight 判为 `unsupported`，缺失时报告 `missing`；ERP 授权、幂等和财务回执未实现。 |

### 5.1 矩阵解读

- 当前没有硬编码的生产 Tool 名。每个 Tool 步骤实际使用哪个名称，取决于服务端别名配置和当前会话 Tool 目录；本地模板只包含稳定逻辑能力键。
- **没有真实业务数据源可验证**。生产 `AgentScopeTaskExecutor.business_data_verified()` 对已登记业务能力返回 `false`；会话里即使存在同名 Tool，也不能单独证明它连到真实、授权且被业务接受的数据源。
- 非 S03 能力目前只有分类、副作用目录和模板参数占位，没有逐能力输入/输出 JSON Schema。Preflight 可检查实际 Tool Schema 与当前参数的兼容性，但不能替代业务接口契约验收。
- `project.authorization.read`、`document.render`、`meeting.minutes_export` 是必需能力声明但没有对应 Tool 步骤。后两者虽然可由 Task 产物管线实现，历史模板没有 ArtifactConfig，因此不能当作已具备产物能力。模板/编译器会保留缺失状态，不伪造一个虚拟 Tool。
- `notification.send`、`archive.write`、`expense.erp_submit` 由逻辑能力目录明确标记有外部副作用；即使工具目录可见，Preflight 也不会为“检查”而执行它们。

## 6. S03 契约与证据要求

契约版本为 `task.expert-capability.v1`，定义在 `_capability_contracts.py`。三个 Tool 仍是现有 `tool` 类型，不增加 SOP 节点类型。

1. **查询输入/输出**：输入含 `project_profile.project_name/project_brief/professional_fields`，可带排除规则；`candidate_limit` 限定 1–20，可选地区/机构过滤。输出必须包含合同版本、系统/数据集/数据版本/查询时间、查询上下文以及候选 ID、授权专业字段和字段级证据引用。
2. **回避输入/输出**：输入接收查询来源和候选证据；输出对每个候选明确 `clear`、`conflict`、`unknown`，记录规则版本、检查时间和证据引用。`unknown` 不可通过 Schema/业务校验；回避服务不可用或证据缺少都会让节点失败。
3. **评分输入/输出**：输入携带原查询来源、回避规则版本/检查时间和逐候选回避状态；`unknown` 输入会被拒绝。输出包含候选 ID、排名、分数、分项、评分算法版本、评分时间和证据引用；输出候选的回避状态只接受 `clear`。
4. **业务未定项**：评分权重、分值上界、分项定义和候选过滤算法不由模型临时决定。当前 Schema 明确提醒分值区间和权重需业务确认，但尚未固定业务规则；本阶段真实能力始终 `unverified`，不允许据 fixture 分数产生生产推荐。
5. **推荐理由**：真实理由只能引用返回的候选资格、回避状态、评分分项及证据引用。模型自然语言节点不能替代查询、回避或评分 Tool。真实服务接入前整个 S03 运行会因业务数据未验证被挡住；普通 Input 复核也不会显示成审批通过。

## 7. Preflight、错误和运行时记录

- 现有 `POST /tasks/{task_id}/preflight` 以 Task 所属用户读取保存快照；路由本身未变。后端逐 Tool 节点检查当前会话 Tool 是否唯一存在、JSON Schema 是否兼容、已解析参数是否合规、权限状态、业务源验证状态及副作用。必需能力没有 Tool/Task 产物对应实现时生成单独的缺失项。
- 状态分开报告：`tool_available`、`interface_compatible`、`permission_status`、`business_data_verified`、`external_side_effect` 和总 `status/message`。工具目录错误为 `unverified`，不会误报成“没有 Tool”；重复注册为“Tool 存在但绑定不唯一/接口不兼容”；无权限、需交互确认、未解析参数、数据源未验证以及外部副作用各自阻止运行。
- 前端先保存当前 Task，再请求 Preflight；若 `ready=false`，停止并展示服务端各项检查结果，不请求创建 Run。`create_run()` 后端仍重新读取会话工具和权限并复核 Task revision，因此 UI 预检不能替代服务端门禁。
- Preflight 不调用 Planner 或 Tool。真实执行中，Tool 参数由现有 JSON Schema 约束；S03 输入契约失败会在调用 Tool 前失败。Tool 抛错、输出 JSON 无效或输出 Schema 不符均会进入现有 SOP/Task 节点失败记录；S03 失败写明具体 Schema 问题。Task 现有节点重试策略仍交由官方 SOPEngine 执行。
- 当前没有 Task 专属 Tool 超时字段/超时分类契约；下游 Toolkit/MCP 的超时异常按失败传播并留在节点记录，具体取消/超时保证需由后续真实连接器验收。

## 8. 测试与工程验证

### 已执行

| 验证 | 结果 |
|---|---|
| `python -m pytest tests/task_capability_contract_test.py -q` | 16 passed。覆盖能力分类、S03 Schema/unknown、只读 PermissionEngine 检查、拒绝/待运行时复核、Schema 不兼容、重复 Tool、目录故障、副作用阻断、工具输出失败落盘、完整历史证据传给后续 Agent、用户 Task/Tool 结果隔离。 |
| 全部可收集 Task 专项测试（`tests/task_*_test.py`，暂排除下列因环境缺包不能收集的 `task_input_protocol_test.py`） | 110 passed、2 skipped、2 subtests passed。两个 PostgreSQL 专项用例因未配置 PostgreSQL 环境而 skipped；不代表真实数据库集成验证通过。 |
| `python -m pytest tests/task_input_protocol_test.py -q` | 收集失败：环境缺少 `fakeredis`（`ModuleNotFoundError`）。未添加依赖；该文件的测试未执行。 |
| 前端 `pnpm build`（`examples/web_ui/frontend`） | PASS，TypeScript 与 Vite 构建退出码 0；Vite 报告有若干 chunk 大于 500 kB。未修改构建配置。 |
| `git diff --check` | PASS；最终提交前再次执行。 |

### 安全边界

- Preflight 通过实际 AgentScope `PermissionEngine` 做允许/拒绝断言，确认没有调用 Tool。
- 缺失、Schema 不兼容、权限拒绝、Ask/确认、工具目录不可用以及已标记副作用均阻止 Run；运行时仍有权限重验。
- 新增用户隔离测试确认他人无法读取 Task 和包含结构化 Tool 结果的 Run，也无法借预检探测该 Task 的工具目录。
- 受控 Tool、专家数据、业务规则和评分均是测试 fixture。测试通过只证明 Task 接口行为和 fail-closed 逻辑，不表示真实业务接入。

### 未验证环境

- 本次没有运行已认证的 AgentScope Workspace 服务会话，未验证真实 MCP 连接、生产数据权限、Workspace Artifact 写入或授权 HTTP 下载；不将本地 fixture 结果称作 Workspace 集成通过。
- 未进行真实 S03 专家目录/回避/评分服务端到端验证，也未验证通知、档案系统或 ERP 等外部副作用。
- PostgreSQL 测试被跳过；`task_input_protocol_test.py` 因缺少 `fakeredis` 未收集。

## 9. 模板兼容与历史文件

- 九份 `s01`–`s09` v1 JSON 未改动；`s03_expert_matching.v1.json` 内容及八节点结构保持不变，没有发布 S03 v2。
- 新增字段可选；模板副本的 `capability_key` 和 S03 输出 Schema 从服务端原始模板快照派生。旧保存副本没有新增字段时继续可解析，不需要迁移。
- 新增契约不改变 `agent/tool/python/input` 节点类型、节点顺序、节点数上限、模板复制编辑策略、自由任务 Planner 路径或 P6-B-2 Artifact Writer。
- 九模板“可读取/可展示”回归通过；“全部业务能力可执行”不通过且不宣称通过：必要 Tool/数据源缺失、未验证的场景必须被阻断。

## 10. 白名单核对

本阶段提交候选文件仅位于以下允许路径：

- `examples/agent_service/task/**`
- `examples/web_ui/frontend/src/api/task.ts`
- `examples/web_ui/frontend/src/pages/task/**`
- `tests/task_*`

没有触碰白名单外文件。工作区仍含上述第 2 节列出的预存无关修改和未跟踪产物；它们不属于本阶段，不会暂存。提交时应逐个 `git add -- <白名单路径>`，不使用 `git add .`。

## 11. P6-B-3B 接入所需业务资料

1. **专家目录**：正式 API/MCP 注册名、版本、授权范围、字段字典、分页/候选上限、来源版本/查询时间语义、脱敏规则、错误码和超时/限流规则；返回每项推荐字段可审计的证据引用。
2. **回避服务**：利益冲突对象与规则定义、规则版本发布方式、`clear/conflict/unknown` 判定语义、证据保留策略，以及服务不可用/数据不完整时的 fail-closed 协议。
3. **评分算法**：业务确认的维度、权重、归一化与分值范围、并列处理/候选过滤、算法版本和可复算证据；提供经业务验收的正负样例。
4. **接口与权限**：真实会话中的只读 Tool/MCP 名称和 JSON Schema；与当前用户/Task 权限上下文对应的授权规则；系统/数据集标识和测试环境。三种服务均需被业务方确认数据来源真实且授权适当。
5. **运行保证**：连接器超时、重试/幂等、限流、取消、日志审计及故障传播约定。若后续实现正式审批，还需由业务方和身份/权限模块负责人确认审批人身份、授权依据、所有者能否自批、并发决定原子性、审计和待审批恢复规则；当前 HumanApproval 为 BLOCKED。

## 12. 最终状态

| 项目 | 状态 | 依据 |
|---|---|---|
| 能力契约 | **PASS** | 九场景逻辑键分类及 S03 三项版本化输入/输出 Schema 已建立并有负例测试。 |
| 运行前校验 | **PASS** | 现有 Task Preflight 分项检查 Tool、接口、权限、数据源和副作用；不运行 Tool；服务端创建 Run 前再次检查。 |
| 九场景回归 | **PASS（读取/目录/专项自动化）** | 九个历史模板与 Task 专项回归通过；生产能力仍因真实 Tool/业务源缺失而不可用。 |
| 真实业务 Tool 接入 | **BLOCKED** | 没有经业务验收的专家查询、回避规则和评分接口；没有生产数据源验证。 |
| 正式 HumanApproval | **BLOCKED** | 身份授权、审计和正式等待/恢复协议未实施，本阶段明确不开发。 |

阶段停止于 P6-B-3A，不自动开始 P6-B-3B 或 HumanApproval。
