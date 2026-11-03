# P7-B：九场景演示数据与最小端到端验收报告

核查日期：2026-10-12

分支：`integrate/dev_l`

工作区 HEAD：`1418f2b`（未提交）

## 1. 结论摘要

| 验收项 | 状态 | 结果 |
|---|---|---|
| 九场景统一 DEMO/SYNTHETIC 数据包 | **PASS** | 已新增统一项目背景、九份当前模板版本的 API 输入样例和四份知识库候选源文件。 |
| 九场景模板输入协议 | **PASS** | 九份输入通过仓库当前模板版本及 `TaskTemplateCompiler.validate_inputs`；均不超过 8 个节点。 |
| S03/S04/S05/S06/S08/S09 Task 原生确定性适配器 | **DEMO PASS** | 使用本数据包的虚构输入直接演练适配器；不是已登录 TaskRun、Agent/Session 或 Workspace 验收。 |
| S01/S02/S07 真实 RAG、来源证据及模型生成 | **BLOCKED** | 当前可访问的浏览器上下文没有可用登录会话；Task API 返回 401，未能读取授权演示 KB 或模型/Agent 配置。 |
| S01 真实 Task 端到端 | **BLOCKED** | 未创建 Task/Run；没有真实 RAG 证据、模型调用或 Run ID。 |
| S04 真实 Task、DOCX、Workspace 和授权下载端到端 | **BLOCKED** | 未创建 Task/Run；没有真实 Workspace 产物路径或下载记录。 |
| 未授权访问/跨用户 KB 隔离真实环境验证 | **NOT TESTED** | 没有第二个已授权测试身份，也没有当前登录上下文；未尝试猜测或使用其他用户的 KB ID。 |

本阶段完成可独立进行的数据准备和适配器验证。真实 S01、S04 端到端验收没有通过，不能以隔离适配器测试替代。未上传知识库文件、未创建真实 Task、未产生 TaskRun 或产物，也没有连接生产数据库、ERP 或其他外部系统。

## 2. 演示数据包

数据位于 `examples/agent_service/task/docs/p7b-demo-data/`：

- `README.md`：数据使用说明、输入映射、KB 上传及 Task 操作步骤。
- `manifest.json`：跨场景实体、ID 和合成属性清单。
- `inputs/s01-project-init.v2.json` 至 `inputs/s09-expense-settlement.v3.json`：九个当前模板版本的实例化请求体，每份只含该模板 `inputs` Schema 接受的字段。
- `knowledge-sources/DEMO-SOURCE-001-project-charter.md`：虚构项目委托及立项背景。
- `knowledge-sources/DEMO-SOURCE-002-planning-template.md`：演示策划章节和 B-01～B-15 编写提示，不是正式制度。
- `knowledge-sources/DEMO-SOURCE-003-demo-review-rules.md`：演示核对清单，不是法律、标准或正式规则。
- `knowledge-sources/DEMO-SOURCE-004-report-reference.md`：虚构报告参考材料，不是可验证的历史案例。

统一项目为 `[DEMO/SYNTHETIC] 澄湖科创园综合能源与设备台账优化咨询项目`，项目 ID `DEMO-LXSCOPE-2025-001`。演示资料中的面积、设备数量、用电基线、预算、周期和节能目标都标为合成输入或待核实值。跨场景关联使用 `DEMO-MTG-2025-001`、`DEMO-EXPERT-001` 至 `003`、`DEMO-OP-001` 至 `004`、`DEMO-FEE-RULE-2025-01`、`DEMO-INVOICE-*` 等标记。

数据结构沿用 Registry 当前声明：S01/S02 为 v2.0.0，S03–S09 为 v3.0.0；没有改动系统模板或输入协议。S07 Schema 要求至少一个 `source_material_ids`，其输入文件使用明显的 `__REPLACE_WITH_SERVER_ISSUED_AUTHORIZED_DOCUMENT_ID__` 占位符；替换成当前用户有权访问的真实文档 ID 前，禁止提交该请求。S02 的 `source_material_ids` 为空，检索仍受 Task 中实际选择且当前用户获准访问的知识库范围限制。

文本解析器源码声明支持 `.md` 扩展（`src/agentscope/rag/_parser/_text.py`）；但由于当前请求没有认证，未从运行服务的受保护内容类型接口验证本机部署实际注册的解析器。上传后应以知识库 UI 的支持类型和索引状态为准。

## 3. RAG、Agent 与权限核查

### 3.1 实际服务/API 观察

| 检查 | 结果 | 证据 |
|---|---|---|
| 本地容器 | **PASS** | `docker ps --format ...` 显示 Web UI、AgentScope、Logto、Redis、PostgreSQL 均运行；AgentScope/Web UI/Redis/PostgreSQL 为 healthy。 |
| FastAPI OpenAPI | **PASS** | `GET http://127.0.0.1:8001/openapi.json` → 200。 |
| 认证健康端点 | **PASS** | `GET http://127.0.0.1:8001/auth/health` → 204。 |
| 受保护健康端点 | **BLOCKED** | `GET http://127.0.0.1:8001/health` → 401，要求 Bearer；不是服务不可达。 |
| Task 模板列表 | **BLOCKED** | `GET http://127.0.0.1:8001/tasks/templates` → 401，响应 `Bearer Logto access token is required.` |
| Task 知识库列表 | **BLOCKED** | `GET http://127.0.0.1:8001/tasks/knowledge-bases` → 401。 |
| 当前可用 IAB 登录上下文 | **BLOCKED** | Codex In-app Browser 打开 `http://localhost:8000/task` 后进入登录流程，最终落在 `/chat`；页面列出 0 个会话，Agent 选择器显示“暂无智能体”。此为当前自动化浏览器的状态，不据此推断用户自己的浏览器没有会话。 |

没有发送伪造用户头、粘贴令牌、重用其他用户 KB ID 或绕过 Logto。当前没有可用于本次 API 调用的合法 Bearer 会话。用户所述服务链路已打通与本次可访问浏览器会话不同；后者没有暴露已登录用户上下文。

### 3.2 RAG 路径和失败关闭

- 源码检查确认 S01 `project.similar_search` / `project.rule_check` 复用 Task 原生 `retrieve_scenario_evidence` 与 `TaskKnowledgeGateway.retrieve_task_evidence`，不是新建 RAG。Task Gateway 通过当前 `user_id`、Task 选择的 `knowledge_base_ids`、可选 `source_material_ids`、文档 ready 状态和服务端返回的来源片段构建证据（`examples/agent_service/task/_scenario_runtime.py:131`、`_knowledge.py:256`）。
- S02 的定向策划模板检索和 S07 报告检索仍受已选授权 KB 限制。此轮没有真实索引文档可查，未验证检索命中、来源元数据、模型引用或语义输出。
- 现有 P7-B 专项测试覆盖 RAG 选定范围、服务端来源证据、无结果、规则源不匹配，以及权限失败不得替换为合成证据；本轮专项测试通过。这些是隔离测试，不是本地授权 KB 的真实权限验证。
- 因无有效会话，本轮没有验证 Agent/Session/模型选择、模型真实请求、Agent 是否使用上游片段、用户间权限隔离或 Workspace ACL。

## 4. 外部系统替代方案检查

下表结果为本次用合成输入执行 Task 内确定性适配器得到的 **DEMO PASS**；不代表真实 TaskRun、Agent 节点、SOP 完整执行或外部系统联调。

| 场景 | 本次演练结果 | 明确边界 |
|---|---|---|
| S03 专家匹配 | **DEMO PASS**：结构化候选数据产生 2 条初步排序；`unknown` 候选被排除。 | 候选是用户输入的虚构数据；没有正式专家库、正式冲突核验、资质确认或审批。 |
| S04 会前准备 | **DEMO PASS**：使用 `DEMO-FEE-RULE-2025-01` 计算合计 CNY 3,560.00；材料结构检查通过。 | 费率不是权威费率；通知状态仍为未发送；未生成 DOCX 或写 Workspace。 |
| S05 会议纪要 | **DEMO PASS**：导入与 S04 同一会议 ID 的已转写虚构文本。 | `transcription_performed=false`；没有模拟 ASR 或录音核验。 |
| S06 意见整理 | **DEMO PASS**：4 条已识别文本得到 3 条唯一意见，合并 1 组重复文本。 | `ocr_performed=false`；未处理图片或手写材料。 |
| S08 档案移交 | **DEMO PASS**：3 条元数据均满足样例所列字段。 | `file_content_read=false`、`archive_system_write_performed=false`；不是实际档案登记。 |
| S09 报销结算 | **DEMO PASS**：两条结构化票据核算总额 CNY 2,120.00。 | 不做税务/票据真伪核验，不做正式审批或 ERP 提交。 |
| S01/S02/S07 RAG | **BLOCKED**：当前没有 API 授权会话和可确认的授权演示 KB。 | 只能等用户登录并上传/选择演示资料后做真实检索和证据核验。 |

## 5. 最小端到端测试结果

| 场景 | 目标 | 状态与真实记录 |
|---|---|---|
| S01 | 模板实例化 → 选择授权演示 KB → Preflight → 两个 RAG 节点 → Agent 生成 → 检查来源证据 | **BLOCKED**。认证前置失败；未调用实例化、Preflight 或 Run API。Run ID、节点状态、证据快照均不存在。 |
| S04 | 模板实例化 → Agent/Session → 费用核算 → DOCX → 当前用户 Workspace → 授权下载 | **BLOCKED**。没有可访问的用户 Agent/Session/Workspace 上下文；未调用实例化或 Run API。没有 Run ID、产物路径、文件或下载响应。 |

本阶段没有以 PreviewTaskExecutor、Mock Agent、隔离 Artifact Writer 或离线适配器冒充上述真实 E2E。P7-B 旧报告中的隔离 Writer 字节测试不是本次真实用户 Workspace 产物证据。

## 6. 验证记录

| 验证 | 状态 | 结果 |
|---|---|---|
| 九份输入按当前 Registry + `TaskTemplateCompiler.validate_inputs` 校验 | **PASS** | S01/S02 v2.0.0、S03–S09 v3.0.0 全通过；模板节点数为 6–8。 |
| P7-B 场景适配与模板版本测试 | **PASS** | `python -m pytest tests/task_p7b_scenario_runtime_test.py tests/task_template_version_test.py -q`：**23 passed**。 |
| 本数据包的确定性适配器演练 | **DEMO PASS** | S03/S04/S05/S06/S08/S09 如第 4 节；未调用模型、RAG、TaskStore 或 Artifact Writer。 |
| Task 全量 pytest | **NOT TESTED（本阶段）** | 本轮未改执行代码；P7-B 上一份报告记录的 184 passed、2 skipped 仅作为既有基线，不声称是本轮结果。 |
| 浏览器 Task 交互、Workspace 产物下载 | **BLOCKED** | 当前自动化浏览器没有可用登录/Agent，Task UI 创建和运行交互未发生。 |
| PostgreSQL、生产服务、ERP | **NOT TESTED** | 未连接数据库或外部业务系统；没有数据库写入。 |

## 7. 需要用户手动操作

1. 在用户自己的、已通过 Logto 登录的本地浏览器中打开 `http://localhost:8000/task`。不要把 Bearer token、Cookie 或密码发到聊天。若页面要求登录，只在受信任浏览器内完成正常登录流程。
2. 确认 Task 模块可以读取模板目录、当前用户至少有一个可用 Agent/Session，并且 Agent 已配置可用模型和当前用户 Workspace。当前 Codex IAB 上的 Agent 列表为 0，不能复用该上下文运行。
3. 在本地开发环境中打开知识库模块，选择用户本来就有权访问的演示 KB；若没有，先由 KB 管理员在**开发/演示环境**创建并按现有权限流程授权。不要使用生产 KB。
4. 将 `p7b-demo-data/knowledge-sources/` 下四个 `.md` 文件上传到该演示 KB，等待每个文档解析/索引为 ready。此轮没有上传。上传后的真实 KB ID、文档 ID、chunk 和哈希必须从系统响应读取；`DEMO-SOURCE-*` 仅是本地文件标签。
5. **先跑 S04**：通过模板画廊创建 `s04_meeting_preparation` v3.0.0（输入见 `inputs/s04-meeting-preparation.v3.json`），选择已确认的 Agent/Session/Workspace，运行前按 InputStep 要求填写同一数据。Preflight 后运行。请记录 Run ID、三个能力节点状态、费用总额 CNY 3,560.00、最终 DOCX 的 TaskRun 关联、产物列表和下载后文件内容。
6. **再跑 S01**：创建 `s01_project_init` v2.0.0（输入见 `inputs/s01-project-init.v2.json`），在 Task 知识库选择器只选第 3–4 步准备的授权演示 KB。Preflight 后运行。核对 `project.similar_search` 与 `project.rule_check` 的真实检索状态和证据字段，确认 Agent 引用了本次 Run 的原始来源；无匹配或权限不足时保持失败。
7. 若继续测 S07，将输入 JSON 中的占位字符串替换成刚才服务端返回且当前用户有权访问的实际文档 ID，再创建 Task；不要直接提交占位值。

用户完成上述准备后，至少需要提供可见的 Run ID、Run 节点状态摘要和产物元数据/下载结果；不需要提供任何访问令牌或敏感业务数据。出现 401、403、无索引资料、Preflight BLOCKED 或 Agent/Session 缺失时，保留原始错误类别并按对应授权/配置流程修复，不通过客户端身份字段绕开。

## 8. 修改范围和工作区保护

本阶段只新增 `examples/agent_service/task/docs/` 下的演示数据与本报告，没有修改 Task 后端/前端源代码、模板 JSON、测试代码、共享认证、知识库核心、AgentScope 官方源码或其他业务模块。已有销售台及 Task 未提交文件均保留；没有执行 `git add`、提交或推送。

## 9. 下一步待解决

- 获得当前自动化环境可合法使用的本地登录会话，或由用户在自己的已登录浏览器中执行第 7 节操作。
- 在开发/演示环境确认一个当前用户授权、索引完成的 KB；保留实际来源元数据以便核对。
- 取得真实 Agent/Session、模型和 Workspace 可用性的 API/页面证据。
- 完成 S01 与 S04 真实 Run、S04 DOCX 生成和授权下载后，再判断是否可进入 S01–S09 全量验收。
- S03 正式专家库/回避/审批，S04 权威费率与通知，S05 ASR，S06 OCR，S08 档案系统，S09 票据验真/审批/ERP 仍分别属于外部依赖；本数据包只覆盖安全替代演练。

本阶段到此停止，不进入九场景正式业务验收，不提交代码。
