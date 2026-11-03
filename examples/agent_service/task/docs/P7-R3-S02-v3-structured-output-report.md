# P7-R3：S02 v3 结构化输出与确定性证据组装报告

## 结论

**自动化验收：PASS。真实登录、真实知识库 RAG、真实模型和浏览器下载：NOT TESTED。**

S02 v3 已作为独立模板版本发布。最终节点现在只接收业务 JSON；Task 在服务端校验内层 JSON Schema、匹配本次 Run 的授权 Evidence、生成 Claim–Occurrence、Citation、Binding、Unverified 协议，然后继续调用既有 S02 台账校验、通用 Evidence 校验和 Artifact Writer。v1/v2 模板文件与已保存的历史 Run 没有迁移或重解释。

本轮没有修改 `src/agentscope/**`、Task 外业务代码、`main.py`、共享 API/认证、数据库迁移、依赖、全局导航或前端文件；没有提交或推送。开始时工作区已有未提交内容，本轮保留。工作区中的其他未跟踪缓存、运行数据和销售台相关内容也未纳入本轮变更。

## 基线与 P7-R2 接口核对

- `NodeOutputContract` 已有 `structured_json`、`json_schema`、`validation_mode=json_schema`、`downstream_binding=validated_result` 和 `fail_closed` 契约字段：`examples/agent_service/task/_models.py:271-310`。模板编译会把契约放到最终 TaskNode，并向模型提示 Schema；AgentScope 执行适配器只在编译后提示中没有 Schema 说明时追加运行时提示：`examples/agent_service/task/_agentscope_executor.py:393-410`。
- `AgentStepOutcome` 验证的是 `{status, output, reason, questions}` 外层状态协议，`output` 只是字符串：`examples/agent_service/task/_models.py:209-229`。因此外层合法不代表内层业务 JSON 合法。S02 v3 适配器再次解析原始文本，并调用既有 `validate_structured_result()`：`examples/agent_service/task/_s02_structured_output.py:68-115`、`examples/agent_service/task/_output_validation.py:81-145`。
- RAG Evidence 从此前已完成的 Task RAG 节点结果读取，并限定在当前 TaskRun 的保存快照内；S02 维持自身单一来源结果，不创建第二个 Evidence Store：`examples/agent_service/task/_service.py:297-330`。
- S02 v3 在最终 Agent 节点后运行适配器，失败即返回 failed `StepExecutionResult`；成功后才进入既有 Citation 物化、S02 台账与输出契约校验：`examples/agent_service/task/_service.py:3139-3234`、`examples/agent_service/task/_service.py:3353-3450`。S02 v3 最终节点最多执行一次，不用模型重试修补结构：`examples/agent_service/task/_service.py:4484-4520`。
- 既有 Artifact Writer 将产物写入 `artifacts/{run_id}/...`，产物记录保留当前 `run_id`、`task_id`、`node_id`：`examples/agent_service/task/_service.py:4769-4799`。本轮没有更改 Writer。
- Registry 将画廊列表限制为每场景一个 current 版本，并通过精确版本解析读取历史定义：`examples/agent_service/task/_template_registry.py:15-59,183-205`。Task 校验器仍根据已保存的 `source_template_id/source_template_version` 解析对应的不可变定义：`examples/agent_service/task/_template_validation.py:246-260`。

P7-R2 报告基线为 **260 passed、2 skipped**。这轮全量测试为 **272 passed、2 skipped、21 个 subtests passed**。两个 PostgreSQL 专项测试因未配置一次性测试库环境变量 `LXSCOPE_TASK_TEST_DATABASE_URL` 而跳过，不代表 PostgreSQL 集成通过。

## 根因与修正

1. **模型承担了过多协议职责。** S02 v2 最终 Agent 必须输出 Markdown、Claim、Occurrence、Citation、Binding 和 Unverified JSON。多次真实 Run 暴露围栏、锚点、字段和正文位置不一致。v3 将 Agent 输出限制为业务章节、分类、待处理信息及逐字来源候选；服务端生成所有协议身份和状态。
2. **内层 JSON 没有被外层 AgentStepOutcome 覆盖。** 即使外层 Outcome 有效，内层文本仍可能截断或含重复 JSON 键。v3 使用重复键拒绝解析器、64 KB 输入上限及已有 JSON Schema 校验；错误在适配器阶段失败，不进入 Artifact Writer。
3. **最终结构协议不能作为普通模型文本接受。** v3 服务端按 Schema 读取结构化业务数据，再生成报告正文、唯一的 `task-claim-evidence` 与 `task-citations` 区块。Agent 输出中的协议标识、哈希、来源 ID、授权身份和“已通过”声明均不被采用。
4. **S02 台账存在模板规则被误判成项目事实的分类冲突。** 通用检查器的宽泛项目事实词表会将包含“设备台账字段”等模板编写规则词句的 Claim 额外判为项目事实，继而错误要求 `project_material`。当前修复优先识别 B 条款/模板规则 Claim，同时仍保留数字项目事实检查；模板规则依旧必须由允许的模板 Evidence 和条款原文支持：`examples/agent_service/task/_s02_project_planning_evidence.py:403-435,448-461`。这不是放宽来源校验。
5. **发布新 current 版本后，显式 v2 实例化导致历史契约回归。** 首轮全量测试出现 16 项失败，均源于实例化服务只比较 current 版本，拒绝既有调用传入的已发布 S02 v2；不是 v3 结构化适配器失败。现改为按请求中的精确版本解析已发布模板，未知版本仍返回 409；画廊仍仅列 current 版本。旧模板按原定义创建，Task 校验继续绑定其精确版本：`examples/agent_service/task/_template_service.py:85-110`。

## S02 v3 模板与输出协议

新增 `examples/agent_service/task/templates/s02_project_planning.v3.json`，Registry 将 S02 current 指向 `3.0.0`：`examples/agent_service/task/_template_registry.py:18-20,49-52`。

- 保持既有 7 个用户可见步骤、八章顺序与 B-01～B-15 要求；没有添加格式化 Agent 或 SOP 节点，仍低于 8 节点上限。
- 既有最终“整理策划输出”节点配置 `structured_json`、JSON Schema、`json_schema` 校验、`validated_result` 下游绑定及 fail-closed：`examples/agent_service/task/templates/s02_project_planning.v3.json:176-205,335-340`。
- Schema 约束报告标题、八章（1–8 顺序）、每章内容项、允许的 Claim 分类、来源候选数量/长度、B 条款编号、三种待处理状态及期望证据类型；顶层及对象字段拒绝额外属性。服务端附加业务约束：B-01～B-15 各出现一次、最多 128 项、报告最大 100 KB、建议标记、单行安全文本，以及待核实项必须说明原因和所需证据类型：`examples/agent_service/task/_s02_structured_output.py:17-51,318-460`。
- v1/v2 JSON 未被本轮编辑，历史 TaskRun 未迁移；v2 仍保持其 Markdown/Claim–Evidence 契约，v3 单独走新适配器。

## 确定性 Claim–Evidence 组装与安全边界

新适配器 `examples/agent_service/task/_s02_structured_output.py:54-315` 的处理顺序：

1. 对 Agent 原始 `result.text` 做大小限制、重复键拒绝解析和 Schema 校验，然后执行 S02 业务形状检查。
2. 对 fact、rule、historical_case 按 S02 EvidencePolicy 要求来源；analysis/recommendation 可按策略不带来源；unverified 不得带来源摘录。
3. Agent 只能提交连续原文候选。Task 在当前 Run 已保存的 Evidence snapshot 中逐字查找；检查 owner、`ready` 状态、服务端 Evidence ID、hash 格式及在完整快照可复算的 SHA-256，再按 Claim 种类检查允许的 Evidence 类型。无匹配、多个匹配、类型错误、权限不符或哈希不符均失败关闭：`_s02_structured_output.py:463-531`。
4. Task 以输入顺序确定性生成 `CL-####`、`O-####`、`C#` 和 `UV-####`。同一文本 Claim 可有多个 Occurrence；每个 Occurrence 单独带 Citation 引用并生成独立 Binding。每条待核实声明各自保留状态、原因和期望证据类型：`_s02_structured_output.py:197-260`。
5. 服务端生成正文锚点和两个协议区块；语义支持不会被字符串匹配伪装成自动验证，报告明确保留 `requires_human_review`。之后继续调用通用 Evidence/引用校验与 S02 台账规则。

此设计只证明结构、来源身份/权限及原文摘录一致性，不证明该摘录在业务语义上充分支持结论。内容候选与授权来源不唯一对应时拒绝；不猜 Evidence ID、不补写引用、不删除 Claim 以换取通过。

## 修改文件

- `examples/agent_service/task/templates/s02_project_planning.v3.json`：新增独立 v3 业务 Schema 与最终节点契约。
- `examples/agent_service/task/_template_registry.py`：注册 S02 v3 为 current；澄清 current 列表与精确历史版本解析的区别。
- `examples/agent_service/task/_template_service.py`：按请求版本解析不可变模板；未知版本仍拒绝。
- `examples/agent_service/task/_s02_structured_output.py`：新增 Task 内的严格结构解析、证据唯一匹配和协议物化。
- `examples/agent_service/task/_service.py`：把 v3 最终节点接入适配器和既有校验链；v3 不重试最终 Agent；失败不进入成功产物路径。
- `examples/agent_service/task/_agentscope_executor.py`：编译提示已经包含 Schema 时不再重复追加整份 Schema。
- `examples/agent_service/task/_s02_project_planning_evidence.py`：修复模板规则与项目事实分类冲突，同时保留原来源/条款检查。
- `tests/task_s02_v3_structured_output_test.py`：新增适配器及 TaskService→SOP→Verifier→Artifact 门控集成回归。
- `tests/task_template_version_test.py`：验证 v3 Schema/current 与显式 v2 原契约兼容。
- `tests/task_nine_scenarios_test.py`：current 版本期望更新为 S02 v3。
- `tests/task_p7b_demo_data_compatibility_test.py`：演示输入按文件声明的精确历史版本校验，避免把 v2 演示数据错误地套用到 v3 Schema。
- 本报告：`examples/agent_service/task/docs/P7-R3-S02-v3-structured-output-report.md`。

全部代码与测试文件均在 Task 白名单内；无前端文件修改，也未运行前端构建。

## 自动化验收结果

| 检查 | 结果 |
|---|---|
| S02 v3 定向回归 | **10 passed**：结构化适配器及 TaskService→SOP→Verifier→Artifact 集成 |
| Task 全量 pytest（所有 `tests/task_*.py`） | **272 passed，2 skipped，21 subtests passed** |
| PostgreSQL 专项 | **NOT TESTED**：未配置专用一次性数据库 URL，2 项 skip；未连接生产或未知数据库 |
| Task Python 编译 | **PASS**：`python -m compileall -q examples/agent_service/task` |
| `git diff --check` | **PASS**：无空白错误；Git 输出 LF→CRLF 工作区提示 |
| 前端 ESLint / build | **NOT RUN**：本轮无前端变更 |
| 真实模型、登录用户 RAG、真实 Workspace 写入/授权下载、浏览器验收 | **NOT TESTED**：本轮未使用真实用户会话或在线服务 |

S02 v3 集成测试使用显式 DEMO/SYNTHETIC 的授权快照和测试 Writer，验证了完整 TaskService→SOP→适配器→通用 Evidence/输出契约校验→DOCX Artifact 门控；确认成功产物关联当前 TaskRun，并确认 JSON/来源校验失败时 Writer 未被调用。这是 Task 自动化集成验收，不是生产 RAG、真实模型或 Workspace 联调。

脱敏失败回归复现了旧 S02 输出形状（15 个 Claim、14 个 Binding、5 个 UnverifiedRecord 和 47 个正文引用标记）；v3 把该 Markdown/协议混合输出拒绝为内层 JSON 格式错误，且无重试、无产物。已有 P7-R2 79 Claim 规模用例也包含在全量 Task 测试中，继续验证旧通用协议的历史校验路径。

## 真实 Run 验收步骤

1. 部署包含本轮后端 Task 修改的代码；后端若未启用自动重载，应重启后端进程。无需前端重建。
2. 在真实登录会话中进入 Task 业务模板，选择“项目策划”当前版本 **3.0.0**，通过既有授权资料选择/输入功能提供用户有权访问的项目资料与策划模板，再创建 Task。
3. 运行 Preflight，确认模板检索、项目资料来源、引用校验和 DOCX Workspace 能力状态来自真实服务；任何权限或数据源错误都应保持失败。
4. 执行完整 Run。在最终节点检查 `s02_v3_structured_output.passed`、`evidence_ledger.passed`、`output_contract_validation.status`，并检查诊断中的 Claim、Occurrence、Binding 和 Unverified 数量。
5. 仅当 Run 成功时，从当前用户 Workspace 下载 Task 产物；核对 Artifact 的 `run_id/task_id/node_id`、DOCX 可打开性、引用台账和人工语义复核标记。
6. 再用一个缺失原文候选或无权来源的输入确认 Run 失败且没有成功 DOCX；不要用测试夹具结果替代真实服务结论。

仍需人工确认真实资料来源是否支持各条业务结论；该语义审查当前不自动化。真实 S02 v3 Run、真实 RAG 授权、浏览器操作和下载在本轮均保持 **NOT TESTED**。
