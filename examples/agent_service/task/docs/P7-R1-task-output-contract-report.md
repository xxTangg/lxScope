# P7-R1：Task 节点输出契约与可靠性修复报告

## 结论

- **P7-R1 Task 代码与隔离回归：PASS。** Task 全量测试 `222 passed, 2 skipped, 13 subtests passed`。
- **用户提供的 S02 Run 根因定位：PASS。** `fb0aaacf1ed2474a8b7edfaf7b8badd5` 的 29/61/5 诊断已用于脱敏回归。
- **该真实 Run 的来源真实性与语义支持：NOT TESTED。** 最终 Agent 文本内的“passed=true”是模型输出；当前未取得其上游引用核验节点的原始服务端结果和该 Run 的完整授权证据快照。
- **真实浏览器重新运行 S02：NOT TESTED。** 需要部署/重载本次 Task 后端修改后创建新 Run。

本阶段没有提交或推送。现有未提交改动均予以保留。

## Gate 0：真实失败诊断

用户提供的最终节点 `整理策划输出` 诊断包含：结构异常 29 项、引用异常 61 项、待核实关联异常 5 项。内容载荷显示 15 个 Claim、14 个 Binding、5 个待核实记录；同一批 CL-01 至 CL-14 在大纲和初稿中重复出现，正文共有多处 `[C1]`、`[C2]`、`[C3]` 标记，而协议未为这些重复位置声明 occurrence。

| 诊断 | 真实 Run 中的表现 | 根因 |
|---|---|---|
| 结构 29 项 | 15 项锚点缺失/重复或 Claim 文本与所在位置不匹配，另有 14 项 `unregistered_claim_anchor` | 旧协议把逻辑 Claim ID 当作唯一正文位置；大纲与初稿复用 ID 后，一个 ID 对应多个位置。旧校验器又只把通过锚点校验的 Claim 放入有效索引，导致已在 JSON 声明的 Claim 被级联报告为未登记。 |
| 引用 61 项 | 14 项 `binding_not_declared_by_claim`、47 项 `citation_without_claim_anchor` | Binding 中的 citation_id 虽列于原始 Claim，但锚点失败的 Claim 未进入旧有效索引，形成级联绑定错误；重复或未锚定的正文引用也没有稳定位置可供逐项关联。 |
| 待核实 5 项 | UV-01 至 UV-05 均指向 CL-15，报告正文没有可定位的 CL-15 锚点 | 五项独立事项共用一个未锚定的 Claim，旧校验器要求关联有效正文 Claim/位置，因此五项一并失败。 |

服务端给出的 evidence_ledger 诊断中 `source` 类别为空，因此本次最终台账失败的主要诊断是位置和关联协议问题。**这不等于来源真实性已经通过**：Agent 生成正文中的引用结论、来源字段和 `passed=true` 都不是权威核验结果；缺少上游 citation verifier 的原始服务端结果，无法据此确认 C1/C2/C3 的来源快照、哈希和原文摘录。

失效类型分别是：

1. **模型输出不满足协议：** 重复展示的 Claim 没有 occurrence 定位，UV-01 至 UV-05 没有各自稳定位置。
2. **协议兼容问题：** 旧的单锚点 Claim 结构无法表达同一逻辑声明在大纲、正文或表格中的多次展示。
3. **校验器级联诊断缺陷：** 锚点无效的 Claim 未进入旧有效索引，后续把已声明的 Binding 和 Claim 错报为未声明。
4. **来源与语义边界：** 用户提供的最终节点载荷不足以独立验证上游来源原文；即使确定性来源校验通过，证据是否在语义上充分支持项目结论仍需人工复核。

## 输出契约与跨节点传递

新增可选、带版本的 `NodeOutputContract`，涵盖契约 ID/版本、输出类型、Schema、校验模式、来源要求、下游绑定方式和失败策略。自由文本节点不被强制转换为 JSON。模板可声明该契约，编译后保存在既有 TaskNode 中。

Agent 的原始文本仍单独保留在 NodeRun 输出中；结构化结果只有在 Schema 确定性校验通过后才保存为 `result`。只有前序节点标记为 `validated_result` 且服务端记录校验通过时，Task 才向下游提供 `previous_result`。旧文本路径继续可用；没有合法校验结果时，下游不能把文本伪装成结构化结果。

Schema 校验复用 Task 内已有安全校验器，不执行 Python `eval` 或模型提供的校验代码。结构化 Schema 错误最多触发两次 Agent 尝试；最终状态由 Task verifier 决定。

## Claim–Evidence 关联及 S02 适配

通用协议区分逻辑 `claim_id`、正文位置 `occurrence_id`、引用编号及 Evidence ID。重复展示同一 Claim 时，每个实际位置需有独立 occurrence 与独立位置核验；表格行也使用同一位置协议。CitationBinding 可以继续引用同一 Evidence，但每个位置都要通过授权来源身份、哈希、连续原文摘录和引用关系校验。

旧协议仍接受单个位置的 Claim；若同一旧 Claim ID 对应多个位置则失败关闭，避免把重复声明静默合并。校验器现将“已声明 Claim”与“通过位置验证的 Claim”分开索引，减少 `unregistered_claim_anchor`、绑定未声明等级联误报。多个 UnverifiedRecord 可各自保留独立 `unverified_id`，不再按 claim_id 折叠。

S02 专属八章结构、B-01～B-15、项目事实和台账规则仍位于 S02 适配层；通用输出契约不依赖这些业务常量。S02 v1/v2 模板定义和历史 Run 不做格式升级或覆盖。

有限协议规范化只调整**唯一且合法 JSON 区块内部的无信息损失空白/格式**；遇到重复区块、非法 JSON 或歧义时保持原文并失败。不会补写 Claim、Occurrence、Evidence ID、引用原文或来源字段。

## 有限重试与失败状态

- 只对 Claim/Occurrence 锚点、正文位置映射、引用标记位置以及已知的关联级联错误开放一次结构修复机会（最多两次总执行）。
- 重试前保存服务端解析的 Claim ID、显式 Occurrence ID 和 Unverified ID 清单；同时锁定已有 Claim 的类型、文本和引用编号、已有 occurrence 的文本和引用编号，以及待核实事项的原因和所需证据类型。下一次输出必须保留这些身份和内容。删除、丢失或改写原声明/待核实项会立即失败并停止重试；原本使用单锚点协议时可以增加 occurrence 来修复重复位置。无法解析初次清单时不自动重试。
- 仅 Schema 格式错误可按统一契约有限重试。来源快照缺失、来源/权限/哈希错误、原文摘录不匹配、未知诊断、语义支持不足不会通过结构修复重试。来源核验失败不通过放宽规则或删除声明处理。
- `StepVerdict.retryable` 把 Task verifier 的重试判断交给现有 TaskSOPStep adapter；adapter 调整自己的重试上限，并在重新构造后恢复失败终态。没有修改 AgentScope 官方 SOPEngine。
- Agent 文本内的 `passed=true`、`issues=[]`、“失败项：无”或“已生成文件”不作为服务端结果。失败节点由服务端前置失败说明覆盖显示；Artifact Writer 仍只在节点通过后执行。失败 Run 不创建成功产物记录。

## 本阶段涉及文件

| 文件 | 职责 |
|---|---|
| `examples/agent_service/task/_models.py` | 可选节点输出契约及显式可重试 verifier 结果。 |
| `examples/agent_service/task/_template_models.py`、`_template_compiler.py` | 模板契约校验、编译与下游结构化结果绑定要求。 |
| `examples/agent_service/task/_output_validation.py`、`_agentscope_executor.py` | 返回经过校验的结构化值，并在 Task 层构造契约提示/typed result 传递。 |
| `examples/agent_service/task/_evidence_contract.py` | Claim/Occurrence/Binding/Unverified 协议、位置和来源验证、清单保护及受限重试分类。 |
| `examples/agent_service/task/_s02_project_planning_evidence.py` | 将 occurrence 和独立待核实记录适配到 S02 台账；保留 S02 特有规则。 |
| `examples/agent_service/task/_service.py` | 执行期校验、授权快照绑定、受限重试、下游传递、最终状态及产物门控。 |
| `examples/agent_service/task/_sop.py` | 将不可重试结论和已耗尽预算可靠投影为终态，不修改官方引擎。 |
| `examples/agent_service/task/_errors.py` | 输出契约错误的 Task 错误码映射。 |
| `tests/task_p7r1_failure_fixture_test.py`、`tests/task_p7r1_output_contract_test.py` | 真实失败形状、Occurrence/UV 清单、不可重试终态、结构化传递和有限重试测试。 |
| `tests/task_project_planning_rag_test.py`、`tests/task_template_output_test.py` | S02 来源失败时不重试、不生成 DOCX；输出校验和 Artifact Writer 失败状态测试。 |

以上是本阶段 Task 代码与测试范围；工作区在开始前已存在其他 Task、前端和销售台未提交内容。销售台与 Task 前端文件本阶段未编辑或覆盖。

## 回归与工程检查

| 命令 | 结果 |
|---|---|
| PowerShell：`$taskTestFiles = rg --files tests \| Where-Object { (Split-Path -Leaf $_) -like 'task_*_test.py' }; .\.venv\Scripts\python.exe -m pytest $taskTestFiles -q` | **PASS** — 222 passed, 2 skipped, 13 subtests passed。两个 PostgreSQL 测试因环境未配置而 skipped；没有将其计为真实数据库集成通过。 |
| 定向 P7-R1 / S02 回归 | **PASS** — 45 passed。 |
| `.\.venv\Scripts\python.exe -m compileall -q examples/agent_service/task tests` | **PASS** |
| `git diff --check` | **PASS**，返回码 0；Git 输出了工作区 LF/CRLF 转换提示。 |
| Task 前端 ESLint / `pnpm build` | **NOT APPLICABLE** — 本阶段没有修改 Task 前端。 |

## 兼容、阻塞与真实验收

- 自由任务和 Planner 路径未替换；模板仍由现有模板编译和 TaskService 执行。
- 普通文本节点仍不需要 Claim/Evidence 协议；Schema 和来源校验只在节点声明相应契约/能力时启用。
- 历史 v1/v2 TaskRun 保留旧格式读取；旧单锚点 Claim 仍可校验。没有迁移历史数据库记录或更改系统模板定义。
- S01–S09 的通用设施可选择启用，业务策略仍按各场景独立提供；不具备授权证据或外部能力时失败关闭。
- 正式审批、外部业务系统写入及证据语义蕴含判断未在本阶段实现。
- 真实 S02 Run 未用本地 Task 服务重新执行；当前没有本次 Run 上游引用核验节点的原始服务端载荷，不能以用户粘贴的 Agent 自述或隔离测试代替真实来源验证。

部署修改后，请重启 Task 后端并创建新的 S02 v2 Run。旧 Run 的节点输出和诊断为历史快照，不会因代码更新自动重算。检查新 Run 的最终节点状态、`claim_evidence_retry_policy`、`evidence_ledger.all_diagnostics`、最终引用核验的服务端 `verification_scope` 及 Artifact 列表。若修复只涉及 occurrence/结构且保留了全部原始 Claim/UV，允许最多一次结构修复；任何来源或原文错误仍应失败并保留其真实原因。
