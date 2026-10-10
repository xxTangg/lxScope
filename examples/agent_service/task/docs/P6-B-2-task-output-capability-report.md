# P6-B-2 Task 模板产物配置与结构化输出能力报告

## 结论

- 模板文件输出：**PASS**。模板副本可配置 Markdown、DOCX、XLSX；任务正常完成后复用现有序列化器、Workspace 写入边界和 TaskRun 产物元数据。
- 结构化输出校验：**PASS**。服务端使用有界 JSON Schema 子集确定性校验 `StepExecutionResult.result`；失败进入原有 SOP 失败路径，并保留结果、校验状态和问题列表。
- 九场景回归：**PASS**。Task 模板版本、目录和既有 SOP 回归通过；九个已发布模板文件均未修改。该结论是代码/专项测试回归，不表示外部业务系统均已接入。
- 正式 HumanApproval：**BLOCKED**。本阶段没有审批身份授权、审批 API 或正式审批状态能力；结果只作为 Task 输出，不表示已审批或正式签发。

## 实际修改文件

以下是本阶段修改的白名单内文件及主要实现位置。没有修改模板目录下的九个系统模板 JSON。

| 文件 | 主要位置 | 修改目的 |
|---|---:|---|
| `examples/agent_service/task/_output_validation.py` | 14–392 | 增加安全、有界的 Schema 定义校验、结果校验和提示文本生成。 |
| `examples/agent_service/task/_models.py` | 229–299 | `TaskNode` 增加可选 `output_schema` 并校验定义；原 ArtifactConfig 类型不变。 |
| `examples/agent_service/task/_template_models.py` | 40–58、106–144、218–267 | 为 TemplateStep 增加可选 `artifact` 和 `output_schema`；限制格式、文件名、节点位置和 Schema 类型；节点上限仍为 8。 |
| `examples/agent_service/task/_template_compiler.py` | 263–356、439–460 | 将模板配置编译到现有 TaskNode/ArtifactConfig，并将 Schema 约束附加到运行提示。 |
| `examples/agent_service/task/_template_validation.py` | 112–126、177–198、367–412 | 校验副本产物配置；允许编辑副本的产物设置；继续锁定模板结构化输出约束。 |
| `examples/agent_service/task/_service.py` | 851–893、1150–1175、1398–1425、1547–1558 | 在 SOP 校验阶段确定性验证输出；成功后生成、写入并关联产物；禁止 Input 节点生成文件。 |
| `examples/web_ui/frontend/src/api/task.ts` | 70–82、100–110、312–322 | 增加 Artifact 和输出 Schema 的前端类型。 |
| `examples/web_ui/frontend/src/pages/task/task-flow-editor.tsx` | 77–111、587–657 | 模板副本可选择已有文件格式和文件名；恢复可选 Agent 节点时保留其产物/Schema 配置。 |
| `examples/web_ui/frontend/src/pages/task/task-template-preview.tsx` | 14–20、148–162 | 预览模板声明的文件格式、文件名和结构化输出校验标记。 |
| `examples/web_ui/frontend/src/pages/task/task-run-panel.tsx` | 362–416 | 展示输出 Schema 校验状态及服务端记录的错误原因；已有产物预览/下载继续使用原功能。 |
| `tests/task_template_output_test.py` | 1–624 | 新增模板编译、配置拒绝、格式序列化、延迟生成、TaskRun 关联、用户隔离、Schema 成败及写入失败测试。 |
| `examples/agent_service/task/docs/P6-B-2-task-output-capability-report.md` | 全文 | 本阶段交付报告。 |

## 现有 Artifact Writer 复用方式

- `ArtifactFormat` 和 `ArtifactConfig` 已定义 Markdown、DOCX、XLSX；`TaskArtifactRecord` 记录 `run_id`、`task_id`、`node_id`、文件名、格式、路径、大小及预览内容（`_models.py:75–99`、`_models.py:454–467`）。
- 现有 `_artifacts.generate_artifact()` 继续负责文件字节序列化；DOCX/XLSX 继续使用 Task 现有 Office renderer。安全文件名处理与格式扩展名仍由原逻辑负责（`_artifacts.py:41–82`）。
- TaskService 只在 SOP 全部完成后生成产物，写入 `artifacts/{run_id}/{filename}`，随后将真实写入结果的元数据保存到该 TaskRun 并发出 `artifact.created`（`_service.py:851–893`、`_service.py:1398–1425`）。模板实例化只编译配置，不写文件。
- AgentScope 执行适配层通过已有 Workspace 边界写入/读取文件（`_agentscope_executor.py:202–235`）；Task 下载路由仍先按当前用户查找 Run/Artifact，再通过 Workspace 读取内容（`_service.py:655–686`、`_router.py:465–517`）。本次没有修改官方 AgentScope 代码或认证机制。
- 若没有可用 Workspace writer，或实际写入失败，TaskRun 不会记录虚假的产物成功元数据；现有异常路径会将运行标记为失败。单个 Task 仍只允许一个最终输出节点配置文件产物，Input 不能作为产物节点（`_service.py:1547–1558`）。

## 模板配置协议

TemplateStep 增加两个可选字段；旧模板省略字段时语义不变：

```json
{
  "artifact": { "format": "markdown", "filename": "成果说明.md" },
  "output_schema": {
    "type": "object",
    "properties": { "summary": { "type": "string" } },
    "required": ["summary"],
    "additionalProperties": false
  }
}
```

- `artifact` 仅允许 `markdown`、`docx`、`xlsx`，仅能配置在最终非 Input 输出节点；文件名限单个路径组件，不允许路径分隔符、控制字符或任意目录。允许在模板副本中选择格式/名称，系统模板本身不因此被改写。
- `output_schema` 当前仅允许声明在 Agent 模板步骤。Compiler 将其带入 TaskNode；不新增 Output StepType，也不改变 agent/tool/python/input 四种节点类型。
- Schema 支持 `type/title/description/properties/required/additionalProperties/items/minimum/maximum/minLength/maxLength/minItems/maxItems/enum` 子集。拒绝 `$ref`、组合关键字、正则模式和其他未声明特性；根类型必须为 object。
- Schema 上限：16 KB、8 层、128 个 Schema 节点、对象最多 32 个字段、枚举最多 32 个标量。运行结果上限：64 KB、12 层、4096 个对象字段/数组项；最多记录 8 条错误。
- 校验使用 `jsonschema.Draft202012Validator`，没有 `eval` 或用户校验代码。AgentScope Agent 结果为现有结构化包装时，验证其中 `output` 的 JSON 内容；其他适配器验证持久化的 `result`。Schema 不存在时旧 Task 保持旧流程。
- Schema 不通过时，在验证失败记录中保留目标、状态和问题路径，同时保留该节点原始输出/结果；复用现有 SOP 失败处理，不生成文件，也不允许模型声明自行通过（`_service.py:1150–1175`）。

## 九场景与历史版本兼容

- 没有修改九个系统模板 JSON，也没有修改 `s03_expert_matching.v1.json`，没有发布 S03 v2，也没有把模板副本自动升级。
- TemplateStep 字段均为可选，因此既有模板解析和版本选择仍按原行为运行。`steps` 最大长度仍为 8（`_template_models.py:234`）。
- 独立测试从内存副本构造带 Artifact/Schema 的 S03 v1 派生测试数据，验证仍为 8 节点、成功编译，且注册表中的原始 v1 不含新配置（`tests/task_template_output_test.py:183–202`）。这不是对系统模板的写入。
- 前端模板副本编辑只新增格式/文件名选择。运行页继续展示真实 TaskRun 产物与授权下载按钮；校验失败显示实际服务端问题。没有“已审批”或“正式签发”标记。

## 测试和工程验证

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/task_template_version_test.py tests/task_template_test.py tests/task_sop_test.py tests/task_mvp_pipeline_test.py tests/task_tool_test.py tests/task_postgres_sop_test.py tests/task_template_output_test.py -q
```

结果：**80 passed, 2 skipped**。2 项 PostgreSQL 测试由于当前环境未配置 PostgreSQL 而跳过，不代表通过了真实 PostgreSQL 集成验证。

新增专项测试验证：

1. 无新增配置的历史模板正常解析、目录版本解析不变。
2. 支持的三种格式可通过既有 `generate_artifact()` 生成真实 Markdown/DOCX/XLSX 字节。
3. 模板实例化不调用 Planner，也不提前写产物；成功运行后通过 TaskArtifactWriter 接口写入临时磁盘文件，并断言字节真实存在。
4. 产物元数据的 `run_id/task_id/node_id` 关联正确；拥有者可以读取，其他用户无法通过 TaskService 读取。
5. 副本文件名编辑不修改原系统模板；不安全路径和不支持的配置被拒绝。
6. Schema 成功/失败、失败记录可追踪、Schema 失败时不生成文件；写入失败时不发 `artifact.created` 或返回产物成功。

DOCX/XLSX 测试运行前，本地 `.venv` 缺少仓库 `pyproject.toml` 已声明的 `python-docx`、`openpyxl`，仅在本地虚拟环境补齐；没有更改依赖声明或锁文件。

前端构建：在 `examples/web_ui/frontend` 执行 `pnpm build`，`tsc -b` 和 Vite 构建通过。Vite 输出已有的大文件 chunk 提示；构建配置和依赖均未修改。没有运行浏览器端到端测试。真实 AgentScope Workspace 后端、已认证 HTTP 下载链路和已配置 PostgreSQL 的集成环境未在本次运行环境中验证；产物写入/隔离通过 TaskService + 磁盘测试适配器验证。

## 白名单与工作区状态

- 本阶段改动均在 Task 后端、Task 前端、Task 专项测试和 Task docs 白名单内；没有修改 `src/agentscope/**`、`main.py`、共享 API Client、认证、导航、数据库迁移、依赖或构建配置。
- 初始工作区已有 `sales-console/**` 修改及若干未跟踪目录/文件；它们与本阶段无关，未编辑、删除、暂存或提交。本阶段的 `git diff --name-only` 中白名单外的销售台文件来自该初始工作区状态。
- 报告落盘后的 `git diff --check` 已通过；最终 `git status --short` 中本阶段文件与初始工作区改动仍分别可辨，没有将无关文件加入暂存区。

## 后续能力缺口

- 正式 HumanApproval 仍 BLOCKED：可信审批身份/授权策略、幂等审批决定、持久化待审批恢复与审计仍需单独设计批准。
- S03 仍未实现真实专家推荐闭环：没有真实专家数据查询、回避规则数据源或业务名单提交接口；Schema 只校验结果形状，不代表推荐业务正确。
- 本地 TaskArtifactWriter 磁盘适配器测试覆盖真实文件字节和 TaskService 所有者隔离，但真实 AgentScope Workspace 服务端、授权下载 HTTP 集成及 PostgreSQL 并发/故障场景仍需有对应环境后补验。
- 模板编辑界面当前提供文件格式和文件名设置及 Schema 状态查看；本阶段没有增加通用 JSON Schema 可视化编辑器，也没有新增审批/分支/并行/等待节点。
