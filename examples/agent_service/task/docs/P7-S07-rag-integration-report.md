# P7-S07：报告生成 RAG Tool 接入与知识库授权修复报告

日期：2026-10-12
分支：`integrate/dev_l`
工作区基准：`1418f2b`（未提交；本阶段未提交代码）

## 验收结论

- **Task 原生 RAG 绑定：PASS。** `report.rag_retrieve` 已有 TaskKnowledgeGateway 运行适配器，本次修正了能力登记，使模板编译和 Preflight 不再要求 Agent 会话 Toolkit 另行提供同名外部 Tool。
- **来源授权错误分类：PASS。** 选定且可访问的知识库内找不到文档、文档尚未完成索引、知识库本身不可访问现在分别返回不同状态与错误码；没有绕过权限或放宽失败关闭。
- **同一 Run 引用核验：PASS（自动化协议测试）。** `report.citation_check` 不再重复检索或重复检查资料 ID；运行时仍只使用本次 Run 前置 RAG 节点保存的授权 Evidence 快照。
- **真实登录知识库联调：BLOCKED。** 当前工作区没有可供本轮调用的合法登录会话、真实 S07 Task/Run 输入及授权知识库响应。用户提供的 Preflight 错误可以支持定位分类问题，但没有包含实际 `source_material_ids` 或服务端文档状态，不能据此断定本次真实 Run 的具体 ID 原因。隔离测试不作为生产 RAG 通过证据。

## Gate 0：根因与现有能力

### `report.rag_retrieve`

S07 当前 Registry 版本为 v3.0.0，检索节点位于 `templates/s07_report_generation.v3.json`，能力键为 `report.rag_retrieve`，参数绑定为 `request={{previous_output}}`。同一模板 v1/v2 文件保持原样。

Task 已有真实适配路径：`_scenario_runtime.py` 将 `report.rag_retrieve` 归为授权 RAG；`_service.py` 的 SOP 执行回调将该 ToolStep 路由到 `retrieve_scenario_evidence`，后者调用 `TaskKnowledgeGateway.retrieve_task_evidence`，按当前 `user_id`、Task 所选 KB 与指定资料 ID 检索，并从服务端检索结果保留 `knowledge_base_id/name`、`document_id`、`chunk_index`、`content_sha256`、原文及 ready 状态，再规范化为带当前 `authorization_subject` 的 Evidence 快照。

故障不是缺少 RAG 实现，而是 `_capability_contracts.py` 的 `_TASK_NATIVE_CAPABILITIES` 漏登了 `report.rag_retrieve`。模板编译、原生 Tool Schema 注入和 `_needs_external_tool_discovery` 都依赖此登记；漏登后 Preflight 把 Task 自己实现的能力当成 Agent Toolkit 外部 Tool，所以显示 `TASK_TOOL_MISSING`。现已补齐登记和 `request` 字段 Schema。运行时仍由 Task Service 控制，不要求向 AgentScope Toolkit 注册虚构工具。

### `report.citation_check` 与资料 ID

`report.citation_check` 已登记为 Task 原生能力。Preflight 先确认它前面存在授权 RAG 节点，再由 `_retrieval_result_for_run` 收集本次 Run 中位于校验节点之前的实际检索结果；`verify_task_citations` 使用当前 Run 用户身份检查来源和逐字摘录。模型输出、用户手填的来源 ID 或其他 Run 的 Evidence 都不能替代该快照。

此前 Preflight 对 RAG 和 citation check 重复调用 `inspect_task_scope`。当前 RAG 节点负责一次性检查所选知识库和该 Task 声明的 `source_material_ids`；citation check 预检改为确认 Task 内校验器可用及其前置 RAG 依赖。此项不会让无证据的运行通过：RAG 执行失败会停止 SOP；citation 校验执行时仍要求本次 Run 的 RAG 结果。

Gateway 对资料 ID 的检查来自 `task.template_inputs.source_material_ids`，并在运行时合并本次 InputStep 显式提交的资料范围。当前知识库 API 的 `get_document_status` 会省略不存在或不属于查询范围的记录。因此本轮将旧的含混错误拆为：

| 情况 | 现在的结果 |
|---|---|
| 知识库不存在或当前用户不能访问 | `knowledge_base_unavailable`；Preflight `TASK_KNOWLEDGE_PERMISSION_DENIED` |
| 文档 ID 未在当前用户可访问的所选 KB 中找到 | `source_material_not_found`；Preflight `TASK_KNOWLEDGE_SOURCE_NOT_FOUND`，类别为 `data_source_not_ready` |
| 文档存在但尚未 `ready` | `source_material_not_ready`；Preflight `TASK_KNOWLEDGE_DOCUMENT_NOT_READY` |
| 知识库/索引服务不能确认状态 | 保持失败关闭，报告数据源未就绪 |

Task 不探测用户不可访问的其他知识库，因此“未在当前可访问的所选知识库找到”不会声称该 ID 在全系统绝对不存在，也不会泄露它是否存在于其他 KB。此前 `source_material_unavailable` 把“所选 KB 内未找到”直接归为权限拒绝，是 UI 显示错误的原因之一。

当前 DEMO 输入 `docs/p7b-demo-data/inputs/s07-report-generation.v3.json` 明确含占位值 `__REPLACE_WITH_SERVER_ISSUED_AUTHORIZED_DOCUMENT_ID__`。如果真实 Task 实例化时使用了这个值，它会被正确拒绝为当前选定 KB 中未找到资料；它不是服务端 `document_id`。仅凭用户提供的错误摘要无法确认本次运行是否使用了该占位值。

### RAG 复用与 Workspace 边界

S07 复用 S02 已用的 `TaskKnowledgeGateway`、TaskRun Evidence 快照和 Claim–Evidence/引用校验协议；没有新增 RAG、Evidence Store 或知识库访问路径。S05/S06 可使用 Task Workspace Artifact Writer 生成和读取授权产物，但 Task 当前没有将 Workspace 产物作为 RAG 文档检索的授权适配器；本阶段未把这些文件伪装为 KB 来源。

## 实际修复

- `examples/agent_service/task/_capability_contracts.py`：将 `report.rag_retrieve` 加入 Task 原生能力登记，使模板编译、Task Tool Schema 和 Preflight 走本地适配器路径。
- `examples/agent_service/task/_scenario_runtime.py`：为原生检索声明严格的 `{request: string}` Tool Schema；不新增检索行为或权限范围。
- `examples/agent_service/task/_knowledge.py`：将可访问所选 KB 中未命中的资料 ID 标记为 `source_material_not_found`，并提供缺失计数；不回传、搜索或推测其他用户资料。
- `examples/agent_service/task/_service.py`：细分 Preflight 状态和错误码；citation check 不重复调用资料范围检查，提示它只验证本次 Run 的前序 RAG 快照。
- `examples/agent_service/task/_errors.py`：将真实 KB 不可访问归为权限错误；文档未找到/未就绪归为数据源问题。
- `tests/task_project_planning_evidence_chain_test.py`：更新现有 TaskKnowledgeGateway 测试，使资料未命中使用新的精确内部错误码。
- `tests/task_s07_rag_integration_test.py`：新增 S07 原生绑定、会话不需外部 Tool、KB/文档状态区分、当前 Run Evidence 绑定及错误分类测试。
- 本报告：记录本阶段根因、修改、测试和真实联调边界。

没有修改 S07 v1/v2/v3 模板文件、S02 模板、Task 前端、共享知识库/认证/API、AgentScope 官方代码、依赖、迁移或 Task 外业务模块。模板输入和执行语义没有改变，不需要发布新模板版本。

## 验证结果

| 验证 | 结果 |
|---|---|
| 定向 S07、S02 RAG 与九场景运行测试（首次修正后） | **PASS** — 35 passed |
| Task 全量 pytest | **PASS** — 229 passed、2 skipped、13 subtests passed；其中 2 个 PostgreSQL 项因测试库未配置而跳过，不算真实数据库集成通过 |
| Python `compileall`（Task 源码及 tests） | **PASS** |
| Task 前端 ESLint（`src/pages/task`、`src/api/task.ts`） | **PASS** |
| 前端 `pnpm build` | **PASS** — TypeScript 与 Vite 构建完成；有既有大 chunk 体积提示 |
| `git diff --check` | **PASS** — 仅有工作区现有 CRLF 转换提示，无空白错误 |
| 真实 S07 Preflight / 知识库 / 模型 / DOCX 下载 | **BLOCKED** — 本轮没有合法登录会话和真实用户 KB/Run；DOCX 仅由现有 Task 自动化测试覆盖 |

最后一项“未索引资料 Preflight 错误码”回归已纳入最终全量测试。S07 新增专项测试共 7 项，均通过。

## 用户重新验证步骤

1. 重载后端 Task 服务，使新的 Task 原生能力登记和错误分类生效。
2. 在当前登录用户可访问的知识库页面选定一份已完成索引的报告资料，复制该 KB 返回的真实 `document_id`；不要使用 DEMO 文件里的占位字符串、文件名或本地路径。
3. 若现有 Task 的模板参数已经保存了错误 ID，创建新的 S07 v3.0.0 Task，并在实例化参数中填写 `source_material_ids: ["<真实 document_id>"]`，同时选择对应授权知识库。不要只在之后的输入步骤追加正确 ID，因为已声明的资料范围会一并接受检查。
4. 重新运行 Preflight。正确的绑定应显示 `report.rag_retrieve` 为 Task 原生可用，`report.citation_check` 为 Task 内引用校验可用，`document.render` 继续使用现有 Workspace 检查。若资料状态分别不匹配，预期看到上述 `SOURCE_NOT_FOUND`、`DOCUMENT_NOT_READY` 或 `PERMISSION_DENIED` 的具体提示。
5. Preflight 通过后运行 Task，检查 RAG 节点保存的实际文档/片段/哈希及当前 Run Evidence；再确认 citation check 只引用这些证据。语义支持仍需人工复核。最终 DOCX/下载仍须由真实登录浏览器验收，本轮没有声称通过。

## 剩余阻塞

- 需要实际测试用户、所选 KB ID、服务端返回的文档 ID/状态及 S07 Run ID，才能确认用户报告中那次资料失败的精确源数据原因。
- 真实登录下的 Preflight、授权 RAG 返回片段、Agent/Session 生成、语义复核、Workspace DOCX 写入和下载尚未执行。
- 当前自动检查验证来源身份、引用编号和原文摘录，不自动证明引用在业务语义上充分支持报告结论。

本阶段完成后停止，未提交代码。
