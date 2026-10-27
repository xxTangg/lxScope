# Task-V1 动态契约与 AI 验收边界治理报告

日期：2026-10-26

## 1. 结论

已完成 Task 层通用信息契约仲裁和 AI 验收边界修复。Agent 可以提示额外风险、建议或待核实信息，但不能凭模型输出创建服务端阻断项；阻断项必须来自当前 Run 冻结的 InputStep 必填项或显式信息契约。SOP 与 DAG 在同一 `ExecutionContext` 契约构造器和同一 executor 仲裁器中处理。

S01 的正式规则来源和规则适用性确认方没有出现在 S01 v3 冻结输入必填字段、该节点的显式信息要求或输出 Schema 中。模板要求来源不足时生成待核实草稿，并禁止正式合规结论。旧 Run 的失败来自需求提取阶段将模型建议映射为重复阻断问题，生成 Agent 与 AI verifier 尚未完成该节点判定。修复保留来源、引用、Schema、权限与 Artifact 的确定性门禁。

**结论状态：Task-V1 定向治理 PASS；选定 SOP/DAG 与场景回归 230 passed、2 failed、15 subtests passed。现有 Claim–Evidence 历史问题仍 BLOCKED；未运行完整 Task 套件、真实模型或外部系统，生产准入 NO-GO。**

## 2. S01 失败根因及权威依据

此前对 Run `48a0e607a8384f40800024034ed5ed4c`、Node `477de471ab18494d92558afc1cd62278` 的只读诊断记录显示：Run 以 `TASK_NODE_EXECUTION_FAILED` 结束；节点被标记失败的原因是重复信息阻断“正式规则/制度来源、规则适用性确认方”。当时的规则检索结果是供复核的候选线索，不能证明规则的效力或适用性。

依据 `templates/s01_project_init.v3.json`：

- 模板 `input_schema.required` 只有 `project_name`、`project_brief`（第 13–20 行）。
- “形成立项建议”节点要求逐项说明候选来源和初步关系；来源不足时标记“待核实”，明确不输出正式规则适用、合规或审批结论（第 132–143 行）。
- 后续“核对项目事实”是既有人工 InputStep，允许记录补充或明确无补充，不代表审批通过（第 153–166 行）。
- 该草稿节点未声明额外 `information_requirements`、JSON Schema 或 `output_contract`。因此这两个字段不是生成草稿的冻结硬性必填项；它们仍是正式规则结论的证据/人工核验要求。

根因不是输出 Schema、引用门禁或 AI verifier 拒绝。失败发生在需求提取后的 clarification 阶段：模型把“建议补充/无法确认”的信息表示为 blocker，旧 Task 逻辑将其当作必填并耗尽重复问题路径，未进入草稿生成。模板本身要求保留待核实事项，流程中已有后续人工核对节点。

持久化状态保留了归一化后的需求/clarification 状态与服务端失败记录；未保留可复现的模型原始 token 响应。因为在阻断前没有调用业务草稿生成 Agent，所以不存在该节点的业务草稿原始响应；AI verifier 也未执行。归因依据是冻结模板、持久化 blocker 和实际执行顺序，而不是对未保存模型原文的猜测。

本次没有重新查询或修改该持久化 Run，也没有发起 Retry。旧 Run 的冻结快照不会被新模板或当前 Registry 改写。其节点可在 Run 当前仍为失败且无在途执行、版本条件满足时，通过既有同 Run Retry API 再试；此建议依据上次只读状态和纯 Agent 草稿节点，无副作用自动调用。重跑前应在页面确认当前状态/版本；这不是本次已执行的恢复验收。

## 3. 契约解析与仲裁

新增 Task 内部 `_information_contract.py`，构造只读的 `task.frozen-information-contract.v1`：

- SOP 使用节点顺序之前的 InputStep；DAG 使用当前节点的祖先 InputStep。
- InputStep `required` 和节点/模板显式声明的 `TaskInformationRequirement` 提供权威分类与来源路径。
- 节点 Prompt、验收标准、输出 Schema/契约与 capability 以路径形式参与 verifier 依据和确定性 gate 说明；结构 Schema、授权来源、引用、权限、交付继续由原有服务端 Validator 执行。
- 契约携带精确模板 ID/版本、Run ID 和冻结快照指纹，不在运行时读取“最新模板”来重解释旧 Run。
- 新版本可通过 Task 模板步骤的 `information_requirements` 声明 `required`、`conditional_required`、`optional`、`pending_verification` 或 `human_confirmation`；条件只接受有限的确定性运算符。版本化 Task 编辑校验会锁定该字段。
- 历史节点没有该新增字段时按空列表读取，快照指纹计算也省略空值，维持旧 Run 的序列化/指纹兼容。

模型需求提取的 `blocking`、分类和声称值都不是权威：Task 会按冻结契约重新裁决。无契约来源的模型字段标为 `pending_verification`，不被错误地称为可选，也不会阻断草稿。规则明确为必填且值缺失时仍阻断；条件必填仅在可信输入满足条件时阻断；条件未知保留 `unresolved`；人工确认只接受用户答案，不接受模型自称已确认。值来自用户答案、Run 初始输入、DAG 绑定或已验证的前序结果，不能从需求模型输出中自证。

## 4. Agent 生成与 AI verifier 边界

- 需求抽取、Agent 生成与 verifier 接收相同的冻结契约身份和规则集合。
- Agent 仅在冻结契约确有 blocker 时进入正式补充路径。模型因未声明信息返回失败时，执行器仅给一次有界提示，要求产出有真实依据的草稿并标注待核实事项；仍失败时保留失败，不无限重问、不编造。
- verifier 负责语义质量，但若要以“契约违规”拒绝，必须引用一个冻结契约源路径及其逐字原文。不能对应原文的额外必填意见降为建议并记录。Verifier 的通过声明不会绕过 JSON Schema、来源授权、Claim–Evidence、输出契约或 Artifact 的服务端校验。
- 对正式合规/审批结论，模板中的冻结标准仍要求来源和核验依据；缺失时草稿可以继续，正式结论仍被拒绝。

## 5. 九场景及版本兼容

| 场景/能力 | 结果 | 证据/范围 |
|---|---|---|
| S01 v3.0.0 原失败字段 | PASS | 使用当前 Registry 编译模板；两个未声明字段被仲裁为未知的待核实事项，生成的是明确待复核草稿；未虚构来源或责任人。 |
| S01 后续人工核对 | PASS（隔离测试） | 已有 InputStep 仍保留；测试确认缺失的人工确认值不能由模型补造。 |
| S02 v5.1 | PASS（选定回归） | `draft_unverified` 轻量交付测试通过；本次未修改 S02 模板、引用或 Claim–Evidence 合同。 |
| S02/S07 历史引用、Claim–Evidence | 原门禁保留；部分历史协议测试 BLOCKED | 当前选定回归复现两项已记录的 P7R1 失败，服务端仍 fail closed 且不生成正式 Artifact。 |
| S01～S09 当前版本契约装载 | PASS（定向测试） | 测试从当前 Registry 读取九个版本、编译节点并建立带版本/指纹的契约；没有修改官方模板。 |
| SOP 与线性 DAG | PASS（定向测试） | 同一输入下，SOP 顺序祖先和 DAG 祖先构造相同的冻结要求和仲裁结果。 |
| 未获声明的新模板字段 | PASS（单元测试） | 新版本未声明的字段进入 pending-verification；显式新版本契约则按其冻结声明生效。 |

这不是九个场景的真实模型或外部业务集成验收。没有连接模型、MCP、Redis、PostgreSQL、知识库或业务 API。

## 6. 定向测试结果

### V1 与 clarification

命令：

```powershell
.venv\Scripts\python.exe -m pytest tests/task_v1_information_contract_test.py tests/task_clarification_test.py -q --tb=short
```

结果：**28 passed、2 subtests passed**。

覆盖原 S01 两字段、模型不能新增 blocker、明确 required、条件 required、人工确认、防止模型自证、verifier 原文边界、生成/验收同契约、SOP/DAG 一致、Schema 确定性阻断、九场景当前版本/指纹以及空字段历史快照兼容。

### SOP、DAG、九场景和业务门禁回归

命令：

```powershell
.venv\Scripts\python.exe -m pytest tests/task_v1_information_contract_test.py tests/task_clarification_test.py tests/task_r7_nine_scenarios_integration_test.py tests/task_dag_e1_business_gate_test.py tests/task_dag_e2_non_linear_s01_test.py tests/task_sop_control_test.py tests/task_sop_template_retry_test.py tests/task_dag_retry_test.py tests/task_template_output_test.py tests/task_p7r1_output_contract_test.py tests/task_s02_lightweight_template_test.py tests/task_s02_v4_delivery_test.py tests/task_s07_rag_integration_test.py -q --tb=short
```

结果：**230 passed、2 failed、15 subtests passed**。

通过的选定集合包括九场景合成执行、九场景线性 DAG、S01 非线性 DAG、SOP/DAG 控制及重跑、Output Schema、S02 v5.1 草稿、S02 v4 交付和 S07 引用测试。失败的是：

1. `tests/task_p7r1_output_contract_test.py::TaskP7R1ServiceOutputContractTest::test_sop_finalizer_builds_citation_index_then_runs_full_validation`：测试文本在 `task-citations` 区块后有尾随正文，且 `task-claim-evidence` 区块截断；运行仍被 `TASK_EVIDENCE_LEDGER_FAILED` 阻断，不生成 Artifact。
2. `tests/task_p7r1_output_contract_test.py::TaskP7R1ServiceOutputContractTest::test_sop_rejected_repair_keeps_body_and_fails_closed`：测试输入缺少 citation JSON 且 Claim–Evidence JSON 截断；运行安全失败、不生成 Artifact，测试期待的 `task_protocol_repair` 元数据仍不存在。

这两项与 `task_r9a_business_gate_acceptance_report.md` 第 4.1 节记录的两项遗留 Claim–Evidence 失败相符。另一项历史 `task_s02_content_repair_test.py::test_s02_v2_repairs_only_the_mixed_occurrence_then_runs_full_ledger` 本次没有运行，继续按 R9-A 记录为 BLOCKED。本次没有运行完整 `tests/task_*.py`，因此不声明完整历史失败集合已逐项复验或无新增失败。

### S02 v5.1 单独确认

命令：

```powershell
.venv\Scripts\python.exe -m pytest tests/task_s02_lightweight_template_test.py -q --tb=short
```

结果：**6 passed**。该测试包含 `draft_unverified` 草稿交付检查。

### 静态检查

对本次编辑的 Python 文件运行 `python -m compileall -q`，通过。当前环境没有安装 Ruff（PATH 和 `.venv\Scripts\ruff.exe` 均不存在），未运行 Ruff。

## 7. 历史失败、业务门禁和风险

- 基线记录：R7 完整 Task 套件为 **433 passed、43 failed、18 skipped、30 subtests passed**；R9-A 完整套件为 **473 passed、3 failed、18 skipped、30 subtests passed**，R9-A 报告记录原 43 项中 40 项转为通过、3 项 Claim–Evidence/引用修复路径仍 BLOCKED。
- 本次较窄的相关回归复现其中两项，失败都保持 fail closed，没有误报成功，也没有正式产物。完整 Task 套件本阶段未重跑，故不宣称这 3 项当前逐项重现或失败集合已完整比较。本次没有为全绿而修改这些合同、业务模板、引用校验或断言。
- 本次较窄的相关回归复现其中两项，失败都保持 fail closed，没有误报成功，也没有正式产物。
- S02 v5.1 仍为 `draft_unverified`；S02/S07 的来源授权、Citation、Claim–Evidence 等门禁未关闭或降级。
- Schema、类型、输入绑定、来源/引用授权和 Artifact 格式仍由确定性 Task 校验负责。模型声称通过不是服务端证据。
- 条件必填的判定只能使用合同允许的字段和确定性比较；未解析条件会保留 `unresolved` 状态，不接受模型自行断言条件成立。
- 真实模型生成质量、隔离知识库、真实 Redis/PostgreSQL、多进程恢复及业务副作用未在本阶段测试。
- 旧 Run 未被查询或写入。本次代码不会更换它的冻结模板版本/节点快照；恢复前需通过现有 API 确认 Run 状态、节点状态及 expected version。

## 8. 修改文件与工作区保护

本阶段实际触及的路径：

- `examples/agent_service/task/_information_contract.py`（新增）
- `examples/agent_service/task/_models.py`
- `examples/agent_service/task/_clarification.py`
- `examples/agent_service/task/_agentscope_executor.py`
- `examples/agent_service/task/_service.py`
- `examples/agent_service/task/_template_models.py`
- `examples/agent_service/task/_template_compiler.py`
- `examples/agent_service/task/_template_validation.py`
- `tests/task_v1_information_contract_test.py`（新增）
- `tests/task_clarification_test.py`
- `examples/agent_service/task/task_v1_dynamic_contract_ai_verification_report.md`（本报告）

官方 S01～S09 模板文件、AgentScope 主框架、`main.py`、前端、依赖、全局配置、Migration 和其他业务模块均未在本阶段修改。工作区在阶段开始前已有大量 Task、前端、Sales Console、Docker 和测试改动；这些改动全部保留。全局 `git diff --name-only` / `git status --short` 会包含该既有状态，不能把其中路径全部归因于 Task-V1。本次新增/编辑仅限上述 Task 后端、Task 专属测试和本报告路径；未提交代码。

## 9. 生产准入

**NO-GO。** Task-V1 的信息要求边界已通过定向和选定回归，但完整 Task 套件未重跑；三项历史 Claim–Evidence 修复未闭环，且没有真实模型、外部来源、真实数据库或生产级副作用恢复证据。不得宣称生产就绪。
