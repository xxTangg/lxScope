# P7-R3-F2：S02 v3 类型判别与业务 Schema 一致性修复报告

## 验收结论

- **代码与 Task 自动化回归：PASS**。S02 当前目录版本已调整为不可变的新版本 `3.2.0`；`3.1.0` 及更早版本仍保留原模板文件和版本解析。
- **真实 Run `a763efd712d44662a31abcc860ebeb57`：NOT TESTED**。工作区没有该 Run 的完整模型响应或 Run 快照，本次夹具是按用户提供的 8 章、39 项、31 个普通条目和 8 个待处理条目构造的脱敏代表形状，不是该 Run 的逐字载荷。
- **真实模型、授权 RAG、真实 Workspace/DOCX 下载：NOT TESTED**。本轮只验证本地 Task 代码和自动化测试。
- **当前真实 Task 实例的 `source_template_version`：未能核实**。本地文件没有该 Run ID，也没有通过已登录 Task API 读取实例。本次只确认注册表新建默认版本为 `3.2.0`。

## Gate 0：根因

### 1. 普通条目与待处理字段共用宽松 Schema

S02 v3.1.0 的最终节点 Prompt 在 `templates/s02_project_planning.v3_1.json:184` 要求区分类型，但没有明确禁止普通条目输出待处理字段。其 item Schema 在 `s02_project_planning.v3_1.json:244-330` 使用一个通用对象：`kind` 可选多个类型，`unverified_status`、`reason`、`expected_evidence_types` 也都在同一个 `properties` 中；它们不是按 `kind` 条件限制的字段。因此 31 个普通条目带这三项时，JSON Schema 仍可通过，之后才被 `_validate_business_shape()` 的非 `unverified` 字段检查拒绝。

“来源语义支持需人工复核”属于 Evidence 验证结果，不等于“没有来源、待核实”。原模型把语义复核状态写进了业务 `unverified_status`。Task 的语义复核范围由服务端 `EvidencePolicy.manual_review_claim_kinds` 决定，见 `_evidence_contract.py:3133`；它不会要求模型把事实改成待核实事项。

### 2. `rule` 条目可以把多条 B 要求混在一项里

v3.1 的 `rule_ids` 仅限制数组最多一项，没有按 `kind` 收紧到恰好一个 ID，也没有 Schema 约束正文只能含一个 B 编号。故第八章将模板适用性限制伪装为 `kind=rule`、`rule_ids=[]` 并同时提及 B-01 至 B-15，未能在 Schema 阶段被拒绝；原业务校验器随后才发现 `rule` 声明不合法或 B 规则无法一一覆盖。

### 3. 模板适用性声明不属于 B 条款

“演示模板不是正式制度/标准”描述的是来源性质和适用范围，不是 B-01 至 B-15 的独立编写规则。将它塞进 `rule` 会把不同业务语义混为一类；删除该声明又会丢失有来源支持的重要限制。本次为该内容增加专用 `template_limitation` 类型，并把它的来源类型限制为本次 Run 授权的 `planning_template` Evidence。

### 4. Run 版本核验边界

模板实例化服务将选定模板版本写入 `source_template_version`，见 `_template_service.py:151`；版本字段保存在 Task/Run 模型中，见 `_models.py:572`、`_models.py:645`。但本轮没有该 Run 的持久化记录或合法登录 API 读取能力，因此不能断言这次失败的实例实际绑定了 `3.1.0`。修改不会迁移旧 Task/Run；只有新建的当前模板实例默认使用 `3.2.0`。

## Gate 1：Schema 与业务校验统一

新模板 [s02_project_planning.v3_2.json](../templates/s02_project_planning.v3_2.json) 采用 `oneOf` 类型分支（模板第 4 行版本号、第 184 行最终节点 Prompt、第 246 行起 item 分支），各分支均使用封闭对象，未声明字段会被拒绝：

| 条目类型 | 允许字段 | 关键约束 |
|---|---|---|
| `fact`、`historical_case`、`analysis`、`recommendation` | `kind`、`text`、`source_quotes`、`rule_ids` | 不允许 `unverified_status`、`reason`、`expected_evidence_types`；`rule_ids` 必须为空。事实/历史案例的引用要求继续由现有 EvidencePolicy 判定。 |
| `rule` | 上述四字段 | Schema 要求 `rule_ids` 恰好一个；v3.2 业务校验要求正文与来源摘录对应同一个 B-01 至 B-15 条款、正文只出现一个 B 编号，并继续检查全部 15 条覆盖。 |
| `template_limitation` | 上述四字段 | `source_quotes` 至少一项，`rule_ids` 为空；不得包含 B 编号。后续来源解析只接受授权 `planning_template` Evidence。 |
| `unverified` | 四字段及三项待处理字段 | 必须显式提供 `source_quotes: []`、`rule_ids: []`、有效状态、原因和期望证据类型；状态只允许三种既有值。 |

`_output_validation.py:37`、`:184-285` 为 Task 安全 Schema 子集增加了有界 `oneOf` 校验（最多 8 分支，继续受 Schema 深度/节点/字节限制）；`:382-427` 将分支说明提供给生成提示。Schema、Prompt 和 `_validate_business_shape()`（`_s02_structured_output.py:710-884`）现在使用同一类型划分。结构错误在 `_s02_structured_output.py:418-653` 生成带章节号、条目索引、`kind`、字段、原始错误类型及校验阶段的诊断。服务端节点错误展示部分摘要，完整诊断仍保存在结构化输出元数据中，见 `_service.py:3148-3235`。

F1 安全规范化仍保留于 `_s02_structured_output.py:382-416`：只对 `kind=unverified` 且字段缺失的 `source_quotes` / `rule_ids` 补空数组；已存在字段不改，类型错误不转换，非空来源声明不删除。之后完整重新执行新 Schema、S02 业务校验和来源校验。

## Gate 2：Prompt 与来源复核语义

v3.2.0 最终节点 Prompt（模板第 184 行）明确禁止普通条目带待处理字段；解释 `requires_human_review` 不等于 `unverified`；列出五种普通内容与 `unverified`、`template_limitation` 的最小字段形状；要求逐字引用候选来自上游授权原文；禁止模型生成 Claim/Occurrence/Citation/Evidence ID、文档 ID、授权主体、哈希、核验通过或产物状态。不存在追加重试或让模型重复生成整份报告的补丁。

服务端 Evidence 组装保持唯一来源边界：S02 v3.2 Policy 只为 `template_limitation` 增加 `planning_template` 来源类型及必须引用/人工复核策略（`_scenario_evidence_policies.py:121-141`）；没有此显式场景策略时，通用验证器会拒绝该 Claim kind（`_evidence_contract.py:2496-2511`）。授权身份、Evidence ID、允许来源类型、原文连续匹配及适用的内容哈希验证仍沿用既有通用路径，见 `_evidence_contract.py:2917-2950`、`:3285`。语义蕴含仍标记为人工复核，不作为自动通过条件。

## Gate 3：模板适用性与 B 条款

- B-01 至 B-15 保持为 15 个各自独立的 `rule` 条目。`rule_ids` 恰好一项，正文不能将多个 B 编号合并；每条原文摘录需包含对应 B 编号及具体条款内容。
- 模板来源/适用性限制以 `template_limitation` 表达，必须提供候选原文；服务端只会从当前 Run Evidence 快照中唯一解析到 `planning_template` 来源。零匹配、重复匹配、权限不符、哈希错误或摘录不匹配均失败关闭。
- 这没有把模板限制归属于某个 B 条款，也没有放宽 B-01 至 B-15 覆盖要求。

## 版本和修改范围

- 新增 `examples/agent_service/task/templates/s02_project_planning.v3_2.json`，版本 `3.2.0`。
- `_template_registry.py:18-22` 注册 S02 1.0、2.0、3.0、3.1、3.2；`:53-55` 将 3.2.0 设为当前展示/新建版本。旧模板文件未覆盖，旧 Task/Run 不迁移。
- Task 内实现文件：`_output_validation.py`、`_evidence_contract.py`、`_scenario_evidence_policies.py`、`_s02_structured_output.py`、`_service.py`、`_template_registry.py`。
- 专项测试文件：`tests/task_s02_v3_structured_output_test.py`、`tests/task_evidence_contract_test.py`、`tests/task_template_output_test.py`、`tests/task_template_version_test.py`、`tests/task_nine_scenarios_test.py`。
- 未修改前端；未修改 AgentScope、官方 SOP、共享认证、知识库核心、`main.py`、销售台、全局导航、迁移或依赖。保留工作区已有无关修改；未暂存、提交或推送。

## 测试与结果

- Task 全量：`& .\.venv\Scripts\python.exe -m pytest (Get-ChildItem tests -File -Filter 'task_*.py' | ForEach-Object { $_.FullName }) -q` — **288 passed, 2 skipped, 21 subtests passed**，62.11 秒。
- 定向：结构化输出、通用 Evidence、模板版本、Schema 输出和九场景测试 — **67 passed**。
- `python -m compileall -q examples/agent_service/task tests` — **PASS**。
- `git diff --check` — **PASS**；Git 仅提示现存 LF/CRLF 转换信息，没有空白错误。
- 2 项 PostgreSQL 测试因未设置 `LXSCOPE_TASK_TEST_DATABASE_URL` 而跳过；未连接未知或生产数据库。
- 代表性 39 项夹具：31 个非 `unverified` 条目各携带 3 个非法待处理字段，Schema 阶段产生 93 条具体字段诊断；合并多个 B 编号的 rule 也在 Schema 阶段失败。TaskService/SOP 回归确认该失败不会调用 Artifact Writer。
- 合法模板限制回归确认：有唯一授权模板 Evidence 时由服务端生成对应引用；重复候选、错误授权主体及哈希篡改拒绝。历史 v3.0.0、v3.1.0 执行回归通过；注册表测试覆盖旧模板版本解析。

## 真实浏览器复验步骤

1. 重新加载后端 Task 代码；若后端未对 Task 源码热重载，重启后端服务。未改前端，无需 `pnpm build` 或重建前端镜像。
2. 通过真实登录页面新建当前“项目策划”模板 Task，并确认新实例的 `source_template_version` 为 `3.2.0`。失败 Run 和旧 3.1.0 Task 不会被本次修改迁移，请勿把重试旧 Run 当成本版本验收。
3. 使用当前用户有权访问且已索引的项目资料、策划模板运行完整流程。
4. 检查最终节点：普通条目不再出现待处理字段；没有来源的项目事实保留为单独 `unverified`；模板适用性限制作为独立 `template_limitation` 并引用模板原文；B-01 至 B-15 分别各一条。
5. 只有 Evidence 校验和后续证据台账均通过时才应生成 DOCX。检查 Artifact 与新 Run 关联、可由当前用户授权下载。语义支持仍需人工复核。

本次未执行真实 Run；自动化测试结果不能替代真实模型、授权知识库和 Workspace 验收。
