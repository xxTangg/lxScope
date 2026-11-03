# P6-D-2：Task 复用现有 RAG 实现项目策划业务闭环报告

- 日期：2026-10-10
- 分支：`integrate/dev_l`
- 基准提交：`137c64e`

## 1. 验收结论

**Task 侧适配与自动化验证：PASS。真实知识库、登录用户和 Workspace 联调：BLOCKED。P6-D-2 整体状态：BLOCKED，等待真实环境验收。**

已完成 Task 模块内的授权检索适配、来源快照保存、确定性引用核验、运行前真实检索预检，以及 S02 自定义副本的 Task 执行链测试。自动化测试使用隔离的 KnowledgeBaseService / Workspace 接口夹具；这些结果不能证明当前部署已连通真实知识库，也不能替代真实用户权限和下载验收。

S02 `1.0.0` 系统模板、九个系统模板文件和节点顺序均未修改。S02 v1 本身没有产物配置；需要 Word 输出时，用户应从 S02 Task 创建“自定义流程”副本，在最终输出节点使用已有编辑器设置 DOCX。标准 S02 v1 未配置产物时，Preflight 会将 `document.render` 报为缺失并阻止运行。

本阶段没有实现或声称实现真实项目业务数据查询、语义事实审计或正式审批。`HumanApproval` 仍未实现。

## 2. 现有能力检查结果

| 检查项 | 源码依据 | 结论 |
|---|---|---|
| Task 可否使用已有知识库 RAG | `examples/agent_service/task/_knowledge.py:124`、`:237`；`src/agentscope/app/_service/_knowledge_base.py:228`、`:548`、`:980` | 可以通过现有 `KnowledgeBaseService.list_knowledge_base_views`、`search` 和 `get_document_status` 复用；不需要新建 RAG 或 MCP。Task 网关以当前 `user_id` 调用这些接口，知识库范围来自 Task 已选知识库。 |
| 原 Task Agent 检索是否足够 | `examples/agent_service/task/_service.py:2335`（原 `_retrieve_knowledge` 调用）、`:2725`（方法） | 原机制只为 Agent 节点提供文本上下文；模板中的 `planning.template_retrieve` ToolStep 没有接到该机制，且原文本投影不保留稳定的文档/片段定位，不能单独支持确定性引用核验。 |
| RAG 结果来源字段 | `src/agentscope/rag/_vdb/_vector_store.py:53`；`src/agentscope/app/storage/_model/_knowledge_document.py:102` | 搜索结果提供 `document_id`、原始 chunk、source、chunk index 和 score；知识文档记录提供文件名、状态和更新时间。当前公开接口没有不可变文档版本号。 |
| 现成引用核验工具 | Task 能力契约、模板能力目录及当前会话 Tool 注册检查 | 未找到可复用的真实引用核验 Tool/MCP。Task 现有通用输出 Schema 不能证明引用对应授权原文。 |
| 产物写入和授权 | `examples/agent_service/task/_service.py:1336`、`:1934`、`:2682`；`examples/agent_service/task/_agentscope_executor.py:260`、`:281` | 已有 `ArtifactConfig`、DOCX Renderer、Workspace Artifact Writer 和用户上下文预检。本阶段复用该路径，没有另建写文件机制。 |

上述 `src/agentscope/**` 仅作只读检查，没有修改。

## 3. 实际实现

### 3.1 `planning.template_retrieve`

- `examples/agent_service/task/_capability_contracts.py:387` 将 RAG 与引用核验标为 Task 自有能力；`_template_compiler.py:239` 保留其逻辑能力名，不通过外部 Tool 别名映射。
- `examples/agent_service/task/_service.py:120` 为模板结构校验提供 Task 内部 Schema。该 Schema 不注册进 AgentScope Toolkit。模板创建/实例化仍走原路径，不调用 Planner；运行时原生 Task ToolStep 由 TaskService 执行。
- `examples/agent_service/task/_knowledge.py:124` 校验当前用户可见的知识库、已索引资料和可选文档 ID；`:237` 调用现有授权搜索接口。KB ID 不硬编码。资料 ID 来自模板输入或 S02 InputStep，并在检索前通过当前用户权限及文档状态再次核验。
- 只保留真实搜索命中的原始 chunk，并记录 KB ID/名称、文档 ID、文件名、`updated_at`、chunk index、score、原文 SHA-256、原文摘录和状态。最多保留 6 个片段，每段最多 2,000 字符；文档哈希以完整搜索 chunk 计算。没有可用资料、资料未就绪、无命中、权限不可确认和检索异常均失败关闭，不会生成虚假检索结果。
- `TaskRunRecord` 新增可选、限量的 `source_material_ids`；Redis JSON 和 Postgres TaskRun snapshot 原有序列化/反序列化路径会保存新字段，旧记录可依赖默认空列表读取，无数据库迁移。

### 3.2 `document.citation_verify`

- 新增 `examples/agent_service/task/_citation_verifier.py:34`，不调用模型裁决。它要求唯一的 `task-citations` JSON 区块，并逐项核对本次 Run 已保存的 KB、文档、chunk、SHA-256 和连续原文摘录；正文引用标记必须都出现在引用清单中。重复 JSON 字段、伪造 ID/hash、错引文、缺少引用区块或超限文档均失败。
- RAG 证据结果和引用校验结构化结果保存在对应 `NodeRunRecord.result` / metadata 中。运行时只对 Agent 的执行副本追加引用协议，不改 Task 模板或用户保存的 Prompt。
- 最终 Agent 输出在 TaskSOPStep 验证阶段再次进行确定性引用核验；失败则 Run 失败，既有 Artifact Writer 不会执行。已核验来源的 DOCX 使用现有确定性 Markdown-to-ArtifactSpec 回退路径，避免渲染前再调用模型改写引用内容。
- 该校验只能证明所列引用来自本次授权检索快照，且摘录与该原文片段一致；它**不能**证明报告每个事实都已引用，也不能证明引文在语义上足以支持整段结论。知识库接口未提供不可变版本号，因此用 chunk SHA-256 和文档更新时间记录本次快照，不将更新时间冒称版本号。

### 3.3 Preflight 与业务流程

- `examples/agent_service/task/_router.py:268` 将已有可选知识库网关传入只读 Preflight；`_service.py:1215` 对 `planning.template_retrieve` 执行真实授权检索，而不是根据模板声明或“存在 ready 文档”静态放行。引用核验状态不能抵消前置检索失败。
- 执行链继续使用官方 SOPEngine、现有 TaskSOPStep、InputStep、ArtifactConfig 和 Artifact Writer（`examples/agent_service/task/_service.py:2196`）。没有改官方引擎，也没有新增 StepType。
- 原 Agent 通用知识上下文检索对带引用核验的 Run 关闭，避免模型引用未保存到本次 RAG 证据的第二份检索结果。模型只能从本次保存的检索节点结果获得该流程的业务资料。
- S02 v1 原七节点和输入协议未改；S03 及其他系统模板未改。没有修改前端，因为 Task 页面已有知识库选择器、自定义流程副本、最终产物编辑器和产物下载展示；本阶段没有新增 UI。

## 4. 修改文件

本阶段修改文件均在 Task 后端或 Task 专项测试白名单内：

1. `examples/agent_service/task/_knowledge.py`：复用已有授权知识库服务，检查范围、取回带来源的原文快照并分类错误。
2. `examples/agent_service/task/_citation_verifier.py`：Task 内确定性引用存在性和摘录一致性检查。
3. `examples/agent_service/task/_service.py`：Task 原生能力预检、执行、运行证据持久化和最终引用复核；限制已核验输出的后续模型改写。
4. `examples/agent_service/task/_router.py`：Preflight 路由注入 Task 知识库网关。
5. `examples/agent_service/task/_template_compiler.py`：原生 Task 能力保留逻辑能力名，不要求会话 Toolkit 注册同名 Tool。
6. `examples/agent_service/task/_capability_contracts.py`：声明 Task 自有能力集合。
7. `examples/agent_service/task/_models.py`：Run 快照新增可选、最多 128 项的来源文档 ID。
8. `tests/task_project_planning_rag_test.py`：授权范围、来源保留、权限拒绝、检索失败、引用伪造拒绝、Preflight 和 S02 自定义流程 DOCX 闭环夹具测试。
9. `examples/agent_service/task/docs/P6-D-2-rag-task-integration-report.md`：本报告。

未修改 Task 前端、系统模板 JSON、`src/agentscope/**`、`main.py`、共享认证/知识库实现、非 Task 后端、迁移、依赖或构建配置。除本报告外，没有新增其他文档。

## 5. 测试与验证

执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/task_artifact_capability_test.py tests/task_capability_contract_test.py tests/task_clarification_test.py tests/task_custom_flow_test.py tests/task_input_protocol_test.py tests/task_mvp_pipeline_test.py tests/task_postgres_sop_test.py tests/task_sop_test.py tests/task_template_output_test.py tests/task_template_test.py tests/task_template_version_test.py tests/task_tool_test.py tests/task_project_planning_rag_test.py -q
```

结果：**136 passed，2 skipped，2 subtests passed**。两个 PostgreSQL 测试由 `skipUnless(_TEST_URL)` 跳过（`tests/task_postgres_sop_test.py:72`），因此本次没有真实 PostgreSQL 集成结论。

另执行 `python -m py_compile` 检查本阶段 Python 源码和新增测试，成功；`git diff --check` 成功。Ruff 未安装，不能报告 Ruff 检查通过。未修改前端，因此未运行 `pnpm build`；未完成已登录浏览器验收。

S02 闭环测试通过的是**本地确定性模拟**：真实 TaskService/SOPEngine/TaskSOPStep、引用校验器、现有 DOCX Renderer 和 mock Workspace Writer；知识库接口由 fixture 返回一条带文档 ID、片段和状态的记录。测试确认模板实例化及运行不调用 Planner、Task 原生检索/核验不请求外部 Toolkit、用户 InputStep 可继续、DOCX 字节实际生成并交给 writer，且重新打开文件后能读到正文引用及 `task-citations` 区块。该测试不等于真实知识库搜索或真实 Workspace 权限验证。

## 6. 仍阻塞的验收项

当前没有可用于本阶段的已登录业务用户、真实项目策划知识库资料及可验证 Workspace 下载会话。因此以下项目仍为 **BLOCKED**：

1. 真实共享/自有知识库权限、用户隔离和文档授权验证。
2. 对真实文档完成检索、版本变更后的原文快照核验和引用准确性人工抽查。
3. 使用真实 AgentScope 模型生成策划大纲/初稿并检查是否严格使用上游证据。
4. 真实 Workspace 写入、授权读取与浏览器下载 DOCX。
5. 部署态 Preflight 与完整浏览器交互验收。
6. PostgreSQL 实际运行和升级兼容验证（测试 URL 未配置）。

解除阻塞所需条件：提供已登录测试用户；该用户在 Task 页面可见并选中一份真实且已完成索引的策划资料知识库；提供一个真实项目需求用于核对引用；绑定可运行 Agent/Session 和 Task Workspace；配置一次性 PostgreSQL 测试 URL（如需数据库验证）。测试应确认无权限的 KB/文档无法检索、检索片段及来源可追踪、错引文阻断文件生成、有效引用生成并可下载 DOCX。

## 7. P6-D-2 最终判断

**实现适配层：完成。自动化 Task 验收：通过。真实业务闭环验收：BLOCKED。** 当前可以确认代码走的是已有授权 RAG 接口和既有产物机制；不能据此宣称真实 S02 项目策划已联通或真实引用已验证。满足第 6 节条件后再执行真实环境验收。本阶段停止，不进入其他场景，也不开发 HumanApproval。
