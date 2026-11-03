# P6 九大业务场景统一完善与自动化验收报告

## 1. 总结

本次在 Task 现有架构内完善九场景模板、通用错误/输出展示和场景证据策略；没有改造 Task 引擎、SOPEngine、Planner 或 Artifact Writer。

**结论：模板目录、Task 创建、隔离测试和文件序列化为 DEMO PASS；九个场景的生产业务执行整体仍为 BLOCKED。** 当前工作区没有可信的登录浏览器验收记录，也没有已确认的专家库、会议、OCR/ASR、报告规则、档案或财务业务 Tool/MCP 联调结果。测试夹具不代表这些服务可用。

- 分支：`integrate/dev_l`
- 基准 HEAD：`1418f2b`
- 未提交、未暂存、未推送。
- 前阶段记录：[P6-nine-scenarios-stage-1-5.md](P6-nine-scenarios-stage-1-5.md)
- 基线记录：[P6-nine-scenarios-phase-0-baseline.md](P6-nine-scenarios-phase-0-baseline.md)

## 2. 通用 Task 变更

### 2.1 证据协议和策略

- 通用 `EvidenceRecord`、`ClaimRecord`、`CitationBinding`、`UnverifiedRecord` 及来源快照逻辑复用 P6-G-1 实现；服务端根据本次 TaskRun 证据快照生成 Evidence ID，并核对授权主体、来源身份、原文摘录及可验证哈希。
- 新增版本化 `EvidencePolicy` 和九场景策略目录。策略声明 claim 类型允许的来源类别、引用要求及需要人工语义复核的 claim 类型。
- S02 是当前唯一接入该通用校验器的场景适配器。S01、S03–S09 尚无完整的业务证据采集适配器，策略目录本身不代表来源已接入。
- 结构、引用编号、授权来源和原文摘录使用确定性检查；“证据是否语义上充分支持业务结论”仍标记人工复核。

### 2.2 错误和运行结果

- `TaskErrorDetail` 为节点或 Run 保存分类、错误码、可读信息、Task/Run/Node ID 和发生时间；新字段为可选，历史 Task/Run 记录保持可读。
- 运行时识别工具缺失、权限拒绝、Schema 不匹配、知识源错误、引用失败、产物失败和通用执行失败。Preflight 保留结构化分类与错误码。
- Task 前端可展开查看完整节点输出、结构化结果、Tool/Task 原生能力标识、执行元数据/证据和错误诊断。结构化结果显示时会过滤常见 token、密钥、密码及私有推理字段。
- 保留现有自由 AI Planner 流程。模板实例化不调用 Planner。

### 2.3 当前模板版本与产物配置

所有系统模板保留 v1 文件，目录将 v2 设为当前版本。所有 v2 最终节点配置在现有范围内的产物格式：

| 场景 | 当前模板 | 节点数 | 最终产物 |
|---|---:|---:|---|
| S01 项目立项 | 2.0.0 | 7 | Markdown |
| S02 项目策划 | 2.0.0 | 7 | DOCX |
| S03 专家智能匹配 | 2.0.0 | 8 | Markdown |
| S04 会前准备 | 2.0.0 | 7 | DOCX |
| S05 会议召开与纪要 | 2.0.0 | 6 | DOCX |
| S06 意见整理 | 2.0.0 | 7 | XLSX |
| S07 报告生成 | 2.0.0 | 8 | DOCX |
| S08 归档移交 | 2.0.0 | 6 | XLSX |
| S09 报销结算 | 2.0.0 | 6 | XLSX |

S02 与 S03 的 v2 流程数据除版本号和最终 ArtifactConfig 外与各自 v1 相同；测试确认 S03 仍为 8 节点且 `collect_human_review` 保持 InputStep。v1 不增加产物配置，不改变既有任务语义。

S04/S08/S09 v2 将危险外部动作改为草稿输出：不会发通知、写档案或提交 ERP；这些能力仍明确列为不支持。

## 3. 九场景统一验收矩阵

状态定义：**PASS** 为实际验证；**DEMO PASS** 为隔离合成数据/测试执行器验证；**BLOCKED** 为缺少真实服务或权限；**NOT TESTED** 为没有执行该项验收。本矩阵不将合成服务当成真实联调。

| 场景 | 模板、节点、编辑 | Preflight / Tool 状态 | 测试闭环与来源 | 文件 | 浏览器 | 正式业务验收 |
|---|---|---|---|---|---|---|
| S01 项目立项 | v2 可创建，7 节点，受现有模板编辑规则约束：DEMO PASS | `project.similar_search`、`project.rule_check`、`project.authorization.read` 只有能力要求，未发现可确认的生产 Task 适配：BLOCKED | 创建/隔离/无工具失败关闭：DEMO PASS；真实历史项目与规则来源：BLOCKED | Markdown 序列化：DEMO PASS；真实 Workspace：BLOCKED | NOT TESTED | BLOCKED |
| S02 项目策划 | v2 可创建，7 节点；复用 P6-D-2F/G 检索和 P6-G-1 证据链：DEMO PASS | Task 原生模板 RAG、引用核验及 Artifact Writer 路径存在；真实登录知识库、Agent 会话和用户 Workspace 未验收：BLOCKED | 授权/来源/模板定向检索、项目资料证据、引用校验及最终重校验使用隔离 KB 夹具：DEMO PASS；真实知识库来源：BLOCKED | DOCX 序列化及测试用临时目录 Writer 路径：DEMO PASS；真实 Workspace 下载：BLOCKED | NOT TESTED | BLOCKED |
| S03 专家智能匹配 | v2 保持 8 节点，人工意见仍为普通 InputStep：DEMO PASS | `expert.lookup`、`expert.conflict_check`、`expert.rank` 有严格能力契约，但无已验证专家目录/回避/评分 Tool：BLOCKED | 协议和模板创建测试：DEMO PASS；真实候选、来源与评分：BLOCKED | Markdown 序列化：DEMO PASS | NOT TESTED | BLOCKED；正式 HumanApproval 仍 BLOCKED |
| S04 会前准备 | v2 可创建，7 节点：DEMO PASS | 费用、材料生成和检查 Tool 未做真实注册验证；`notification.send` 不执行：BLOCKED | 模板/Preflight 失败关闭：DEMO PASS；真实费用规则与计算：BLOCKED | DOCX 序列化：DEMO PASS；只表示草稿包 | NOT TESTED | BLOCKED |
| S05 会议召开与纪要 | v2 可创建，6 节点：DEMO PASS | `meeting.audio_transcribe` 未联调；当前流程没有已验证的 ASR 或免 ASR 转写文本替代路径：BLOCKED | 模板和无工具 Preflight：DEMO PASS；真实转写、发言时间定位：BLOCKED | DOCX 序列化：DEMO PASS | NOT TESTED | BLOCKED |
| S06 意见整理 | v2 可创建，7 节点：DEMO PASS | `opinion.image_ocr`、`opinion.classify`、`opinion.export` 未联调：BLOCKED | 模板和无工具 Preflight：DEMO PASS；真实 OCR/来源定位/意见去重：BLOCKED | XLSX 序列化：DEMO PASS | NOT TESTED | BLOCKED |
| S07 报告生成 | v2 可创建，8 节点：DEMO PASS | 报告 RAG、引用、完整性/一致性/合规 Tool 未接入验证；S07 尚未绑定通用 Claim–Evidence 最终校验适配器：BLOCKED | 当前只有场景策略契约，不能证明报告来源闭环：BLOCKED | DOCX 序列化：DEMO PASS | NOT TESTED | BLOCKED |
| S08 归档移交 | v2 可创建，6 节点：DEMO PASS | 文件元数据与完整性 Tool 未联调；档案写入和回执不执行：BLOCKED | 模板/Preflight 失败关闭：DEMO PASS；真实档案资产权限和完整性规则：BLOCKED | XLSX 序列化：DEMO PASS；仅待移交清单 | NOT TESTED | BLOCKED |
| S09 报销结算 | v2 可创建，6 节点：DEMO PASS | OCR、发票验真去重、费用规则未联调；没有确定性 Decimal 业务核算适配器；ERP 提交不执行：BLOCKED | 模板/Preflight 失败关闭：DEMO PASS；真实票据及财务规则：BLOCKED | XLSX 序列化：DEMO PASS；仅待提交草稿 | NOT TESTED | BLOCKED；正式财务审批仍 BLOCKED |

## 4. 可复用能力与缺口

### 可复用且已在 Task 范围存在

- TaskService、TaskSOPStep、AgentScope SOPEngine、InputStep 和自由 Planner 路径。
- 九场景模板 Registry、编译、实例化、版本追溯和自定义副本。
- Task 原生 `planning.template_retrieve`、`document.citation_verify`，及按 TaskRun 保存的证据快照。
- ArtifactConfig、Markdown/DOCX/XLSX 序列化器、Task Artifact Writer 协议、TaskRun 产物记录与所有者校验读取接口。
- Preflight 对真实 Tool/Schema/权限/数据源能力的失败关闭检查。
- 专家查询/回避/排序能力契约（只含契约，不代表连接的专家服务）。

### 尚需业务方或环境提供

- 可用登录用户与 Task 页面会话；可运行 Agent/Session；真实用户知识库选择和已授权资料；经用户权限预检的 Task Workspace。
- S01 历史项目检索、规则来源和项目授权接口。
- S03 专家目录、回避规则、评分服务及正式审核授权/审计策略。
- S04 会议费率和规则来源、会议物料能力；消息网关仅在未来获得授权后使用。
- S05 内网 ASR 或批准的转写文本导入协议。
- S06 OCR、意见分类规则和意见来源定位服务。
- S07 授权 RAG、引用关系和报告完整性/一致性规则服务。
- S08 已授权文件资产、归档分类/完整性规则及正式档案接口（若未来需要写入）。
- S09 OCR、发票真实验真/查重、费用规则和确定性金额计算；ERP 幂等、权限及审批协议。
- PostgreSQL 一次性测试库，以运行当前两个被跳过的 Postgres 专项测试。

未接入服务的模板仍可创建和编辑，但 Preflight 不得被解释为业务结果正确。对于正式审批、通知发送、档案写入、ERP 过账，本阶段不执行。

## 5. 测试和工程验证

最终执行结果：

| 检查 | 命令/范围 | 结果 |
|---|---|---|
| Task 专项全量回归 | `tests/task_*.py` 全部 Task 专项文件 | **169 passed, 2 skipped, 2 subtests passed** |
| PostgreSQL | `tests/task_postgres_sop_test.py` 等 | 2 项因当前未配置可用测试数据库而 skipped；未声称通过真实数据库集成 |
| Python 编译 | `python -m compileall -q examples/agent_service/task tests/task_*.py` | PASS |
| 前端生产构建 | `pnpm build`（`examples/web_ui/frontend`） | PASS；Vite 提示有大于 500 KB 的 chunk，不影响构建 |
| 全仓前端 lint | `pnpm lint`（`examples/web_ui/frontend`） | FAIL；全仓 55 errors、22 warnings，包含多个未修改模块的导入顺序问题；未修改的 `src/pages/task/task-template-gallery.tsx` 也有 2 项导入顺序错误 |
| 本次修改的 Task 前端 lint | `pnpm exec eslint src/api/task.ts src/pages/task/index.tsx src/pages/task/task-run-panel.tsx` | PASS |
| Diff 空白检查 | `git diff --check` | PASS；输出只有 LF/CRLF 转换警告 |
| Black / Ruff | 当前 `.venv` 模块探测 | NOT AVAILABLE；未安装，未增加依赖 |

Task 全量测试在 PowerShell 中使用文件枚举运行：

```powershell
$taskTests = @(Get-ChildItem -Path tests -Filter 'task_*.py' | ForEach-Object { $_.FullName })
& .\.venv\Scripts\python.exe -m pytest @taskTests -q
```

浏览器端 `http://localhost:8000/task` 返回 HTTP 200，但只说明路由可达。Computer Use 尝试检查已打开的 Chrome 页面时无法可靠确认当前 URL，按安全策略停止；因此模板浏览、创建、运行、结果和下载的真实浏览器交互均为 **NOT TESTED**。

Artifact 序列化测试验证格式内容和测试用临时目录 Writer 路径；没有使用真实用户 Workspace 进行写入或授权下载验收。

## 6. 修改文件及白名单核对

Task 后端：

- `examples/agent_service/task/_models.py`
- `examples/agent_service/task/_errors.py`
- `examples/agent_service/task/_service.py`
- `examples/agent_service/task/_template_models.py`
- `examples/agent_service/task/_template_registry.py`
- `examples/agent_service/task/_template_service.py`
- `examples/agent_service/task/_template_validation.py`
- `examples/agent_service/task/_evidence_contract.py`
- `examples/agent_service/task/_scenario_evidence_policies.py`
- `examples/agent_service/task/_s02_project_planning_evidence.py`
- `examples/agent_service/task/_citation_verifier.py`、`_knowledge.py`：延续并复用 P6-D-2F/G 的 Task 改动。
- `examples/agent_service/task/templates/s01_project_init.v2.json` 至 `s09_expense_settlement.v2.json`（九个 v2 文件）。

Task 前端/API：

- `examples/web_ui/frontend/src/api/task.ts`
- `examples/web_ui/frontend/src/pages/task/index.tsx`
- `examples/web_ui/frontend/src/pages/task/task-run-panel.tsx`

Task 专项测试：

- `tests/task_nine_scenarios_test.py`
- `tests/task_evidence_contract_test.py`
- `tests/task_template_version_test.py`
- `tests/task_template_test.py`
- `tests/task_artifact_capability_test.py`
- `tests/task_custom_flow_test.py`
- `tests/task_project_planning_rag_test.py`（同时包含工作区已有的 P6-D-2F/G 覆盖）。
- `tests/task_project_planning_evidence_chain_test.py`

Task 文档：

- `examples/agent_service/task/docs/P6-nine-scenarios-phase-0-baseline.md`
- `examples/agent_service/task/docs/P6-nine-scenarios-stage-1-5.md`
- `examples/agent_service/task/docs/P6-nine-scenarios-integration-report.md`

**本次实现没有修改白名单外的源文件。** `sales-console/apps/admin-web/src/App.tsx`、`sales-console/services/sales-api/src/app.ts` 的既有未提交修改及 `sales-console/compose.dev.yaml` 等未跟踪内容已在开始时存在，本次未触碰、未暂存。没有运行 `git add .`，没有提交或推送。

## 7. 尚未通过的验收与后续建议

1. 在有登录用户、真实 Agent/Session、知识库和 Task Workspace 的环境中执行 S02：模板检索、项目资料检索、最终 Claim–Evidence 台账、DOCX 写入/授权下载。
2. 为 S05 增加能安全支持“ASR 可用时调用；否则导入已转写文本”的显式流程协议；当前没有条件节点，不能用模板文字假装已实现分支。
3. 仅在接到真实专家、会议、OCR/ASR、报告、档案、财务服务后逐场景接入 Tool，并通过真实权限预检和服务端响应验证。
4. 为 S01、S03、S07 等场景实现 TaskRun 证据采集适配器后，再启用各自 EvidencePolicy 的执行校验；目前仅 S02 有实际场景适配器。
5. 正式 HumanApproval 需要可信审批身份、服务端授权策略、审计和恢复协议；不能以当前 InputStep 替代。
6. 提供登录浏览器会话与 PostgreSQL 测试库后完成未验收项目；未完成前不提升生产验收状态。

## 8. 最终验收

- 九个当前模板目录、版本解析、创建、隔离和无 Planner 路径：**DEMO PASS**。
- 最多八节点、S02/S03 v1 兼容和危险副作用未伪装：**PASS（自动化测试）**。
- 产物序列化：**DEMO PASS**；真实用户 Workspace 写入/授权下载：**BLOCKED**。
- TaskRun 结构化错误和节点详情展示：**PASS（构建及测试）**；浏览器交互：**NOT TESTED**。
- S02 真实知识库/模型/Workspace 端到端：**BLOCKED**。
- S01、S03、S04–S09 真实业务 Tool/MCP：**BLOCKED**。
- 正式 HumanApproval：**BLOCKED**。

本阶段已完成允许范围内的模板版本、通用 UI/API 和隔离自动化验证工作。九场景生产执行验收仍须等待各业务服务、凭据和授权环境接通；完成本报告后停止，不自动开发下一阶段。
