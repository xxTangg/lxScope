# P7-R4：S02 Task 模板业务化重构与演示闭环优化报告

日期：2026-10-13
分支：`integrate/dev_l`

## 结论

S02 已新增不可变版本 `4.0.0`，将 Agent 负责的业务内容与 Task 服务端负责的授权来源解析、引用台账和文件交付分开。默认交付模式仍为 `strict_verified`；用户只有在标准 S02 v4 Task 中显式选择 `demo_draft`，才可生成醒目标记的演示草稿。演示模式不会跳过身份、授权、来源身份或内容哈希检查，也不会把语义支持标成自动通过。

自动化 Task 回归和 Task 前端构建通过。测试实际生成了带草稿标记的 DOCX；未连接真实登录会话运行新 S02 v4，也未在真实 Workspace 中验证下载，因此真实 RAG/模型联调和真实下载仍为 **NOT TESTED**。

## 原 S02 模板的问题与根因

S02 v3.2 的最终节点要求 Agent 同时生成业务正文以及 Claim、Occurrence、Citation、Binding、Unverified 等底层证据协议。真实运行 `772b8f77ccb443f0988ae4a3f9755caf` 已生成八章、47 条业务内容，B-01～B-15 均独立出现；但校验器把 analysis、unverified、template limitation 中自然提及 B 编号的文字误识别为规则声明，导致最终验收失败并阻止 DOCX 生成。

根因是模型内容分类与通用证据协议耦合过紧：模型要负责协议标识和位置关联，业务规则扫描又曾以正文中是否出现 B 编号作为判定依据。授权 Evidence 校验、来源哈希和权限校验本身仍有必要，本次没有关闭或降低这些校验。

## 新旧模板流程

| 项目 | v3.2 及更早版本 | v4.0.0 |
|---|---|---|
| 模板历史 | 保留原有版本和既有运行语义 | 新注册版本，不覆盖历史文件 |
| SOP | 七步业务流程，最终 Agent 输出正文和复杂证据协议 | 六个可见步骤：读取项目资料、检索模板、推荐大纲、生成初稿、用户复核、整理输出 |
| Agent 输出 | Markdown 与 Claim/Evidence 关联协议 | 八章业务 JSON 和逐条来源候选；不生成服务端证据身份 |
| 证据组装 | Agent 直接承担多项协议字段 | Task 服务端从当前 Run 的授权 Evidence 快照确定性匹配并组装现有 Claim–Occurrence 协议 |
| 文件交付 | 既有 Artifact Writer | 继续复用既有 Artifact Writer，仅在校验/草稿状态满足后生成 DOCX |

模板保留八章策划要求。B-01～B-15 作为写作约束使用，不要求正文机械生成十五条 `rule` 对象；自然语言分析、建议或待核实内容提及 B 编号不会改变该条目的类型。规则覆盖仍独立检查，模板适用性说明不能替代任一 B 条款。模板规则的来源仅能来自本次 Run 中授权且有效的策划模板证据。

## 精简后的输出协议与职责

最终 Agent 返回 `title` 和按 1–8 顺序排列的 `chapters`。每个普通业务条目包含 `kind`、`text`、`source_candidates`；`kind` 支持 `fact`、`historical_case`、`analysis`、`recommendation`、`template_limitation`。`unverified` 条目另需 `pending_status`、原因和期望证据类型，且没有来源候选。Schema 限定章节数、条目数量、字段、长度和枚举。

Agent 不得生成 Claim/Occurrence/Citation/Evidence ID、knowledge base/document ID、授权主体、哈希、核验通过状态、审批/签发结论或文件生成状态。Task 服务端：

1. 验证 Agent JSON 结构和业务类型。
2. 只在本次 TaskRun 授权 Evidence 快照中查找候选原文；候选必须与原文连续逐字匹配，并符合场景 EvidencePolicy。
3. 对唯一匹配的声明确定性生成现有 Claim、Occurrence、Citation、Binding 和 Unverified 记录，再调用通用来源及台账校验器。
4. 将模板 B 条款的来源覆盖与普通业务条目分类分开判断。
5. 保持语义支持为人工复核边界，不根据字符串匹配宣称业务结论成立。

候选无匹配、重复匹配或资料缺失时，严格模式拒绝完成交付；演示模式仅将可安全识别的来源缺口转为带原因的待核验项和 warning。来源越权、授权主体不一致、证据身份异常、哈希错误、文档未就绪等仍失败关闭，不能降级成 warning。

## 交付模式

| 模式 | 选择和范围 | 交付语义 |
|---|---|---|
| `strict_verified` | 默认值；所有任务均适用 | 现有严格 Evidence、引用、来源和业务校验失败时，不生成成功 DOCX。 |
| `demo_draft` | 必须由用户明确选择；仅适用于标准 S02 v4.0.0 模板实例 | 可对可识别的非关键来源缺口生成草稿及 warning；认证、知识库权限、租户隔离、来源身份、哈希、Workspace 文件写入仍强制执行。产物状态为 `draft_unverified`，不能冒充严格通过。 |

演示 DOCX 通过现有 Writer 的 DOCX 标记路径在首页和页眉声明 `DEMO/SYNTHETIC`、演示草稿和未完成完整核验。Run/Artifact 的交付状态及报告字段采用可选、向后兼容的快照字段，旧记录缺失这些字段时继续可读。只有文件确实写入成功后，服务端才保存对应交付状态；文件写入失败不会生成成功产物状态。未设置或无效的交付模式不被自动猜测为演示模式。

Task 页面仅在标准 S02 v4 模板中呈现模式选择，并以不同状态展示严格结果、演示草稿、八章完成情况、来源状态、warnings、待核验事项及现有 DOCX 下载项。其他场景和自由 Planner 始终走严格默认行为。

## 修改文件

本阶段相关文件均在允许的 Task 白名单内：

- `examples/agent_service/task/templates/s02_project_planning.v4.json`：新增 S02 v4 模板及受限业务 JSON Schema。
- `examples/agent_service/task/_template_registry.py`：注册 v4 为当前版本，保留历史版本解析。
- `examples/agent_service/task/_scenario_evidence_policies.py`：为 v4 声明模板限制及业务条目的 EvidencePolicy。
- `examples/agent_service/task/_s02_structured_output.py`：结构化输出校验、确定性候选来源解析、现有 Claim–Occurrence 协议物化和分级交付报告。
- `examples/agent_service/task/_s02_project_planning_evidence.py`：按声明的业务类型区分 B 规则和对规则的自然提及。
- `examples/agent_service/task/_models.py`、`_postgres_store.py`：增加可选的交付模式、状态、核验报告与 Artifact 状态快照字段；未增加数据库迁移。
- `examples/agent_service/task/_service.py`：限制演示模式适用范围，串接 v4 适配器、通用证据台账、Artifact Writer 和持久化状态。
- `examples/agent_service/task/_output_validation.py`：避免对已由 v4 适配器验证的原始 JSON 重复按已组装结果校验；Evidence 与台账校验仍然执行。
- `examples/agent_service/task/_artifacts.py`、`_office_renderers.py`：复用现有产物路径，在演示 DOCX 添加显式标记。
- `examples/web_ui/frontend/src/api/task.ts`、`examples/web_ui/frontend/src/pages/task/index.tsx`、`task-header.tsx`、`task-run-panel.tsx`：传递允许的模式，呈现 S02 v4 交付状态、证据台账和现有下载入口。
- `tests/task_s02_v4_delivery_test.py`：新增 v4 注册、业务分类、授权边界、SOP 到 Writer 的成功/失败测试。
- `tests/task_template_version_test.py`、`tests/task_s02_v3_structured_output_test.py`、`tests/task_nine_scenarios_test.py`：更新当前版本断言，并保留旧版本定向兼容验证。

工作区开始前已有多个 Task 修改和非本阶段未提交内容；未清理、暂存或提交这些文件。本阶段没有主动修改允许范围以外的源码。

## 测试与验证

| 验证 | 结果 |
|---|---|
| Task 全量测试（`python -m pytest <tests/task_*.py> -q`） | **297 passed, 2 skipped, 21 subtests passed**。 |
| 定向 S02 v4 + 九场景测试 | **12 passed**。 |
| S02 v3 结构化输出与模板历史定向测试 | **36 passed**。 |
| Task 前端 ESLint | **PASS**：`pnpm exec eslint src/api/task.ts src/pages/task/index.tsx src/pages/task/task-header.tsx src/pages/task/task-run-panel.tsx`。 |
| 前端生产构建 | **PASS**：`pnpm build`（`tsc -b && vite build`）；构建器报告大 chunk 提示，不影响构建成功。 |
| Task Python `compileall` | **PASS**。 |
| `git diff --check` | **PASS**；Git 提示现有工作树部分文件将发生 LF/CRLF 转换，不是空白错误。 |
| DOCX Writer | **测试夹具 PASS**：TaskService/SOP 路径实际调用现有 Writer，生成带演示标识且关联当前 Run 的 DOCX；严格模式来源缺失和 Workspace 写入失败均未标记成功。 |
| PostgreSQL 专项 | **NOT TESTED**：2 项因未配置一次性测试库变量 `LXSCOPE_TASK_TEST_DATABASE_URL` 而 skipped；未连接现有数据库。 |
| 真实登录、真实模型/RAG 和真实 Workspace 下载 | **NOT TESTED**：本轮没有在合法登录浏览器会话中启动真实 Run。 |

## 历史兼容

S02 v1、v2、v3.0、v3.1、v3.2 模板文件未被覆盖；Registry 继续可按版本读取历史定义。自由 Planner、其他场景的默认交付模式仍是 `strict_verified`。TaskRun 和 Artifact 新增状态字段可选，不需要迁移历史记录。自动化回归结果没有发现其他八个场景或自由任务回归。

## 真实演示验收步骤

1. 部署 Task 后端和 Task 前端本阶段代码，并以真实用户登录。
2. 创建标准系统模板“项目策划”`4.0.0` Task，选择该用户获授权的知识库/资料，确认 Preflight 和实际检索使用同一授权主体。
3. 先以默认 `strict_verified` 运行；确认来源齐全时报告和 DOCX 都通过完整校验。来源不足时确认严格模式不生成成功产物。
4. 若仅需演示且业务人员知情，明确选择 `demo_draft` 并重新创建/运行 Task。检查 DOCX 首页和页眉的演示标识、Run 的 `draft_unverified` 状态、warning、来源清单与待核实事项。
5. 在 Task 页面下载 DOCX，验证下载仍需当前用户权限，且文件与该 TaskRun 关联。
6. 分别尝试未经授权来源、失效/不存在文档、身份/哈希不匹配；确认仍失败关闭。记录脱敏 Run ID、节点状态、真实产物和下载响应。

## 风险与剩余事项

- JSON Schema、唯一候选匹配和原文哈希验证可以保证结构、来源完整性和授权边界，不能自动证明证据在语义上充分支持业务结论；报告仍需人工复核。
- 演示模式不是正式验收或审批，不能将 `draft_unverified` 文件当作正式报告或已核验结果流转。
- 本轮没有真实登录浏览器与业务知识库会话，真实模型适配、RAG 内容质量、用户 Workspace 读写和授权下载仍待环境验收。
- PostgreSQL 适配字段进行了 Task 专项自动化检查，但真实数据库持久化需在独立的一次性数据库环境补验。

本阶段完成后停止；未提交或推送代码。
