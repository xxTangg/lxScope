# P7-B：Task 九场景实际执行能力补齐报告

## 结论

Task 内可独立执行的检索适配、结构化输入处理和确定性计算已补齐，并为输入协议或流程发生变化的 S03–S09 新增 v3 模板。S01、S02 继续使用 v2；所有 v1/v2 文件保持原样。当前模板最多 8 个节点，模板实例化仍走既有 TaskService/SOP 流程，不调用 AI Planner。

本阶段的 PASS/DEMO PASS 仅代表 Task 代码和合成隔离测试结果。没有有效登录会话、真实知识库联调、生产业务 Tool/MCP 或真实用户 Workspace 记录，因此**九个场景均未达到正式业务执行验收**。本阶段没有运行浏览器或 Docker 检查。

## 能力和模板变更

新增的 Task 内适配集中在 `_scenario_runtime.py`，通过 TaskService 的现有 ToolStep 执行路径分派。RAG 继续调用 TaskKnowledgeGateway，检索范围使用当前运行用户、Task 已选择的知识库和资料 ID；检索原文快照沿用现有 Evidence 规范化及 TaskRun 持久化路径。没有新增 RAG、SOP、TaskStore 或 Artifact Writer。

确定性处理仅使用本次运行明确提交的数据。JSON 输入有重复键拒绝和大小边界；费用和票据金额使用 Decimal；缺少必要规则、输入格式错误、资料未命中或知识库权限错误时失败关闭。Task 模板文件输出继续使用现有 ArtifactConfig、Renderer 和 Workspace Artifact Writer。

当前目录版本如下：

| 场景 | 当前版本 / 节点数 | 可执行的 Task 能力 | 文件产物 | 业务验收状态 |
|---|---:|---|---|---|
| S01 项目立项 | v2 / 7 | `project.similar_search`、`project.rule_check` 走授权 Task RAG，保留实际来源快照；规则命中仅是候选来源，不作合规结论 | Markdown | **DEMO PASS**（适配逻辑）；真实知识库和模型 **BLOCKED** |
| S02 项目策划 | v2 / 7 | 保留既有定向模板/项目资料检索、Claim–Evidence、最终整理复核与来源台账；本阶段未改模板或扩展执行链 | DOCX | **BLOCKED**（没有本阶段真实 Run 记录；复用 P6-D-2G 证据链） |
| S03 专家智能匹配 | v3 / 8 | 处理用户提交的结构化候选记录；按专业字段重合度排序；仅 `clear` 声明进入初排，`unknown/conflict` 排除；支持 Top N | Markdown | **DEMO PASS**；正式专家库、正式回避核验和审批 **BLOCKED** |
| S04 会前准备 | v3 / 7 | 根据用户提供的费率用 Decimal 核算；生成材料/议程草稿并检查必填项；不发送通知 | DOCX | **DEMO PASS**；权威费率、审批、通知发送和回执 **BLOCKED** |
| S05 会议纪要 | v3 / 6 | 导入用户已转写文本，保留原文状态；后续 Agent 可整理摘要和待办；InputStep 只收集复核意见 | DOCX | **DEMO PASS**（转写文本导入）；ASR 和正式签发 **BLOCKED** |
| S06 意见整理 | v3 / 6 | 导入用户已识别文本，按用户确认关键词分类、去重并保留意见 ID/来源引用 | XLSX | **DEMO PASS**（文本输入处理）；OCR/手写识别 **BLOCKED** |
| S07 报告生成 | v3 / 6 | 走授权 Task RAG；使用 report_source 策略进行 Claim–Evidence 来源、引用、哈希和锚点确定性校验；语义充分性仍要求人工复核 | DOCX | **DEMO PASS**（隔离适配与协议测试）；真实知识库、模型和业务语义审核 **BLOCKED** |
| S08 归档移交 | v3 / 6 | 规范化用户提交的元数据清单；按用户明确填写的必需字段做完整性检查；路径名、空规则失败关闭 | XLSX | **DEMO PASS**（元数据清单处理）；真实文件读取授权和档案系统写入 **BLOCKED** |
| S09 报销结算 | v3 / 6 | 导入用户提供的结构化票据字段；核算金额与税额、检查输入内重复、按用户规则检查类别/限额 | XLSX | **DEMO PASS**（草稿规则处理）；OCR、官方票据验真、审批和 ERP **BLOCKED** |

S03–S09 v3 是流程/输入语义变化后的新版本；历史 v1/v2 仍由 Registry 读取。S05 v3 明确使用已转写文本，不再要求音频转写 Tool；S06 v3 不要求图片 OCR；S08/S09 v3 接受明确的用户结构化输入，不宣称已读取外部文件或完成系统写入。每个模板仍不超过 8 个节点。

## 运行边界与真实依赖

- **S01/S02/S07 RAG：**运行时通过当前 Task 用户和所选知识库范围调用现有 TaskKnowledgeGateway；没有有效会话和真实授权知识库时 Preflight/执行不得虚假通过。本阶段只用隔离 Gateway 测试验证用户 ID、选定知识库 ID、来源 ID 传递，不等同真实权限联调。
- **S03：**记录来源标记为 `user_provided_task_input`。专业字段重合分只代表初步排序，不是专家资质、利益冲突或聘任结论。仍缺正式专家目录、回避规则服务及 HumanApproval。
- **S04：**费率必须由任务输入提供并带规则标识；输出明示未经权威费率验证。通知仅为草稿，不调用消息服务。
- **S05/S06：**分别处理用户提供的已转写/已识别文本；结果明确标注未执行 ASR/OCR。
- **S08：**只校验用户输入的元数据 JSON，不读取任意本地路径或文件内容；必需字段规则为空时不判定完整。尚无文件资产读取授权与档案系统写入/回执接口。
- **S09：**不接受不一致的“金额 + 税额 = 含税金额”；费用规则至少要声明类别或金额限制。重复检查仅限本次输入集合，不代表税务真伪检查。
- **所有场景：**模型生成仍依赖可用 Agent/Session 和模型配置；本阶段未连接真实会话。Artifact Writer 的实际 Workspace 授权下载未做真实用户验证。

## 实际修改文件

Task 后端和模板：

- `examples/agent_service/task/_scenario_runtime.py`：Task 内 RAG 适配和 S03–S09 的有限确定性处理。
- `examples/agent_service/task/_service.py`：接入原生能力分派、证据聚合和最终来源路径；修正检索节点失败后检索结果与节点键错配的问题。
- `examples/agent_service/task/_capability_contracts.py`、`_template_compiler.py`、`_template_validation.py`：登记 Task 内适配及编译/校验支持，不要求虚构外部 Tool 绑定。
- `examples/agent_service/task/_template_registry.py`、`_scenario_evidence_policies.py`：注册 S03–S09 v3，并为版本策略提供相应版本条目。
- `examples/agent_service/task/templates/s03_expert_matching.v3.json` 至 `s09_expense_settlement.v3.json`：新增七个版本化模板；没有覆盖 v1/v2。

Task 专项测试：

- 新增 `tests/task_p7b_scenario_runtime_test.py`：覆盖 S03–S09 节点逻辑、S01/S07 RAG 适配、RAG 权限失败、S07 证据协议以及 S04 官方 SOP 状态传递和隔离 Writer 文件字节。
- 更新 `tests/task_nine_scenarios_test.py`、`tests/task_template_version_test.py`、`tests/task_template_test.py`，验证当前版本、旧版本兼容和新模板实例化。
- 更新 `tests/task_artifact_capability_test.py`、`tests/task_capability_contract_test.py`、`tests/task_custom_flow_test.py`、`tests/task_sop_test.py`、`tests/task_template_output_test.py`，使测试分别符合 Task 原生适配、当前版本和 Artifact Writer 预检的既有安全约束。没有放宽服务端 Preflight。

本阶段没有修改 Task 前端，也没有新增/修改任何白名单外的源代码。工作区原先已存在的销售台等白名单外改动和其他未提交文件均保留；没有暂存、提交或推送。

## 验证结果

| 验证项 | 结果 |
|---|---|
| P7-B 专项测试 | **PASS**：`tests/task_p7b_scenario_runtime_test.py`，15 passed |
| Task 全量 pytest | **PASS**：184 passed、2 skipped、2 subtests passed |
| PostgreSQL 专项集成 | **BLOCKED / SKIPPED**：未设置 `LXSCOPE_TASK_TEST_DATABASE_URL`；未连接未知或生产数据库 |
| Python 编译 | **PASS**：`python -m compileall -q examples/agent_service/task tests/task_p7b_scenario_runtime_test.py` |
| Task 前端 ESLint | **PASS**：`pnpm exec eslint src/pages/task src/api/task.ts`；本阶段没有前端源文件改动 |
| 前端构建 | **PASS**：`pnpm build`；构建器输出大 chunk (>500 kB) 警告，但 build 成功 |
| `git diff --check` | **PASS**：无空白错误；Git 仅提示当前工作树行尾将按仓库设置转换 |
| 浏览器/真实登录/生产 RAG/真实 Workspace | **NOT TESTED**：本阶段为不依赖浏览器的开发阶段，未伪造用户或数据 |

对比 P7-A-R 记录的 Task 基线 169 passed、2 skipped，本次为 184 passed、2 skipped；增加的 15 项来自 P7-B 专项覆盖，没有把 PostgreSQL 跳过项计作通过。

## 文件产物与正式业务状态

Task 流程在最终节点成功且现有校验通过后，仍由既有 Artifact Writer 写文件。测试验证了隔离测试目录中的实际生成字节和 S04 SOP 结果传递；这不证明真实用户 Workspace 权限、授权下载或生产知识库已联通。没有将测试夹具当成生产资料，也没有生成“已审批/已签发”结果。

| 场景 | 内部/隔离执行 | 正式业务执行条件 |
|---|---|---|
| S01 | DEMO PASS | BLOCKED：需真实授权知识库、模型会话和业务用户验收 |
| S02 | BLOCKED（本阶段无真实 Run） | BLOCKED：需 S02 真实知识库、模型、Workspace 联调 |
| S03 | DEMO PASS | BLOCKED：需正式专家数据、回避服务及审批协议 |
| S04 | DEMO PASS | BLOCKED：需权威费率来源和获准通知/审批系统 |
| S05 | DEMO PASS | BLOCKED：输入已转写文本；真实 ASR 未接入 |
| S06 | DEMO PASS | BLOCKED：输入已识别文本；真实 OCR 未接入 |
| S07 | DEMO PASS | BLOCKED：需真实授权 RAG、模型和业务人员语义复核 |
| S08 | DEMO PASS | BLOCKED：需文件资产授权读取及档案系统契约 |
| S09 | DEMO PASS | BLOCKED：需 OCR、官方验真、审批及 ERP 幂等接口 |

**阶段结论：Task 内可验证的确定性节点和 RAG 适配已完成；九场景真实业务运行与正式审批不通过本阶段报告宣称完成。**
