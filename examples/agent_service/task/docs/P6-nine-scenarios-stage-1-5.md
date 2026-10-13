# P6 九场景统一完善阶段记录（阶段 1–5）

## 基线与边界

- 分支：`integrate/dev_l`
- 开始时 HEAD：`1418f2b`（未创建提交）
- 沿用工作区中已有的 P6-D-2F、P6-D-2G 与 P6-G-1 Task 实现；没有重做 RAG、证据核验器、SOP、Planner 或 Artifact Writer。
- 保留 `src/agentscope/**`、`main.py`、Task 外业务模块、共享认证/API、数据库迁移及依赖配置。
- 销售台已有改动和工作区已有未跟踪目录未覆盖、未暂存、未提交。

## 阶段 1：Task 通用能力

- 新增版本化 `EvidencePolicy`，描述 claim 类型可接受的来源类型、引用要求及语义人工复核范围。策略只约束服务端已采集的证据，不创建来源，不授予权限。
- S02 使用与模板版本对应的策略；S02 专属的八章、B 条款、项目事实规则仍留在 S02 适配器中。
- 为 TaskRun 与 NodeRun 增加可选 `TaskErrorDetail`，含错误分类、错误码、Task/Run/节点定位及时间戳；老记录不含这些字段时仍可读取。
- 运行详情面板可以展开查看完整节点输出、结构化结果、执行元数据与证据；显示结构化错误分类。常见私密字段在结构化展示时过滤。
- Preflight 错误分类按实际能力边界区分缺少工具、数据源不可用、权限、配置、Schema 与产物错误。
- 当前只有 S02 的业务适配器实际调用通用 Claim–Evidence 校验器。其余场景先声明版本化策略，不宣称已有业务证据采集或自动校验闭环。

## 阶段 2：九场景模板

- 九个 v1 文件保留；注册九个当前 v2 文件，并将模板目录当前版本指向 v2。
- 九个 v2 最终节点分别使用 Markdown、DOCX 或 XLSX 的既有 ArtifactConfig。实例化只保存配置，不生成文件。
- S02、S03 v2 与对应 v1 的流程定义保持一致，只有版本号和最终产物配置不同；S03 仍是八节点，`collect_human_review` 仍为 InputStep，不代表正式审批。
- S04 v2 只准备通知草稿；S08 v2 只生成待移交清单；S09 v2 只生成待提交报销草稿。通知发送、档案写入、ERP 提交均不作为已实现能力。
- 所有模板仍满足八节点限制。没有真实 Tool/MCP 的场景仍可创建；Preflight 负责阻止不具备能力的运行。

## 阶段 3：隔离测试

- 合成输入逐个实例化九个当前模板，确认版本、节点上限、最终产物配置、用户隔离及不调用 Planner。
- 在未提供真实业务工具/知识库/Workspace 的服务夹具中，九个 Task 的 Preflight 均失败关闭，且不会生成 TaskRun。
- 对九个当前产物配置运行真实 Markdown/DOCX/XLSX 序列化器，检查输出字节与容器签名。该检查不等同于真实用户 Workspace 写入。
- Claim–Evidence 测试覆盖场景策略拒绝错误来源类型、支持的来源类型、语义人工复核标记和独立待核实 ID。
- 测试夹具只证明 Task 编排、契约和序列化逻辑，不证明生产业务服务可用。

## 阶段 4：前端

- 保留原有 Task 页面与 Preflight 折叠改动。
- Preflight 失败项展示服务端错误类别和错误码。
- NodeRun 展示完整输出入口、结构化 `result`、能力/Tool 信息、执行时间、元数据与证据；Run/Node 错误展示分类及定位信息。
- 没有增加页面、共享客户端或导航；未显示“已审批”“已发送”“已归档”或“已提交 ERP”等未实现状态。

## 阶段 5：验证

- Task 全量测试：`169 passed, 2 skipped, 2 subtests passed`。两个 PostgreSQL 测试因没有可用的一次性数据库配置而跳过，不计为数据库集成通过。
- Python `compileall`：通过。
- 前端 `pnpm build`：通过；Vite 有现有的大 chunk 提示，不影响构建结果。
- `git diff --check`：通过；仅有 Git 对 LF/CRLF 转换的警告。
- Black 与 Ruff 未安装在当前 `.venv`，未新增依赖，也未声称其检查通过。
- 浏览器：HTTP GET `localhost:8000/task` 返回 200。Computer Use 因无法可靠判定当前 Chrome 页 URL 而停止，故模板创建、运行和下载的浏览器交互验收为 NOT TESTED。

## 本阶段结论

九个模板可被读取、实例化和编辑；共同的错误与结果展示已经补齐；产物格式、版本兼容和隔离场景通过自动化验证。除 Task 已有的 S02 知识库/引用适配和通用产物生成机制外，真实业务 Tool/MCP、用户认证下的知识库、模型会话与 Workspace 联调仍需真实环境验证。正式 HumanApproval 继续 BLOCKED。
