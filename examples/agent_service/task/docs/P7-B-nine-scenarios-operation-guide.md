# P7-B：九场景演示运行操作说明

更新日期：2026-10-12

适用演示数据：`p7b-demo-data/`
所有项目、人员、专家、票据、费率和来源内容均为 `DEMO/SYNTHETIC`，不得用于生产业务。

## 使用前须知

1. 使用本地开发/演示环境中正常登录的用户。不要复制、伪造或共享其他用户的 Cookie、Token、知识库 ID、文档 ID 或 Workspace。
2. S01、S02、S07 要求选择当前用户有权访问且已完成索引的演示知识库。其余场景使用用户提交的结构化演示数据，不需要知识库。
3. 模板实例化参数按各模板 Schema 提交；可参考 `p7b-demo-data/inputs/` 对应文件中的 `inputs` 对象。实例化只创建 Task 副本，不启动运行。
4. Task 运行时的 `InputStep` 通过普通文本框收集答案，答案统一以字符串传递。数组/对象字段要粘贴**一次序列化后的原始 JSON**，例如 `[{"id":"DEMO-1"}]` 或 `{"rule_id":"DEMO-RULE"}`。不要把外层样例文件中的引号和反斜杠转义也复制进去；`"` 是外层 JSON 文件的转义表示，不是要粘贴的字符。
5. 数值字段在运行文本框中填数字文本，例如 `8`。S05 的议程接受 JSON 字符串数组，也接受逐行或逗号分隔文本。S03 专业字段接受 JSON 数组；也兼容带引号的逗号分隔文本。
6. 若可选复核 InputStep 没有修改意见，在文本框填“暂无补充，保留待人工复核”后继续。不要把“继续”解释为已审批、已签发或已发送。
7. Preflight 通过只说明服务端本次检查到的 Task 能力/权限条件满足，不代表真实项目事实已核实、规则已获批准或外部系统已执行。所有 RAG 失败、权限失败、来源缺失和节点失败均按真实结果处理。

## 九场景输入与执行对照

`*` 表示模板实例化 Schema 必填。运行期间还必须满足各 InputStep 的必填问题。节点数与 Artifact 格式来自当前 Registry；没有场景超过八个节点。

| 场景 / 当前模板 | 实例化输入字段（模板类型） | 运行 InputStep 与文本框格式 | Tool 能力及 Agent 上下游 | KB / 预检 / 预期产物 |
|---|---|---|---|---|
| **S01 项目立项** `s01_project_init` v2.0.0，7 节点 | `project_name:string*`、`project_brief:string*`、`industry:string`、`scope:string` | 项目名称、项目背景与目标必填；行业、范围可选。普通文本即可。 | `project.similar_search` → `project.rule_check`；Agent `extract_project_profile`、`draft_initiation_recommendation`、`produce_initiation_card` 消费用户输入和前序检索结果。 | 选择授权演示 KB；预检检查知识库范围/索引状态，运行时记录真实来源证据。规则命中只是候选，不是合规结论。输出 Markdown 知识卡。 |
| **S02 项目策划** `s02_project_planning` v2.0.0，7 节点 | `project_name:string*`、`project_brief:string*`、`professional_fields:string[]`、`source_material_ids:string[]` | 项目名称、项目背景与目标必填；专业领域填 JSON 数组；资料 ID 可留空，或填当前用户授权文档 ID 的 JSON 数组。 | `planning.template_retrieve`、`document.citation_verify`；Agent `draft_planning_outline`、`draft_planning_document`、`produce_planning_output` 使用模板证据、项目资料检索和前序草稿/校验/复核结果。 | 选择授权 KB。预检必须命中真实策划模板并验证八章、B-01～B-15 与来源字段。项目委托资料在运行时另行定向检索；未命中时，若必填项目输入已提供，可形成待核实草稿，但用户输入不得冒充知识库来源。输出 DOCX 草稿。 |
| **S03 专家智能匹配** `s03_expert_matching` v3.0.0，8 节点 | `project_name:string*`、`project_brief:string*`、`professional_fields:string[]*`、`exclusion_rules:string`、`required_expert_count:integer`、`expert_records_json:string*` | 专业领域推荐填 `["综合能源","设备资产管理"]`；候选记录和回避声明填原始 JSON 数组文本；候选数量填数字。复核意见可填“暂无补充…”。 | `expert.user_data_lookup`、`expert.user_conflict_check`、`expert.user_rank`；Agent `extract_project_profile`、`explain_matching_reasons`、`produce_expert_shortlist` 使用结构化用户候选及确定性排序。 | 不需 KB。演示数据应得到专家甲、乙各 50 分并按 ID 稳定排序；`unknown` 专家丙排除。输出 Markdown 初排清单；不代表专家库查询、正式回避审查或聘任。 |
| **S04 会前准备** `s04_meeting_preparation` v3.0.0，7 节点 | `meeting_topic:string*`、`meeting_date:string*`、`location:string`、`participant_count:integer*`、`expert_fee_rules_id:string`、`fee_rates_json:string*` | 参会人数填数字；`fee_rates_json` 粘贴内层 JSON 对象本身，例如 `{"rule_id":"DEMO-FEE-RULE-2025-01","currency":"CNY","rates":{"venue_fixed":"800.00","catering_per_person":"180.00","materials_per_person":"45.00","transport_per_person":"120.00"}}`。不要添加外层引号。 | `meeting.cost_calculate`、`meeting.material_generate`、`meeting.material_check`；Agent `prepare_notification` 和 `assemble_meeting_preparation_pack` 使用费用计算、会务草稿和检查结果。 | 不需 KB。通过后演示费率合计应为 CNY 3,560.00。人工补充节点可修正材料；通知始终是草稿、不发送。输出 DOCX。 |
| **S05 会议纪要** `s05_meeting_minutes` v3.0.0，6 节点 | `meeting_id:string*`、`language:string`、`agenda:string[]`、`transcript_text:string*` | 填已有转写文本，不上传音频；议程可粘贴 `[...]` JSON 数组，或每行一项。人工复核没有改动时填“暂无补充…”。 | `meeting.transcript_import`；Agent `segment_meeting_topics`、`extract_meeting_actions`、`produce_meeting_minutes` 使用用户文本、已导入记录和复核意见。 | 不需 KB。检查原始文本、议题/待办和复核节点状态；`transcription_performed=false`。输出 DOCX 草稿，不声称执行 ASR。 |
| **S06 意见整理** `s06_opinion_processing` v3.0.0，6 节点 | `project_name:string*`、`classification_dimensions:string[]`、`opinion_records_json:string*`、`classification_rules_json:string*` | 分类维度填 JSON 字符串数组；意见记录与分类规则分别粘贴 JSON 数组文本，保留唯一意见 ID、文本和 `source_reference`。没有复核修改时填“暂无补充…”。 | `opinion.text_import`、`opinion.classify`；Agent `summarize_opinion_dimensions`、`export_opinion_summary` 使用导入、分类、去重结果。 | 不需 KB。预期演示输入 4 条意见、合并 1 组重复文本；检查未分类/多规则冲突是否需要复核。输出 XLSX。Task 不执行 OCR。 |
| **S07 报告生成** `s07_report_generation` v3.0.0，6 节点 | `project_name:string*`、`source_material_ids:string[]*`、`required_chapters:string[]*`、`applicable_rules:string[]` | 所有数组均填原始 JSON 数组文本。`source_material_ids` 必须替换为服务端返回、属于当前用户所选授权 KB 的真实文档 ID；不能粘贴 `__REPLACE_WITH_SERVER_ISSUED_AUTHORIZED_DOCUMENT_ID__`。 | `report.rag_retrieve`、`report.citation_check`；Agent `draft_report_chapters`、`produce_report_draft` 使用授权原文快照、确定性校验和人工复核意见。 | 选择授权 KB 并将确切真实文档 ID 配入实例化参数和运行输入。未知/占位/无权限 ID 应由预检或检索拒绝。检查 Claim–Evidence、引文原文/哈希和未核实项；语义支持仍需人工复核。输出 DOCX 草稿。 |
| **S08 归档移交** `s08_archive_transfer` v3.0.0，6 节点 | `project_id:string*`、`archive_category:string*`、`retention_rule_id:string`、`archive_metadata_json:string*`、`required_metadata_fields_json:string*` | 元数据粘贴 JSON 对象数组；必需字段粘贴 JSON 字符串数组，例如 `["file_name","category","document_date","owner","retention_period","document_number"]`。仅填元数据，不提供任意文件系统路径。 | `archive.document_metadata`、`archive.completeness_check`；Agent `classify_archive_materials`、`prepare_transfer_manifest`、`produce_transfer_manifest` 使用归一化元数据及完整性结果。 | 不需 KB。检查缺失字段与重复文件名。输出 XLSX 交接清单；不读取实际文件、不写档案系统、不生成正式回执。 |
| **S09 报销结算** `s09_expense_settlement` v3.0.0，6 节点 | `project_id:string*`、`expense_category:string*`、`accounting_period:string`、`invoice_records_json:string*`、`expense_rules_json:string*` | 票据粘贴 JSON 对象数组；费用规则粘贴 JSON 对象。金额使用十进制字符串；演示样例 `amount + tax = total`。 | `expense.invoice_import`、`expense.invoice_verify_dedupe`、`expense.rule_check`；Agent `prepare_expense_draft`、`produce_expense_submission_package` 使用核算、输入内重复检查和规则检查结果。 | 不需 KB。演示样例总额应为 CNY 2,120.00、没有样例规则违规。输出 XLSX 草稿；不验真、不审批、不提交 ERP。 |

## 人工补充节点

运行期间如遇 InputStep，填写当前显示的字段并提交，系统继续同一个 TaskRun。可选复核问题也可以表达“暂无补充，保留待人工复核”。若输入缺失或被拒绝，保留实际错误和节点状态，不通过填造来源 ID 或声明 `clear` 消除权限/证据检查。任何标注为 DEMO 的输入与结果只证明演示逻辑可执行。

## 本轮代码修复后的复验安排

本轮没有修改 `knowledge-sources/` 中任何文件，也没有操作知识库服务。若现有授权演示 KB 已上传所需源文件并显示索引完成，**无需重新上传、删除旧索引或重新解析**。本轮后端 Task 代码需要由开发环境重载/重启后才会生效；前端无须因本轮代码重建。

### 优先复验 S02

1. 重载后端 Task 服务，以现有 S02 v2.0.0 Task 副本开始一个**新 TaskRun**；不要把已失败的历史 Run 当成已修复结果。
2. 确认 Task 选中的知识库仍为当前用户授权的演示 KB，且 `planning.template_retrieve` 预检显示命中策划模板及完整八章/B 条款原文。
3. InputStep 输入项目名称、项目简报；如提供 `source_material_ids`，只填服务端返回且当前用户可访问的真实文档 ID。没有项目委托资料命中时继续检查大纲是否可作为草稿生成，项目数值是否标成用户陈述/DEMO 假设/待核实；不得伪造 RAG 证据。
4. 检查 `project_material_retrieval` 的真实状态、授权来源、chunk/hash、引用校验、最终来源台账和 DOCX。若授权来源仍未命中，应保留 `not_found`，但不应仅为该缺失反复阻塞已经具备必填输入的草稿步骤。

### 优先复验 S03

1. 用 S03 v3.0.0 新建演示 Task，专业领域填 ` ["综合能源","设备资产管理"] `（去掉首尾空格亦可）。候选数据可取输入文件中 `expert_records_json` 字段解析后的原始数组文本。
2. 运行到专家排序节点，确认甲、乙匹配字段各一项、得分均为 50，结果顺序稳定；丙 `unknown` 应出现在排除项，不得进入排名。
3. 检查最终 Markdown 明确注明候选来自用户演示输入、未连接正式专家库，也未做正式回避审查。

### 优先复验 S07

1. 在开发/演示知识库页面复制当前登录用户有权访问且索引完成的服务端文档 ID。
2. 在 S07 输入文件副本中，用该 ID 替换占位符；不要修改源模板文件或粘贴到聊天。
3. 选择同一授权知识库，实例化并运行；核对 Preflight、`report.rag_retrieve` 的文档/片段/哈希和证据快照，再核对 `report.citation_check` 对最终报告的确定性检查。
4. 无权、未知或未索引文档应失败关闭。仅凭 Schema 可接受字符串不代表文档真实存在或获授权。

S01 与 S04 的演示运行已由用户报告完成，但本说明不伪造 Run ID 或产物路径；若需归档验收证据，请在真实 Task 页面记录各自 TaskRun ID、节点状态、S04 DOCX 下载文件和 Workspace 产物元数据。S05–S09 的确定性适配器样例测试不等同浏览器完整运行。
