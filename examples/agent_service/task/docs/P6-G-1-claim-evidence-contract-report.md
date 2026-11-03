# P6-G-1：Task 通用 Claim–Evidence 协议与证据台账修复报告

## 结论

已在 Task 模块内加入向后兼容的结构化 Claim–Evidence 协议，并把 S02 的场景规则保留在 S02 适配器中。TaskRun 证据快照继续作为唯一可信来源；没有新增数据库字段或迁移，也没有修改 AgentScope 官方代码、SOPEngine、知识库核心或 Task 外业务模块。

Task 全量专项回归为 **162 passed、2 skipped、2 subtests passed**。两个跳过项是 PostgreSQL Task 测试没有配置 disposable PostgreSQL URL。当前没有可读取的已认证真实失败 Run，因此本报告的复现与验证依据是 Task 专项测试夹具；不将其描述为真实知识库或生产运行验收。

## Gate 0：旧协议失败原因

当前工作区没有提供可访问的真实失败 Run。根据执行代码和已有 S02 测试夹具，问题根因如下：

1. **同一 `[C#]` 在多个正文位置被拒绝**：通用引用校验先把编号转换为集合，本身允许重复标记；S02 旧台账随后用 `Counter` 要求每个编号在全文恰好出现一次，并用以 citation ID 为键的映射拒绝该编号关联不同 claim。因此一个来源被多个独立事实引用时会失败。
2. **旧 `claims[n]` 结构无法表达独立关联**：旧协议严格要求 `section`、`claim`、`claim_type`、`citation_ids` 四个字段，且要求章节和完整 claim 字符串精确出现在正文中。它没有 `claim_id`、稳定锚点、Claim 到 Evidence 的逐项 binding，也没有每条 binding 自己的 quote。新协议输出这些字段时不能通过旧字段校验。
3. **待核实事项靠字符串猜位置**：旧实现要求 section 和 claim 原文完整包含在报告里，并在 claim 文本中搜索“待核实”；后续再按整行检查是否精确包含 claim。多个待核实项、改写后的行文或表格单元格拆分都会造成遗漏或错位。
4. **Markdown 表格没有 claim 级映射**：旧代码按 Markdown 行扫描项目事实关键词和数值，但 claim 对应使用完整行/句字符串包含判断，没有稳定的行或单元格声明锚点。表格字段与状态分列时，台账无法确定哪条 claim 对应哪个事实值。
5. **跨 chunk 的 B 条款会误拒**：策划模板检索器可以把同一授权文档的多个 chunk 合起来检查模板整体；旧最终台账校验却要求单条 citation 的 quote 同时含 B 编号和足够长的条款正文。编号和正文落在相邻 chunk 时会被拒绝。
6. **旧确定性检查的边界**：旧 verifier 检查正文引用编号集合、引用源定位是否存在于本次运行的证据快照、quote 是否为保存原文的子串；它不重算完整未截断文本的 SHA-256，不做 claim 级 quote 关联，也不验证文本在语义上充分支持结论。检索适配器负责按当前用户权限获取来源，但证据记录里没有显式保留该授权主体。

## 新协议

### EvidenceRecord

`_evidence_contract.py` 定义服务端模型，包含：

- 稳定 `evidence_id`、`source_type`、`authorization_subject` 和来源记录 ID。
- 来源定位：知识库 ID、文档 ID、chunk index；通用模型也容纳 Tool/MCP 的 record/call/field locator。
- `content_sha256`、原文摘录、是否截断、采集时间、来源更新时间、来源名称及来源分类。

当前 Task 运行时接入的是用户授权知识库 chunk。`evidence_id` 由 Task 服务端基于来源身份、内容哈希及 TaskRun 所有者确定性生成；它是当前 Run 的引用 ID，不是权限令牌。权限判断仍使用 TaskRun 所有者和其持久化证据快照。模型不能通过提交一个新 ID 扩大来源范围。

### ClaimRecord

```json
{
  "claim_id": "CL-area",
  "kind": "fact",
  "anchor": "CL-area",
  "text": "项目建筑面积为12000平方米。",
  "citation_ids": ["C1"]
}
```

`kind` 区分 `fact`、`rule`、`historical_case`、`analysis`、`recommendation` 和 `unverified`。正文通过 `<!-- task-claim:CL-area -->` 锚定具体段落或表格行；校验不依赖完整 Markdown 行相等。每条表格事实要求独立成行并放置自己的锚点。

### CitationBinding 与 UnverifiedRecord

```json
{
  "claim_id": "CL-area",
  "citation_id": "C1",
  "evidence_id": "EV-…",
  "quote": "建筑面积：12000平方米"
}
```

同一 Evidence 可以支持多个 claim，但每个 claim/citation 组合必须有自己的 binding 和实际原文摘录。重复使用 `[C1]` 时，正文中每个对应 claim 都独立校验。每个 binding 的 quote 都必须属于其 evidence 原文，并通过数值和关键用语的保守字面关联筛查。

待核实记录使用独立 `unverified_id`，指向一个正文中已锚定、类型为 `unverified` 的 claim，并保存原因及预期证据类型。S02 适配器另外要求相应正文明确标为“待核实”。台账的待核实状态由服务端生成，不由模型自报。

## 三层校验边界

1. **结构与引用编号**：严格检查 JSON 字段、数量边界、ID 唯一性、claim 锚点、正文标记与 citation 清单集合。重复出现同一引用编号允许通过，但每个 claim 的标记集合必须与自身声明一致。
2. **来源身份与摘录关联**：citation 必须解析到当前 TaskRun 的证据记录；服务端校验授权主体、来源状态、Evidence ID、哈希格式及 quote 原文子串。保存了完整且明确未截断的原文时，会重新计算 SHA-256 并比对。当前知识库适配器对超长 chunk 保存完整文本哈希和最多 2,000 字的原文前缀；此类截断记录无法仅从前缀重算全文哈希，Task 会校验来源快照身份、哈希格式与前缀内逐字摘录，并把此限制如实保留。缺少旧版 `source_text_truncated` 字段的历史快照不强制按完整文本重算，以保持历史 Run 可读。
3. **语义支持充分性**：关键用语/数值匹配只是确定性关联筛查，不是自然语言蕴含证明。正文结论是否被证据语义支持仍标为需人工复核；系统不会把字符串匹配报告成事实正确。

诊断按 `structure`、`source`、`citation`、`unverified` 分组，记录 `error_code`、claim/unverified ID、正文锚点或位置、错误类型、涉及的 Evidence ID 和可读原因。完整诊断保存在节点 `evidence_ledger` 元数据中，节点错误只显示分组计数摘要。历史旧协议没有稳定 claim ID，因此兼容路径不会伪造 ID；其诊断标识为 `legacy_issue` 并保留原始错误信息。

## 通用协议与 S02 边界

通用 Claim/Evidence 类型、稳定证据 ID、授权主体检查、逐条 binding、Markdown 锚点和分组诊断位于 `_evidence_contract.py` 与 `_citation_verifier.py`。它们不包含 S02 章数、B 条款或项目事实词表。

`_s02_project_planning_evidence.py` 继续负责 S02 专属政策：项目事实和模板规则的来源分类、B-01～B-15 条款检查、相邻 chunk 的同文档条款组合、项目事实待核实要求及 S02 台账生成。不同来源的 chunk 不会合并。S02 v1 模板和其历史运行格式未改；新结构先尝试 `task-claim-evidence`，没有新协议时保留旧 `task-evidence-claims` 读取路径。旧 `task-citations` 格式继续有效，新格式支持 `citation_id`、`evidence_id` 和 quote。

知识库适配器只更新了生成给 Agent 的协议说明文字。TaskRun 读取时会为旧证据快照派生 owner-scoped Evidence ID，不写回、不迁移历史数据。Task 现在把授权主体保存在服务端 Run 结果中，Agent 收到的检索文本包含 Evidence ID，但不包含授权主体字段。

EvidenceRecord/引用协议可表达 Tool/MCP 或其他记录来源，但本阶段没有新增或声称已接入 Tool/MCP 证据采集器。未来适配器必须从服务端授权结果创建并持久化来源快照，不能接受模型自报的工具来源。

## 本阶段修改文件

- `examples/agent_service/task/_evidence_contract.py`：新增 Pydantic Evidence/Claim/Binding/Unverified 数据结构、服务端 Evidence ID 派生、锚点解析、字段/来源/摘录关联校验和结构化诊断。
- `examples/agent_service/task/_citation_verifier.py`：兼容旧知识库来源字段并增加 Evidence ID 引用；为本次运行的未截断原文增加 SHA-256 重算，对服务端传入的 TaskRun 所有者执行来源归属检查。
- `examples/agent_service/task/_knowledge.py`：统一检索结果里的 citation protocol 说明，避免模型收到两套互相冲突的字段要求。检索权限及知识库实现未改。
- `examples/agent_service/task/_service.py`：在 Run 证据快照中附加 Evidence ID、授权主体与采集时间；Agent 展示中隐藏授权主体；更新 S02 最终输出协议及诊断摘要；最终输出仍重新执行引用核验。
- `examples/agent_service/task/_s02_project_planning_evidence.py`：增加新协议到 S02 台账的场景适配、跨相邻 chunk 的 B 条款文本检查和表格/待核实关联；保留旧协议兼容分支。
- `tests/task_evidence_contract_test.py`：新增通用协议与 S02 用例。
- `tests/task_project_planning_rag_test.py`：增加端到端 Run 证据 ID/授权主体保存及 Agent 输出隐藏主体字段的断言。
- `examples/agent_service/task/docs/P6-G-1-claim-evidence-contract-report.md`：本报告。

没有修改 AgentScope 官方源码、SOPEngine、`main.py`、知识库核心、认证、Task 外业务模块、前端、共享 API、数据库迁移、依赖或构建配置。

## 回归与验证

执行命令：

```powershell
$taskTestFiles = Get-ChildItem tests -Filter 'task_*_test.py' | ForEach-Object { $_.FullName }
& .\.venv\Scripts\python.exe -m pytest @taskTestFiles -q
```

结果：**162 passed, 2 skipped, 2 subtests passed**。跳过项是 PostgreSQL Task 测试未配置 disposable PostgreSQL 测试 URL，不代表完成真实 PostgreSQL 集成验证。

另外，Task 证据协议核心文件和新增测试通过 Black 23.3.0、79 字符行宽检查；本阶段 Python 文件通过 `py_compile`；`git diff --check` 无空白错误。项目环境没有安装 `pre-commit`，因此没有运行 pre-commit hook。已有未提交的 `_knowledge.py`、`_service.py` 和 `task_project_planning_rag_test.py` 中存在与本次改动无关的 Black 格式差异；为保留工作区原有修改，没有对这些文件做全文件自动格式化。

本阶段未取得真实登录会话、真实失败 TaskRun、线上知识库证据快照或 Workspace 文件访问环境。S02 的实际知识库检索、生产 chunk 的 B 条款跨 chunk 组合及生产 DOCX 下载仍须真实环境验收。测试结果仅证明 Task 适配逻辑和专用夹具行为。

## 工作区与后续

仅对 Task 目录和 `tests/task_*` 做了本阶段变更，并在 Task docs 内新增报告。工作区中原有的销售台、Task 前端、`.pnpm-store`、Playwright、blobs、output 和 tmp 等未提交内容均保留且未纳入本阶段修改。没有提交代码。

待 P6-G-2 评估的事项：为其他场景迁移统一声明协议；为实际 Tool/MCP 结果建立服务端证据快照适配；若需要对截断 chunk 独立复算全文哈希，需在不破坏历史快照的前提下设计安全保留原文/定位方案；评估更强的语义支持人工审核流程。当前语义充分性仍需人工判断。本阶段到此结束。
