# Task P2-F2 验收报告：DAG 图编辑与前后端闭环

日期：2026-10-19

## 结论

- **图编辑 API 与组件实现：PASS**。自定义 Task 可在服务端允许时编辑节点、依赖边、输入绑定、并发数和资源容量；保存前调用 Task 图校验接口。
- **DAG 服务端执行开关：PASS（源码与 API 合约测试）**。`editing_enabled` 和 `execution_enabled` 分开返回；`LXSCOPE_TASK_DAG_ENABLED` 未设置时执行保持关闭。创建 Run、Continue、Step 和节点重跑均由服务端检查执行开关。
- **DAG 专项后端回归：PASS**，53 passed、11 skipped。
- **SOP、九大模板和自定义流程回归：PASS**，49 passed。
- **真实浏览器操作：BLOCKED**。本地 Task 页面重定向到 `/login`，要求用户名和密码；仓库没有可用测试账号，本环境没有合法登录会话。本轮未尝试猜测凭据、跳过认证或创建身份。
- **完整旧链路补充回归：有 1 项失败，归因未定**。`task_mvp_pipeline_test.py::test_agent_tool_python_agent_pipeline_without_mcp` 失败信息来自 TaskService 的验收路径：“当前执行器不支持 AI 步骤验收，请使用已配置模型的会话。”测试夹具 `_MockToolTaskExecutor` 未实现 `verify_node`；该用例执行的是无 graph 的旧 SOP Run，没有进入本阶段的 DAG 开关分支。由于本阶段没有重跑该用例的开发前基线，报告不把它归为本次引入或既有问题。
- **生产 DAG：未启用**。没有修改部署配置、容器环境或数据库。

## 1. 文件修改

本阶段新增或修改：

- `examples/agent_service/task/_models.py`：新增 `TaskDagCapabilitiesResponse`。
- `examples/agent_service/task/_router.py`：新增认证的 `GET /tasks/capabilities`；图校验、创建和更新遇到编辑权限/状态冲突时返回 409。
- `examples/agent_service/task/_service.py`：独立读取 DAG 编辑开关；仅允许认证用户编辑其自有、无模板来源、未归档、未处于 Planner 节点生成状态且无在途 Run 的 Task 图；继续使用现有 graph 校验；执行关闭时拒绝 Continue、Step 和节点重跑。
- `examples/web_ui/frontend/src/api/task.ts`：新增能力、图校验请求与响应类型及客户端方法。
- `examples/web_ui/frontend/src/pages/task/index.tsx`：读取能力契约；仅对无模板来源的自定义 Task 开放 DAG 模式；保存前调用后端校验；执行关闭时禁用 DAG 启动和重新执行。
- `examples/web_ui/frontend/src/pages/task/task-dag-canvas.tsx`：增加依赖边、命名输入、并发和资源容量编辑控件，显示服务端校验问题及节点运行状态。
- `examples/web_ui/frontend/src/pages/task/task-dag-utils.ts`：新增祖先查询和节点变更时的 graph 引用清理。
- `examples/web_ui/frontend/src/pages/task/task-flow-editor.tsx`：DAG 模式下允许配置节点内容、添加和删除节点；隐藏会误导 DAG 调度的线性重排控件。
- `examples/web_ui/frontend/src/pages/task/task-header.tsx`：支持显示并禁用服务端关闭的 DAG 执行按钮。
- `tests/task_dag_editing_test.py`：新增能力分离、真实 Task Router/TaskService 图保存、循环拒绝、模板限制、所有权和活动 Run 状态测试。
- `tests/task_dag_retry_test.py`：两个直接验证节点重跑机制的测试显式启用 DAG 调度器，以匹配服务端新增的执行开关检查。该测试文件在阶段开始前已是工作区未跟踪文件。
- 本报告。

阶段开始前已存在的工作区变更仍保留。当前完整 `git diff --name-only` / `git status --short` 仍包含既有 `admin_api.py`、存储实现、管理系统、Sales Console、Task 旧测试和临时目录等变更；本阶段没有修改这些白名单外内容。没有提交代码。

## 2. 前后端契约

| 接口 | 契约与检查 |
|---|---|
| `GET /tasks/capabilities` | 要求 `get_current_user_id` 身份认证。返回 `{ editing_enabled, execution_enabled }`，两个字段分别表示图编辑许可与 DAG 执行开关。 |
| `POST /tasks/validate` | 提交 `{ task_id, nodes, graph, context? }`，返回 `valid` 和字段级 `issues`。Task ID 按当前用户读取；图编辑能力关闭、来源为模板或存在未结束 Run 时拒绝图校验/编辑。 |
| `POST /tasks/` | 可为无模板来源的自定义 Task 创建 graph；编辑开关关闭时返回 409。旧的无 graph 创建不受影响。 |
| `PATCH /tasks/{task_id}` | 图字段提交时按 Task 所属、模板来源、活动 Run 和编辑开关检查；通过现有 graph 校验后只保存 Task 新修订，不改写既有 Run 快照。非法依赖或绑定返回 422。 |
| `POST /tasks/{task_id}/runs` | 沿用已有 TaskService DAG 执行开关检查。`execution_enabled=false` 时服务端拒绝创建 DAG Run。Continue、Step 和节点重跑也检查同一开关；Pause/Stop 保留已有安全控制行为。 |

图字段按后端实际模型传输：`schema_version`、`edges[{source_node_id,target_node_id}]`、`input_bindings[target][name]={source_node_id,source_value}`、`max_concurrency`、`resource_limits`。服务端校验循环、未知节点、输入源必须是依赖祖先、并发上限和节点资源需求。九大模板及模板副本因模板来源而不能通过伪造前端状态取得 DAG 编辑权限。

编辑开关通过 `LXSCOPE_TASK_DAG_EDITING_ENABLED` 读取，默认允许已认证的 Task 所有者编辑自有自定义 Task；设为 `false` 可关闭编辑。执行开关继续使用现有 `LXSCOPE_TASK_DAG_ENABLED`，默认关闭。两个配置在服务启动时读取。

## 3. DAG 图编辑结果

自定义自由 Task 可点击“启用 DAG 图编辑”。初始 graph 使用现有节点，默认无连线，初始并发度不超过节点数。用户可从画布控件添加/删除依赖边、给目标节点设置前驱祖先的 `output` 或 `result` 命名输入、配置并发度和资源容量；节点详情由现有 `TaskFlowEditor` 编辑。添加边时前端拦截自环、重复边和循环；节点删除会清除引用该节点或已不再可达的输入绑定。

保存先调用 `POST /tasks/validate`。无效结果显示后端 `path/message`，不会发送 Task 更新；通过后调用 `PATCH /tasks/{task_id}`。既有 Run 的节点、graph 和历史记录保持各自快照。标准模板与模板副本不显示编辑入口。

实际 API 合约测试通过 FastAPI ASGI 测试客户端调用 Task Router，应用状态使用真实 `TaskService` 和 `TaskStore`：能力查询返回编辑开、执行关；`POST /tasks/` 保存自定义 graph；循环 graph 的 `PATCH` 返回 422，随后 `GET /tasks/{id}` 仍读到原 revision 和原 graph。另验证编辑关闭、模板来源、活动 Run、节点生成中和错误用户分别被 409/404 拒绝。执行开关关闭时 Continue、Step 和节点重跑均由 TaskService 拒绝。

## 4. 浏览器操作与 Run 控制验收

启动本地 Vite 后访问 `http://localhost:5173/task`，实际页面跳转到 `http://localhost:5173/login`，显示用户名/密码字段和登录按钮。浏览器没有已有应用标签或登录态；仓库的运行验收记录也没有提供合法用户账号。没有提交登录请求或输入凭据，随后停止了本地 Vite 进程。

| 浏览器项目 | 结果 |
|---|---|
| 创建/保存自定义 DAG、连线和节点编辑 | **BLOCKED**：登录身份缺失，未进入 Task 页面。 |
| A → B/C 并行 → D 汇合与 SSE 节点更新 | **BLOCKED**：没有通过浏览器创建或执行 Run。 |
| C 失败后局部重跑且 A/B 保持成功 | **BLOCKED**：没有浏览器 Run；Tool/Python 风险确认只通过现有源码和此前 Task API 回归证据确认，本轮未做浏览器点击验收。 |
| Pause、Continue、Step、Stop | **BLOCKED**：没有浏览器 Run；后端 DAG 控制专项测试通过，但这不等价于浏览器验收。 |
| 409 版本冲突、重复请求和关闭执行开关 | **BLOCKED（浏览器）**：前端沿用已有 409 刷新及重复点击保护，后端专项回归通过；未在真实登录页操作。 |
| 旧 SOP Task 页面和九大模板 | **BLOCKED（浏览器）**；对应后端模板/SOP 自动化回归 PASS。 |

前端 `package.json` 只有 `dev`、`build`、`lint`、`preview` 脚本，没有测试脚本，源码中也没有 Task 页面测试文件。因此没有声称自动化浏览器 E2E 完成。此 BLOCKED 项需要提供合法隔离测试账号及可验证的隔离运行环境后再验收。

## 5. Run 与 Node 状态记录

本阶段没有为浏览器验收创建真实 Run，因此没有新的 live Run/Node 状态迁移记录。Task API 合约测试确认图保存与 invalid graph 拒绝；既有 DAG 专项测试负责 Run 与 Node 的控制路径。本报告不把这些测试结果表述为本轮浏览器状态记录。

## 6. 测试结果

| 命令/范围 | 结果 |
|---|---|
| `pytest tests/task_dag*_test.py -q` | **53 passed, 11 skipped**，41.61 秒。11 项为受 `LXSCOPE_TASK_TEST_REDIS_URL` 门控的 Redis 实测，环境变量未设置，因此未连接 Redis。 |
| `pytest tests/task_sop_test.py tests/task_nine_scenarios_test.py tests/task_template_test.py tests/task_custom_flow_test.py -q` | **49 passed**，16.18 秒。 |
| 加跑上述兼容测试及 `tests/task_mvp_pipeline_test.py` | **52 passed, 1 failed**，34.25 秒。唯一失败为 `TestTaskMvpPipeline.test_agent_tool_python_agent_pipeline_without_mcp`；TaskService 因夹具执行器未实现 `verify_node` 而报“当前执行器不支持 AI 步骤验收，请使用已配置模型的会话”。该用例 Task graph 为空，未执行本阶段 DAG 分支；其开发前是否已失败未在本轮核实。 |
| `pnpm run build` | **PASS**：TypeScript 与 Vite 构建通过，转换 6900 个模块；构建输出包含大于 500 KB 的 chunk 警告。 |
| 修改文件范围 ESLint | **PASS**：本阶段修改的 Task 页面、Task API Client 和画布组件通过。此前对整个 Task 目录运行时，另有 `task-input-form.tsx` 和 `task-template-create-dialog.tsx` 的 import 顺序错误；这两个文件本阶段未修改，未为清除此既有错误扩大变更。 |
| 浏览器交互 | **BLOCKED**：登录页无合法测试会话。 |

本轮未执行完整 `tests/task_*.py`，也未执行真实 Redis/PostgreSQL 或生产服务测试。

## 7. 九大模板兼容

`task_nine_scenarios_test.py`、`task_template_test.py`、`task_custom_flow_test.py` 和 `task_sop_test.py` 合计 49 项通过。源码层面图编辑进一步要求无 `source_template_id` 且 workflow 不是模板实例/模板副本；九大模板不自动转换成 DAG，旧 SOP Task 未提交 graph 时维持原有写入和执行分支。真实模板页面浏览器检查因登录缺失而 BLOCKED。

## 8. 剩余风险与生产部署

- 当前 `LXSCOPE_TASK_DAG_ENABLED` 默认关闭，生产未启用。不要仅凭画布显示或 `editing_enabled=true` 判断执行许可；启动 Run、Continue、Step 和 Retry 仍由服务端检查。
- 本轮没有在 Docker 后端进程中开启 DAG，也没有连接容器数据库。容器属于本地 Compose 项目 `lxscope2`；Task 集成测试 URL 未设置，11 个 Redis 测试已跳过。
- 真实用户认证、SSE 浏览器刷新、并行分支可视化、Tool/Python 风险确认和 409 交互仍需在合法隔离账号下完成。
- 一个无 graph 的旧 SOP pipeline 测试失败且基线差异未核实；在调查该问题并完成浏览器验收前，不应将本阶段描述为完整前后端 E2E 通过或生产就绪。

## 9. 进入后续阶段判断

图编辑代码和后端合约回归已交付；真实浏览器验收为 **BLOCKED**，另有一项旧 SOP pipeline 回归失败且基线未判定。P2-F2 的前后端闭环验收尚未完整通过。生产 DAG 仍关闭；本阶段到此停止，没有提交代码。
