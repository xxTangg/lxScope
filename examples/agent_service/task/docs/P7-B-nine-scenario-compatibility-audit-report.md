# P7-B：九场景演示数据与 Task 执行协议兼容性审查报告

审查日期：2026-10-12

分支：`integrate/dev_l`
工作区 HEAD：`1418f2b`（未提交；本阶段没有提交或推送）

## 1. 结论

- **静态协议兼容：PASS。** 九份 DEMO 输入均按当前 Registry 版本及输入 Schema 校验；所有场景 6–8 节点，当前文件产物为 Markdown、DOCX、XLSX 三种既有格式。
- **S02 修复：实现完成，真实浏览器复验 BLOCKED/待用户执行。** 根因是来源标题分类不识别英文 `project-charter` 文件名；项目来源缺失随后被通用澄清模型提升为阻塞项。修复保留授权/原文过滤，并只允许“策划模板已命中、项目简报已提供”时将缺失项目来源记为待核实，不制造证据。
- **S03 修复：实现完成，真实浏览器复验待用户执行。** 运行时 Textarea 的带引号字段被当作字面内容，导致集合交集为空。现解析 JSON 字符串数组/带引号分隔值，并保留 unknown/conflict 排除与已有稳定排序。
- **S04：** 外层演示文件对 `fee_rates_json` 的转义是合法 JSON 表示；读取后值为普通内层 JSON 文本。演示数据可计算 3,560.00 元；粘贴外层转义表示会失败关闭，操作说明现已明确正确粘贴方式。
- **S05–S09：** 已检查 InputStep 字符串与模板类型差异，并用本地 DEMO 样例覆盖现有确定性适配器。S05 现在支持直接粘贴 JSON 议程数组。S07 的文档 ID 占位符不可作为真实授权 ID 使用。
- **真实验收边界：** 用户此前报告 S01、S04 已完成演示运行，故矩阵标记为“用户报告 DEMO PASS”；当前回合未在真实浏览器重新运行，也没有获得 Run ID/产物路径可独立核实。S02、S03 需用户在后端重载后重新运行；S07 需实际授权文档 ID。自动化/离线 DEMO 测试不等同真实 E2E。

本轮没有修改系统模板 JSON、知识库源文件、前端源代码或共享/AgentScope 代码。没有连接生产数据库、ERP、专家库、OCR、ASR、档案系统或其他外部业务服务。

## 2. 现有协议与根因

### S02 项目资料来源与澄清

历史 Run `0db3d74909f44f0183ad0ac407b938ef` 的结果由用户提供，本轮未从线上 TaskStore 重新读取。按源码复核：

1. `TaskKnowledgeGateway.retrieve_project_material_evidence` 仍通过当前用户、Task 已选 KB、文档 ready 状态和服务端文档 ID 检查授权；检索使用三条项目资料意图查询，不拼接完整 brief。每次结果按 `(knowledge_base_id, document_id, chunk_index, content_sha256)` 去重，再按同一文档聚合有序原文片段。
2. `_project_material_source_match` 要求内容包含项目资料特征和事实特征，并且来源标题可识别或文档已被用户明确选中。源文件 `DEMO-SOURCE-001-project-charter.md` 的原文含“项目背景”“建筑面积”“设备台账”“项目预算”“建设周期”等内容，但 `project-charter` 原先不属于中文标题关键字；S02 样例的 `source_material_ids` 为空，因此标题门槛失败。修复加入受限英文项目委托标题模式；内容/事实特征和授权检查保持不变。
3. 聚合器只把同一授权文档的检索 chunk 合并用于分类；每个原始 chunk 仍单独保留来源、索引、摘录和哈希作为证据。定向查询没返回足够 chunk 时仍会返回 `not_found`，不会从本地文件或用户简报补造 RAG 证据。
4. S02 v2 已要求项目简报，且输出契约允许待核实草稿；缺少独立委托资料不等于缺少用户输入。过去通用要求整理器将 `project_commission_material` 变成 blocking 字段，触发重复追问。修复只在历史 Tool 结果同时显示模板匹配成功、项目资料 `not_found`，并且用户已提交 `project_brief` 时加入 S02 草稿指导，并将该项目来源字段降为 non-blocking/unknown。其他 blocking 字段不变；没有 brief 或模板未命中时不应用该规则。来源结果本身仍是 `not_found` 且证据数组为空。

源码位置：`_knowledge.py` 的 `retrieve_project_material_evidence`；`_s02_project_planning_evidence.py` 的 `_project_material_source_match`；`_agentscope_executor.py` 的 `_resolve_requirements`、`_s02_missing_project_material_guidance`、`_allow_s02_pending_project_material`；S02 v2 输出要求仍允许列待人工复核项。此修复没有修改 S02 v2 模板。

### S03 专业字段零分

- 模板实例化 Schema 中 `professional_fields` 是字符串数组；运行时 InputStep 的 `TaskInputReply.answers` 和 Task 页面 Textarea 均为字符串。适配器原本只识别开头为 `[` 的 JSON 数组，其余内容按分隔符切开，但保留了值两边的 `"`，例如实际的 `"综合能源"` 不等于候选记录里的 `综合能源`，因此 `matched=[]`、分数为 0。
- 现先按 JSON 数组/JSON 数组元素片段解析；普通分隔值仍受支持；候选和所需专业字段都会去掉一层配对的 ASCII/中文引号，空项和未配对引号明确失败。没有硬编码演示专业名称或分数。
- 得分仍按 `重合专业领域数 / 所需专业领域数 × 100` 计算；同分顺序继续按 `expert_id` 升序稳定排序；`unknown`/`conflict` 仍不进入初排。

### S04 JSON 转义

`fee_rates_json` 在 `s04-meeting-preparation.v3.json` 外层 JSON 中是字符串，所以内层双引号显示为 `\"`；通过 JSON 解析读取字段后，实际值是 `{"rule_id": ...}`，无需再 `json.loads` 一次外层字符串。直接把样例文件中包住字段值的外层引号及反斜杠复制到运行 Textarea，会得到 JSON 字符串而非对象，`_json_object` 会拒绝。代码无须放宽；测试验证原值合计为 CNY 3,560.00，额外外层转义值失败关闭。

### S05–S09 和运行时类型边界

- 模板 API 实例化参数按 Schema 接受真正的 JSON array/integer；模板中 `*_json` 字段本身是字符串，其中保存一段 JSON 文本。
- 运行时 InputStep 回答全部是字符串。S06/S08/S09 JSON 字段由 Task 侧 JSON parser 检查重复键、输入长度及对象/数组类型。S05 的 `agenda` 模板类型为数组，但运行时之前只用逗号/换行切分，直接粘贴 JSON 数组会把方括号/引号当内容；现支持严格 JSON 字符串数组，保留原分隔文本格式。
- S07 `source_material_ids` 的输入 Schema 只能验证“非空字符串数组”，不能证明 ID 存在或有权限。实际权限通过 Task Knowledge Gateway 的当前用户、已选 KB、文档状态检查；未知占位符应返回 `source_material_unavailable`。报告引用的语义充分性仍非自动证明。
- ArtifactConfig 由模板 Compiler 编译到最终节点；运行实例化不提前写文件。最终文件继续由已有 Artifact Writer 在执行阶段生成。模板格式见下表；本轮没有修改 Writer。

## 3. S01–S09 执行协议兼容性矩阵

表中 `*` 表示模板实例化 Schema 必填。运行期间的输入仍由 InputStep 再收集；现有表单以普通文本字符串提交。Agent 收到此前执行历史/结果。ToolStep 均由 TaskService 现有 Task 原生适配器或 Task Knowledge Gateway 执行，不代表接入相应外部业务系统。

| 场景 / 当前版本 | 节点数 / 输入 Schema 字段 | InputStep 文本格式 | ToolStep 能力 / 结果 | Agent 使用的上游 | Preflight / RAG 边界 | 最终产物 |
|---|---|---|---|---|---|---|
| S01 项目立项 v2.0.0 | 7；`project_name:string*`、`project_brief:string*`、`industry:string`、`scope:string` | 名称/简报为文本，行业/范围可选 | `project.similar_search`、`project.rule_check`；来源分别标为 `project_material`、`project_rule` | 项目画像、相似项目检索、规则候选 | 检查选定 KB/授权/索引范围；运行时检索真实来源。一般项目资料命中不等于规则适用或合规结论 | Markdown |
| S02 项目策划 v2.0.0 | 7；`project_name:string*`、`project_brief:string*`、`professional_fields:string[]`、`source_material_ids:string[]` | 专业领域/来源 ID 用 JSON 数组文本；来源 ID 可为空，前提是授权 KB 检索范围仍明确 | `planning.template_retrieve`、`document.citation_verify`；另需 Artifact `document.render` | 授权策划模板、独立项目资料快照、前序大纲/初稿/引用校验及人工复核 | 预检实际校验模板来源、八章、B-01–B-15 和来源字段；项目资料是运行时单独检索，不以模板预检代替。没命中但 brief 已提供时允许待核实草稿 | DOCX |
| S03 专家匹配 v3.0.0 | 8；`project_name:string*`、`project_brief:string*`、`professional_fields:string[]*`、`exclusion_rules:string`、`required_expert_count:integer`、`expert_records_json:string*` | 专业字段用 JSON 字符串数组或兼容分隔文本；候选字段用 JSON 对象数组；人数用数字 | `expert.user_data_lookup`、`expert.user_conflict_check`、`expert.user_rank`；基于本次输入做字段匹配和声明状态过滤 | 用户输入项目画像、候选记录、回避声明、确定性排序结果 | Task 本地结构和业务数据检查在执行时完成；不连接专家库，不代表正式回避校验 | Markdown |
| S04 会前准备 v3.0.0 | 7；`meeting_topic:string*`、`meeting_date:string*`、`location:string`、`participant_count:integer*`、`expert_fee_rules_id:string`、`fee_rates_json:string*` | 人数数字文本；费率为未再套外层引号的 JSON 对象字符串 | `meeting.cost_calculate`、`meeting.material_generate`、`meeting.material_check`；费率由用户提供 | 会议信息、Decimal 费用、材料草稿、完整性检查 | 不依赖 RAG/外部费率 Tool；输入规则正确才可执行，预检不证明费率权威 | DOCX |
| S05 会议纪要 v3.0.0 | 6；`meeting_id:string*`、`language:string`、`agenda:string[]`、`transcript_text:string*` | 转写文本原文；agenda 可 JSON 数组文本或逐行/分隔文本 | `meeting.transcript_import`；`transcription_performed=false` | 用户转写稿、议题/行动整理、用户复核意见 | 不调用或伪造 ASR；运行时检查必需 Meeting ID 与转写文本 | DOCX |
| S06 意见整理 v3.0.0 | 6；`project_name:string*`、`classification_dimensions:string[]`、`opinion_records_json:string*`、`classification_rules_json:string*` | 维度为 JSON 字符串数组；意见与规则为各自 JSON 数组，保留 ID/来源 | `opinion.text_import`、`opinion.classify`；按用户规则归类、去重 | 已识别文本、分类/去重结果、复核意见 | 不调用或伪造 OCR；JSON、重复键、规则和意见结构在执行时校验 | XLSX |
| S07 报告生成 v3.0.0 | 6；`project_name:string*`、`source_material_ids:string[]*`、`required_chapters:string[]*`、`applicable_rules:string[]` | 所有数组填 JSON 数组文本；资料 ID 必须是服务端真实返回的当前用户授权 ID | `report.rag_retrieve`、`report.citation_check`；另需 `document.render` | 授权原文快照、Claim–Evidence、确定性来源校验、人工复核 | 预检校验所选授权 KB/文档 ID/ready 状态；实际检索仍在运行时发生。占位符不可用；哈希/引用/锚点验证不代表语义充分性 | DOCX |
| S08 归档移交 v3.0.0 | 6；`project_id:string*`、`archive_category:string*`、`retention_rule_id:string`、`archive_metadata_json:string*`、`required_metadata_fields_json:string*` | 文件元数据 JSON 对象数组；必需字段 JSON 字符串数组 | `archive.document_metadata`、`archive.completeness_check` | 用户提交的元数据、缺失字段及交接清单 | 不读文件内容、不写档案系统；本地校验字段与路径安全 | XLSX |
| S09 报销结算 v3.0.0 | 6；`project_id:string*`、`expense_category:string*`、`accounting_period:string`、`invoice_records_json:string*`、`expense_rules_json:string*` | 票据 JSON 对象数组、费用规则 JSON 对象；Decimal 金额字符串 | `expense.invoice_import`、`expense.invoice_verify_dedupe`、`expense.rule_check` | 结构化票据信息、输入内去重、演示规则校验 | 不验真、不审批、不调用 ERP；`amount + tax = total`、重复和规则限制在 Task 内校验 | XLSX |

### 各类 Preflight 的实际覆盖

- 模板实例化：`TaskTemplateCompiler.validate_inputs` 按模板 JSON Schema 验证输入，Compiler 检查 ToolStep 能力映射和节点配置；不执行 Tool 或生成产物。
- 通用 Task Preflight：检查节点计划、最多八节点及能力/Artifact 配置。RAG 相关检查使用当前用户的知识库范围；Artifact 使用既有 Writer/Workspace 检查。
- S02 `planning.template_retrieve` 的 Preflight 与执行调用同一策划模板检索/相关性校验；S02 项目资料检索不是该 Preflight 的替代品，也不保证项目资料命中。
- S01 的普通授权资料可检索，不等于 `project.rule_check` 一定会找到规则；规则适用性仍未自动核验。
- S07 已有 ID 在 Preflight 可进行授权、Ready 状态检查；有权限/索引不等于运行时一定检索到相关内容，也不等于 Agent 结论语义正确。
- S03–S06、S08–S09 的 Task 本地确定性节点不是外部业务系统接入凭证；真实业务能力边界继续由结果字段明确标记。

## 4. 集中修复文件

本阶段实际新增/修改：

- `examples/agent_service/task/_s02_project_planning_evidence.py`：增加受限英文项目委托标题匹配；不修改授权、内容特征或事实特征要求。
- `examples/agent_service/task/_knowledge.py`：项目资料 `not_found` 时携带草稿/待核实指导；没有新增证据或改变失败状态。
- `examples/agent_service/task/_agentscope_executor.py`：基于已命中的 S02 模板、项目资料真实 `not_found` 和已提供简报提供限定指导；只放开对应来源字段阻塞，保留其他 blocking。
- `examples/agent_service/task/_scenario_runtime.py`：安全规范化所需/候选专家字段的配对引号；支持 S05 agenda 的 JSON 字符串数组文本；保留 JSON 错误失败关闭。
- `tests/task_project_planning_evidence_chain_test.py`：用工作区真实 DEMO charter 文本构造跨 chunk 检索用例；验证来源不匹配/权限失败、S02 待核实指导与其他阻塞保留。
- `tests/task_p7b_scenario_runtime_test.py`：覆盖真实 S03 样例分数 50/50、unknown 排除、稳定排序与坏引号拒绝。
- `tests/task_p7b_demo_data_compatibility_test.py`：新增九份输入按当前模板 Schema 校验及 S04–S09 DEMO 数据运行测试。
- `examples/agent_service/task/docs/p7b-demo-data/README.md`：澄清历史 401 仅为早期快照，并指向更新操作说明。
- `examples/agent_service/task/docs/P7-B-nine-scenarios-operation-guide.md`：新增逐场景字段、JSON 粘贴方式、人工复核、预期产物和 S02/S03/S07 手动复验说明。
- `examples/agent_service/task/docs/P7-B-nine-scenario-compatibility-audit-report.md`：本报告。

上一阶段已经存在的 `DEMO-SOURCE-002-planning-template.md` 八章/B-01–B-15 内容修复及其测试在本轮保留、未覆盖；本轮未改 `knowledge-sources/`、九个系统模板 JSON、前端源代码或销售台文件。

## 5. 自动化验证与真实环境状态

| 验证项 | 结果 |
|---|---|
| 定向回归：S02 证据链、P7-B 运行时、演示数据协议 | **PASS**：`pytest tests/task_project_planning_evidence_chain_test.py tests/task_p7b_scenario_runtime_test.py tests/task_p7b_demo_data_compatibility_test.py -q`：32 passed，9 subtests passed。 |
| Task 全量 pytest | **PASS**：PowerShell 通过 `Get-ChildItem -LiteralPath tests -Filter 'task_*.py'` 收集 18 个 Task 专项文件，再运行 `pytest <18 个 Task 文件> -q`：**193 passed、2 skipped、11 subtests passed**。两项跳过为 PostgreSQL 专项未配置一次性 Task 测试库；没有连接真实或生产数据库。 |
| Task/Python 文件编译 | **PASS**：`python -m compileall -q examples/agent_service/task <全部 tests/task_*.py>`，退出码 0。 |
| Task 范围 ESLint | **PASS**：`pnpm exec eslint src/pages/task src/api/task.ts`，退出码 0；本阶段没有前端源码改动。 |
| 前端构建 | **PASS**：`pnpm build` 完成；Vite 报告既有大 chunk (>500 kB) 警告，构建成功。 |
| `git diff --check` | **PASS**：退出码 0；Git 输出为既有行尾转换提示，无空白错误。新未跟踪文件另在最终检查中验证。 |
| 浏览器操作 / 当前用户登录 / 当前授权 KB 的真实 RAG / S02 实际重跑 | **NOT TESTED / 等待用户复验**；没有伪造 E2E。 |
| S03 专家候选、费率、OCR/ASR/档案/ERP 等外部业务 | **DEMO PASS 或 BLOCKED**，见下方矩阵；未调用真实外部系统。 |

### S01–S09 当前状态矩阵

| 场景 | 当前状态 | 证据与剩余条件 |
|---|---|---|
| S01 | **DEMO PASS（用户报告）** | 用户已报告知识卡生成及来源引用；本轮未重查 Run ID/来源元数据；正式业务事实核验仍不在 Task 自动能力内。 |
| S02 | **BLOCKED（待真实重跑）** | 用户报告的历史 Run 有项目资料 `not_found` 并重复追问；本轮源码与回归已修复，尚需后端重载并在真实授权 KB/会话下新开 Run 检查引用台账和 DOCX。 |
| S03 | **BLOCKED（待浏览器复验）** | 用户报告真实演示曾运行但甲乙为 0 分；离线真实样例现输出甲乙 50 分、丙 unknown 排除；需用户重跑确认 TaskRun/最终文件。正式专家库和正式回避仍缺失。 |
| S04 | **DEMO PASS（用户报告）** | 用户报告 CNY 3,560、人工意见、DOCX、Workspace 与下载成功；本轮没有 Run ID/文件路径可独立复验。权威费率与真实通知仍未接入。 |
| S05 | **DEMO PASS（确定性输入适配测试）** | 演示转写文本、JSON agenda 解析测试通过；未运行浏览器 TaskRun，不接 ASR。 |
| S06 | **DEMO PASS（确定性输入适配测试）** | 样例 4 条意见运行，合并 1 组重复；未运行 OCR/浏览器 TaskRun。 |
| S07 | **BLOCKED（需真实授权文档 ID）** | 当前样例 ID 是明确占位符，测试确认未知 ID 不会通过资料授权范围；未完成真实 KB/Claim–Evidence/DOCX 验收。 |
| S08 | **DEMO PASS（确定性输入适配测试）** | 三条样例元数据完整性测试通过；没有读取文件或档案系统写入。 |
| S09 | **DEMO PASS（确定性输入适配测试）** | 样例 Decimal 总额 CNY 2,120.00、规则无违规；没有验真、审批或 ERP。 |

所有 DEMO PASS 均不等同正式业务 PASS。用户本轮要求的是为真实登录浏览器复验消除协议问题；复验前不进入九场景正式验收。

备注：PowerShell 下 `pytest tests/task_*.py` 不会按预期把通配符展开；第一次调用因此启动仓库级测试发现，并在三个无关测试模块因缺少 `boto3`、`mem0`、`textual` 而停止收集。随后显式收集 Task 专项文件并完成上述全量结果；仓库级非 Task 测试不在本阶段范围内。

## 6. 需要重新上传、重建或手动配置的事项

- **本轮不需要重新上传知识库文件、删除索引或重新解析。** 本轮只改 Task 侧分类/澄清/输入解析代码，没有修改知识库原文。若授权演示 KB 已有当前版本文件且状态 ready，继续使用即可；若用户此前未上传或索引未完成，则按正常授权流程处理。
- **需要重载后端 Task 服务**以加载 `_knowledge.py`、`_s02_project_planning_evidence.py`、`_agentscope_executor.py` 和 `_scenario_runtime.py` 的代码。前端源代码未改，无需为本轮变化另行重建前端。
- **S02** 不需创建新模板版本或重建模板定义；建议在现有 S02 v2 Task 副本上新建一个 TaskRun，确保失败历史 Run 仍保留且不被当作成功。若现有 UI 无法对失败 Task 启动新 Run，再创建新的 v2 Task 副本即可。
- **S03** 新建一个 S03 v3 Task 或新 Run，按操作说明填入 raw JSON/专业领域，核对甲乙 50 分和 unknown 排除。
- **S07** 必须从当前用户可访问的已索引演示 KB 复制真实服务端 `document_id`，替换样例占位符；同时在 Task 中选择该用户授权 KB。绝不提交占位符或其他用户文档 ID。

## 7. 修改边界及停止条件

本轮修改仅限 `examples/agent_service/task/**`、`tests/task_*` 和 Task docs/演示数据说明。**未触碰** `src/agentscope/**`、Task 外后端模块、共享认证/知识库核心、共享 API、`main.py`、前端源代码、销售台、数据库迁移、依赖或构建配置。工作区原有销售台和其他未提交改动保留；未执行 `git add`、`git commit` 或 `git push`。

完成更新报告后停止。下一步由用户在真实登录浏览器依操作说明复验 S02、S03、S07；本阶段没有自动进入 P7-B 九场景正式验收。
