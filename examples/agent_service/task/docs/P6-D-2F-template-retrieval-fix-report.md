# P6-D-2F：S02 策划模板定向检索修复报告

## 1. 基线与根因

- 分支：`integrate/dev_l`
- 基准提交：`1418f2b feat(task): integrate existing rag for project planning`
- 修改范围：Task 检索适配、TaskService 预检/执行调用及 Task 专项测试。
- 未修改 S02 v1 模板、AgentScope 官方代码、知识库服务、共享认证、前端或其他业务模块。

旧 `_task_planning_query` 把模板参数中的 `project_brief`、InputStep answers 和整段运行输入拼成一次查询。相同 brief 会重复进入 query，令模板节点更容易命中项目需求、历史案例等资料。Preflight 也只检查是否返回任意可追溯片段，因此普通知识库文档曾被当成模板检索能力可用。

## 2. 修复内容

### 定向查询和来源判定

`_knowledge.py` 新增四条固定模板检索词：

1. `策划报告编制模板`
2. `报告八章大纲`
3. `策划报告写作要求`
4. `项目策划模板 B-01 B-15`

每条查询继续通过已有 `KnowledgeBaseService.search` 执行，并经现有用户/知识库范围解析和 `get_document_status` 状态校验。查询只使用 Task 已选择的知识库；不会调用其他知识库或绕过文档状态检查。模板检索不再把项目 brief、InputStep 项目资料 ID 或运行输入拼入模板 query。

结果按同一个 `knowledge_base_id + document_id` 聚合、按 chunk 去重。候选文档必须同时满足：

- 来源标题明确指向策划/报告模板，排除需求资料、历史案例、会议纪要、上传指南等来源类别。
- 同一文档的授权原文跨 chunk 覆盖第 1 至第 8 章。
- 原文含写作/编写要求及 B-01 至 B-15 全部条款编号。
- 每个返回片段均有真实的 `knowledge_base_name`、`document_id`、`chunk_index`、由原文计算的 `content_sha256`、来源 `source` 和非空原文片段。

只有通过上述判定的原文片段进入成功结果；模型摘要和未匹配文档不会替代模板原文。缺少来源字段时不再以“未知来源”填充模板命中证据。

### Preflight 与运行时

Preflight 与执行调用同一个 `retrieve_planning_template_evidence`，使用同一组查询及同一模板相关性规则。没有匹配时，能力检查为 `missing`，错误以“未检索到匹配的策划模板。”开头，并附实际查询词、授权检索命中文档和失败原因。运行时同样返回失败状态，并在节点元数据中保留 `template_retrieval_diagnostics`，不会将检索节点记为成功。

项目资料 ID 属于项目需求资料检索意图，不再约束模板检索。权限范围仍限于 Task 当前绑定且当前用户可访问的知识库；每条结果继续经过用户身份和文档状态检查。

## 3. 测试命中与来源字段

离线 Task 契约测试的知识库夹具命中预期文档 `02_策划报告编制模板_测试.docx`。它将八章目录及 B-01～B-15 写作条款拆成三个 chunk，由不同模板查询分别命中，再按同一文档聚合。

| 字段 | 测试结果 |
|---|---|
| `knowledge_base_name` | `授权项目资料` |
| `document_id` | `doc-planning`（测试夹具 ID） |
| `source` | `02_策划报告编制模板_测试.docx` |
| `chunk_index` | `0`、`1`、`2` |
| `content_sha256` | `c33da5fc5d838ea74a958f8c6a9eaa9cd5e4cc5c10ac0a8793c1b9e4875fa528`、`a7e12236ae346023537324d6dcd08af7447b9f39b0b1ecc47cb5966f5ad68661`、`63d056257eddccb20b224026da5dadf5b45d944776ca51fc219e06f31cdbdf54` |

上述文件名、知识库名和文档 ID 是 Task 测试夹具中的期望命中，不是生产代码硬编码的目标。当前没有通过已认证的真实 KnowledgeBaseService 执行线上/本地登录态检索，因此**真实环境实际命中文档及其生产来源字段仍待验证**；不把测试夹具结果声明为真实知识库联调命中。

## 4. 修改文件

- `examples/agent_service/task/_knowledge.py`：定向查询、多 chunk 聚合、模板来源/内容/元数据校验和诊断信息。
- `examples/agent_service/task/_service.py`：Preflight 和运行时共用定向检索；去除 brief 重复拼接；无模板时阻断。
- `tests/task_project_planning_rag_test.py`：覆盖定向查询、跨 chunk 八章与 B 条款识别、来源元数据、需求/案例误命中、未授权文档、Preflight 与运行时一致及失败阻断，并保留 S02 引用核验和 DOCX 流程验证。
- `examples/agent_service/task/docs/P6-D-2F-template-retrieval-fix-report.md`：本报告。

本阶段没有修改白名单外文件。工作区中既有的 `sales-console/**` 修改和其他未跟踪文件仍保留，未纳入本阶段变更。

## 5. 验证结果

Task 专项全量测试：

```powershell
$taskTests = rg --files tests | Where-Object { $_ -match '(^|[\\/])task_.*_test\.py$' }
.\.venv\Scripts\python.exe -m pytest $taskTests -q
```

结果：`140 passed, 2 skipped, 2 subtests passed`。

PostgreSQL 专项的两个测试因未设置一次性 PostgreSQL 测试 URL 而跳过；这不代表完成了真实 PostgreSQL 集成验证。

其他验证：

- `python -m py_compile`（三个修改的 Python 文件）：通过。
- `git diff --check`：通过；Git 只提示工作区 LF/CRLF 转换通知。
- 未改前端，因此未运行前端构建或浏览器验收。

## 6. 仍需真实环境验证

1. 使用实际登录用户，在其 Task 所选知识库中运行四条定向查询，确认实际模板文档和授权来源字段。
2. 确认真实模板文档的八章标题、B-01～B-15 条款能由最多 12 条/次的现有搜索结果跨查询检索完整；若真实索引切分或排序导致缺片段，当前逻辑将失败关闭并显示诊断，不会误报成功。
3. PostgreSQL 专项测试需在配置 disposable PostgreSQL 测试 URL 后运行。

本阶段只修复 S02 模板定向检索，没有新增 RAG、修改 S02 v1 语义或实现审批能力。
