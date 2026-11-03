# P7-R4-F1：S02 v4 演示草稿证据关联与 DOCX 交付修复报告

## 验收摘要

- **协议修复：PASS（Task 自动化测试）**。复合声明现在可以通过明确的局部原文映射组装；不相关摘录不会作为有效引用保留。
- **严格模式：PASS（失败关闭回归）**。来源关联不成立时 Task 失败，不生成成功产物。
- **演示草稿 DOCX：PASS（本地序列化与 TaskService/SOP 测试）**。使用 Task 现有 Artifact 生成路径得到可打开的 DOCX，首段和页眉均有演示草稿标识；测试 Workspace Writer 是内存夹具。
- **真实登录、真实知识库和 Workspace 下载：NOT TESTED**。未使用或伪造用户会话。
- **原始 Run 脱敏载荷：BLOCKED**。当前仅有 Run ID、汇总计数和错误码，没有本地 Run 快照或逐条 Claim/Quote/Evidence 内容；因此回归复现了用户提供的诊断形状，不冒称逐字复现了 Run `6f9edbc90d2e49e799c1c7302529a3e3`。

## 根因与失败链

通用 Claim–Evidence 校验器在 [_evidence_contract.py](../_evidence_contract.py#L3662) 使用 `_lexically_associated(claim, quote)` 对整条 Claim 与每条 Quote 做确定性字符串关联。该检查先要求 Claim 中提取的全部数值也出现在同一条 Quote 中，再检查标识符或有意义的汉字双字词是否重叠。它不是语义蕴含判断。

v4.0 的结构化输出把一个业务条目的完整 `text` 作为一个 Claim，并将多个原文候选都绑定到同一 Claim。对 CL-0001、CL-0003、CL-0008 这类复合内容，单条原文可能只支持其中一个局部事实，却不含整条 Claim 的其他数字或用语。通用校验因此会把该 Binding 记为 `quote_unrelated_to_claim`；该 Binding 未进入可接受的 Binding 集合后，后续引用完整性循环又会为同一位置报告 `claim_binding_missing`。两条诊断是一条错误链的首错与派生错误，并不代表授权身份和内容哈希也失败。

用户提供的 Run 汇总与这条失败链一致：7 项 `quote_unrelated_to_claim` 派生出 7 项 `claim_binding_missing`。但现有信息未包含 C2、C3、C5～C8、C12 的原文摘录和对应 Claim，故本报告不能逐条判断这些 Quote 实际支持哪些数字或子句，也不能断言其中任何一条具有语义支持。

相关实现位置：

- `_lexically_associated`：[_evidence_contract.py:3662](../_evidence_contract.py#L3662)
- 首个 Quote 关联错误与派生 Binding 缺失：[_evidence_contract.py:2954](../_evidence_contract.py#L2954)、[_evidence_contract.py:2992](../_evidence_contract.py#L2992)
- v4.0/v4.1 服务端结构化结果分派：[_service.py:3340](../_service.py#L3340)
- S02 专用模板条款与模板适用性检查：[_s02_project_planning_evidence.py:407](../_s02_project_planning_evidence.py#L407)

## 两种 `passed` 的含义及文件门控

`s02_v4_delivery.passed=true` 表示 S02 适配器已通过对应版本的 JSON Schema、S02 业务形状、来源候选解析及结构组装。它不代表通用 Claim–Evidence 台账通过，也不证明语义支持充分。

`evidence_ledger.passed=true` 是另一道独立门槛，表示最终正文经过通用证据协议、Occurrence/Binding、引用编号、授权来源、哈希、逐字原文及 S02 专属检查。`semantic_support` 仍是 `requires_human_review`。

TaskService 只有在适配器结果和最终 Evidence Ledger 都为 `true` 时才设置交付状态并继续到 Artifact Writer；SOP 最终节点发现 Ledger 失败时会失败关闭。实现位置见 [_service.py:2383](../_service.py#L2383)、[_service.py:3644](../_service.py#L3644)、[_service.py:3982](../_service.py#L3982) 和 [_service.py:2469](../_service.py#L2469)。因此用户提供 Run 中 `s02_v4_delivery.passed=true` 但 `evidence_ledger.passed=false` 时不生成 DOCX 是当前设计的预期保护行为。

本次测试还注入了 Ledger 失败，确认节点元数据同时保留 `s02_v4_delivery.passed=true` 和 `evidence_ledger.passed=false`，Run 为失败，产物列表为空，Writer 没有被调用。失败不会被改写为 `evidence_ledger.passed=true`。

## 修复与版本兼容

### S02 v4.1 输出协议

新增不可变版本模板 [`s02_project_planning.v4_1.json`](../templates/s02_project_planning.v4_1.json#L1)。当前 Registry 将 S02 当前版本指向 `4.1.0`；v1、v2、v3 和 v4.0.0 文件及既有 TaskRun 仍按各自保存的版本解析。v4.1 保留原有业务节点数量，不增加 Agent 或格式化 SOP 节点。

v4.0 的来源候选是字符串数组；v4.1 的候选为 `{quote, supports}`：

- `quote` 必须是本次 TaskRun 授权 Evidence 原文中的连续原文。
- `supports` 必须是当前业务条目 `text` 中唯一出现的连续原文子串，指明该 Quote 对应的最小完整声明。
- 多个局部声明可以分别提供来源候选，服务端据唯一位置拆成独立 Claim/Occurrence；同一局部声明可有多个 Quote。
- 局部声明必须无重叠并覆盖正文中的全部有意义字符，避免只映射数字或关键词。
- 每个 Quote 仍交由现有 `_resolve_quote` 校验来源类型、当前 Run Evidence、主体授权、文档身份、哈希和原文，然后对局部声明执行现有确定性关联检查。
- Task 继续使用现有 Claim–Evidence 组装器和通用/S02 Ledger；没有第二套 Evidence Store，也没有语义蕴含判定。

实现位于 [_s02_structured_output.py:724](../_s02_structured_output.py#L724) 与 [_s02_structured_output.py:1247](../_s02_structured_output.py#L1247)。v4.1 的版本特定模板规则识别见 [_s02_project_planning_evidence.py:407](../_s02_project_planning_evidence.py#L407)。

### 演示草稿安全降级

仅当用户显式选择 `demo_draft` 时，v4.1 才可把能够明确定位的来源候选缺失、未命中、歧义或局部关联失败转为待核验内容。无效 Quote 不进入引用目录、Claim Binding 或“已核验”台账；必引事实保留为独立 `unverified` 声明，并记录状态、原因、章节、条目和诊断 warning。原授权 Evidence 快照不被修改。

JSON/Schema 错误、重复 JSON 键、映射无法安全定位、来源身份/哈希异常及授权失败仍会阻止交付。严格模式遇到 Quote 关联失败也直接失败。演示状态必须是 `draft_unverified`；它不等于完整校验成功。DOCX 复用现有 `generate_artifact`、`render_docx` 和 `mark_docx_draft`，并在首页及页眉写入 `DEMO/SYNTHETIC · 演示草稿 · 未完成完整证据核验`。授权检查和 Artifact Writer 的用户隔离仍使用现有 Task 路径。

## 其他场景数据隔离检查

代码检查没有发现 Task 将其他 Run 的节点输出并入当前 S02 执行历史：[_service.py:2810](../_service.py#L2810) 从当前 `TaskRunRecord.node_runs` 生成历史；AgentScope 执行器构造模型输入为当前节点消息与当前上下文，见 [_agentscope_executor.py:342](../_agentscope_executor.py#L342)、[_agentscope_executor.py:463](../_agentscope_executor.py#L463)。执行器会按当前用户、Agent 和 Session 加载会话配置，但生成请求没有把 Session 的历史消息作为模型输入。

这项静态检查不能证明目标 Run 的具体每条输入都来自哪里。Run 的完整初始输入、Prompt、知识库结果和 Session 消息快照未提供，因此不能凭 T1～T4、会议转写或专家状态等文字断定发生了跨 Run 泄漏；也不能排除用户输入或当前 Run 的合法上游输出本身包含这些内容。真实 Run 的输入来源审计仍为 **NOT TESTED**，本次未加关键词过滤，也未改上下文组装。

## 回归与验证

新增/扩展的关键测试位于 [`task_s02_v4_delivery_test.py`](../../../../tests/task_s02_v4_delivery_test.py#L545)：

- 脱敏诊断形状夹具按报告汇总构造 54 个 Claim/Occurrence、18 个 Binding、29 个待核实项和 16 个 Citation；旧整条 Claim 匹配产生 7 个 `quote_unrelated_to_claim` 与 7 个 `claim_binding_missing`。它复现的是计数和错误类型，不是原始 Run 文本。
- 同一复合条目的多个 Quote 对应不同精确 `supports` 子串后，会拆成独立 Claim/Occurrence；每个局部 Quote 单独检查。
- 不相关 Quote 在 `demo_draft` 中不再显示为已验证，相关声明转为待核验；同一载荷在 `strict_verified` 下阻止 Run 和 DOCX。
- 身份主体和内容哈希错误仍硬失败。
- TaskService → SOP → Evidence Ledger → Artifact 集成测试验证演示 DOCX 为可打开的 DOCX ZIP 文件，首页、页眉含演示标签，Artifact 关联正确 Run；独立 Ledger 失败不会写文件。
- 版本测试检查 v4.0 与 v4.1 使用不同 Contract ID，且 v1/v2 历史定义未被本次测试替换；九场景回归覆盖模板 Registry。

实际验证结果：

- 定向：`31 passed`
- Task 全量：`305 passed, 2 skipped, 21 subtests passed`
- Task Python `compileall`：PASS
- Task 前端 ESLint：PASS
- `pnpm build`：PASS；构建输出包含现有大 chunk / 插件计时告警
- `git diff --check`：PASS；仅有 LF/CRLF 工作副本提示
- 2 个 PostgreSQL 跳过项：NOT TESTED（没有把它们算作真实 PostgreSQL 集成通过）
- 真实 Agent/模型、登录知识库检索、真实 Workspace 写入和授权下载：NOT TESTED

## 本次涉及的文件

- `examples/agent_service/task/templates/s02_project_planning.v4_1.json`：发布新的来源候选协议，不覆盖 v4.0。
- `examples/agent_service/task/_s02_structured_output.py`：支持 v4.1 局部声明映射、确定性拆分、安全降级及诊断。
- `examples/agent_service/task/_s02_project_planning_evidence.py`：让 v4.1 使用类型化 Claim 与模板适用性规则检查。
- `examples/agent_service/task/_scenario_evidence_policies.py`、`_template_registry.py`：登记 v4.1 版本和对应既有策略。
- `examples/agent_service/task/_service.py`：分派 v4.1 适配器、保留独立 Ledger 门控与正确的输出 Contract 目标。
- `examples/web_ui/frontend/src/pages/task/index.tsx`、`task-run-panel.tsx`：将 v4.1 纳入演示交付展示；修正可空版本号的类型缩窄。
- `tests/task_s02_v4_delivery_test.py`、`task_template_version_test.py`、`task_nine_scenarios_test.py`：覆盖关联、门控、版本与回归。

工作区原有其他修改均保留；没有暂存、提交或推送。此次代码改动仅在 Task 后端、Task 专属前端、Task 专项测试和 Task 文档范围内。

## 真实登录复验步骤

1. 部署包含 Task v4.1 代码的后端，并重启/热重载后端进程，使新版本模板和适配器被加载；前端若由静态构建产物提供，部署本次通过的 `pnpm build` 产物。
2. 使用真实登录用户创建新的 S02 Task，确认实例保存版本为 `4.1.0`。已创建的 v4.0 Task 不会自动迁移。
3. 选择该用户有权访问的真实知识库和项目资料，运行 `demo_draft`。
4. 检查最终节点分别显示 `s02_v4_delivery.passed`、warning/待核验项及 `evidence_ledger.passed`。成功交付要求 Ledger 自身真实通过；若 Ledger 为 false，Run 必须失败且不生成 DOCX。
5. 在同一用户会话中下载实际 Artifact，核对 Run ID 关联、`draft_unverified` 状态、首页/页眉标识及内容；语义支持仍由人工复核。
6. 以 `strict_verified` 重跑合法业务输入，验证任何未解决必引来源关联仍失败关闭且没有成功产物。
