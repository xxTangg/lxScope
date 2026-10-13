# P7-R3-F1：S02 v3 Schema 兼容修复报告

## 结论

代码修复和自动化回归：**PASS**。S02 v3.0.0 与新发布的 v3.1.0 都由同一 Task 适配器处理；对 `unverified` 条目仅规范化缺失的 `source_quotes: []`、`rule_ids: []`，然后重新执行 JSON Schema、S02 业务规则和现有授权证据校验。失败仍会阻断 DOCX。

真实登录、真实模型及真实知识库 Run：**NOT TESTED**。本轮没有可读取的完整 Run 原始 JSON，也未执行真实浏览器 Run。回归样本按用户提供的脱敏形状构造，不声称是 Run `1e8554673e1049fe9637567cddf09e27` 的逐字副本。

## Gate 0：根因与校验顺序

1. S02 v3.0.0 的最终输出 Schema 在 [s02_project_planning.v3.json](../templates/s02_project_planning.v3.json) 的 item 定义中，把 `kind`、`text`、`source_quotes`、`rule_ids` 全部列为 `required`。因此 `unverified` 项语义上没有来源或规则时，也必须显式输出两个空数组。原提示只要求“符合本节点 JSON Schema”，没有明确给出这两个空数组的写法。
2. `unverified_status`、`reason`、`expected_evidence_types` 不是该 Schema 的 `required` 项，但 S02 业务校验会强制要求它们。修复不会替模型补写这些业务字段；缺失仍在 `s02_business` 阶段失败。
3. 原校验顺序是严格 JSON 解析（重复键拒绝）→ JSON Schema → S02 业务校验 → 当前 Run 的 EvidencePolicy、来源唯一匹配、权限主体、内容哈希和连续原文检查 → 通用 Claim–Evidence 台账验证 → Artifact Writer。此次只在 JSON Schema 前加入两字段的有限规范化，后续顺序与门控不变。
4. 通用校验器 [_output_validation.py](../_output_validation.py) 原先只记录 `error.validator`，例如 `required`，没有保留 JSON Schema 的缺失属性描述；因此真实诊断不能指出具体字段。现在 S02 适配器在校验失败后按相同 Schema 再取错误路径，并安全展开缺失字段名及条目上下文。

脱敏复现夹具构造了 8 章、52 条内容，其中 8 条 `unverified` 都含 `kind`、`text`、状态、原因和期望证据类型，但缺少两个空数组。修复前，JSON Schema 返回 8 个 `required` 错误（每个条目的一条错误同时列出两个缺项；通用错误格式未显示字段名）。S02 适配器现报告全部 16 个字段路径，例如：

```text
chapters[7].items[1].source_quotes: missing_required_field (raw_error_type=required, stage=json_schema, chapter=8, item_index=1, item.kind=unverified)
chapters[7].items[1].rule_ids: missing_required_field (raw_error_type=required, stage=json_schema, chapter=8, item_index=1, item.kind=unverified)
```

## 修改内容

- [_s02_structured_output.py](../_s02_structured_output.py)：在严格 JSON 解析后，只对 `kind == "unverified"` 且确实缺字段的条目插入两个空数组。字段已存在时不改；类型错误不转换；非空来源或规则声明不删除。规范化后继续调用既有完整 Schema 与 S02 业务验证。Schema 失败诊断包括路径、章节号、条目索引、`item.kind`、缺失字段、原始错误类型和校验阶段。
- [_service.py](../_service.py)：把原始模型 JSON 和规范化变更记录保存在 `s02_v3_structured_output` 元数据中；成功节点结果保留已通过 Schema 的规范化业务对象。Evidence 快照仍来自当前 TaskRun，Artifact 仍受后续台账校验门控。
- [_scenario_evidence_policies.py](../_scenario_evidence_policies.py)、[_template_registry.py](../_template_registry.py)：注册 S02 v3.1.0 并将其设为当前版本；为 3.1.0 提供同一 S02 EvidencePolicy，并保留 3.0.0 精确版本解析。
- [s02_project_planning.v3_1.json](../templates/s02_project_planning.v3_1.json)：新版本明确要求每项输出 Schema 必填字段；`unverified` 使用空 `source_quotes`、`rule_ids` 数组并提供状态、原因、证据类型；无引用的分析/建议使用空数组；模型不得生成服务端 ID、授权、哈希、核验或文件状态。v3.1.0 的业务 Schema 与 v3.0.0 相同。
- [task_s02_v3_structured_output_test.py](../../../../tests/task_s02_v3_structured_output_test.py)、[task_template_version_test.py](../../../../tests/task_template_version_test.py)、[task_nine_scenarios_test.py](../../../../tests/task_nine_scenarios_test.py)：增加真实失败形状、规范化边界、诊断、完整 TaskService/SOP/Verifier/Artifact 门控及模板版本兼容回归。

## 安全边界与兼容性

规范化只把 `unverified` 条目中未声明的来源和规则列表解释为空列表；不改任何已经提供的值，不会为 `fact`、`rule`、`analysis` 或 `recommendation` 增补字段。若 `unverified` 提供非空 `source_quotes` 或 `rule_ids`，业务校验拒绝；若提供的数组类型错误，JSON Schema 拒绝。`text`、`unverified_status`、`reason`、`expected_evidence_types` 均不自动生成。

权限主体、Evidence ID、SHA-256、来源类型、原文连续匹配与歧义检查均未放宽或绕过。未授权 Evidence、哈希篡改和错误原文仍失败。语义支持仍标记为需要人工复核。

v1、v2 和已发布 v3.0.0 文件保持原样；v3.1.0 是独立模板文件。运行时兼容适配同时支持 v3.0.0 和 v3.1.0，以便历史 v3.0.0 TaskRun 使用同一安全修复。其他八个场景和自由 Planner 没有修改其模板或执行规则。

## 测试结果

- 定向测试：`tests/task_s02_v3_structured_output_test.py tests/task_template_version_test.py tests/task_nine_scenarios_test.py -q` — **35 passed**。
- Task 全量测试（`tests/task_*.py`）：**282 passed, 2 skipped, 21 subtests passed**，55.88 秒。
- `python -m compileall -q examples/agent_service/task tests` — **PASS**。
- `git diff --check` — **PASS**（仅有仓库现存 LF/CRLF 转换提示，无空白错误）。
- 52 条脱敏结构回归：规范化后完成完整来源及证据台账校验，测试 Artifact Writer 写入一份与 TaskRun 关联的演示 DOCX；移除必填业务字段或使用错误数组类型时拒绝且不生成成功产物。
- PostgreSQL：本轮没有声称真实数据库集成通过；全量测试中 2 项 skipped。

## 文件与未提交状态

本轮新增/修改文件均在 Task 模块或 `tests/task_*` 白名单内。工作区原有未提交和未跟踪内容均保留；未暂存、未提交、未推送。未修改 AgentScope 官方代码、知识库核心、认证、数据库迁移、Task 外业务模块或前端。

## 真实 Run 复验步骤

1. 让后端 Task 服务加载本次 Task 代码（若本地开发服务器未监视该目录，重启后端；未改前端，无需前端构建）。
2. 在真实登录环境使用当前授权的 S02 v3.1.0 模板创建新 Task，重新执行完整流程；不要重试失败的历史 Run。
3. 检查最终节点的 `s02_v3_structured_output`：确认 `normalization_changes` 记录每个补入的空数组，`raw_model_output` 保留原始模型 JSON，节点结构化结果包含规范化后的数组。
4. 确认后续 Evidence 台账验证通过后才生成 DOCX，并检查产物归属当前 TaskRun。若其他 Schema、来源、权限、哈希或原文检查失败，保留失败诊断，不将其归因于本次空数组兼容修复。

该复验尚未执行；真实 S02 v3.1.0 Run 和 DOCX 下载仍为 **NOT TESTED**。
