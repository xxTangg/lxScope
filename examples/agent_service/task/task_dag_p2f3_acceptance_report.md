# Task P2-F3：真实浏览器端到端验收报告

日期：2026-10-19  
总体结论：**BLOCKED（自定义 DAG 创建、编辑、校验和保存已通过；真实 Run、局部重跑与断点控制尚未验收）**

## 1. 验收范围与结论

本阶段使用本地 Web UI 和用户确认的隔离测试账号。用户确认当前 Agent/Session 只连接本地测试 MCP。未运行真实 Task 节点、未创建 Run、未调用模型或生产 MCP、未连接生产数据库；未修改产品源码、测试、配置或导航，也未提交代码。

| 验收项 | 结果 | 实测依据 |
|---|---|---|
| 合法登录与本地 Task API | **PASS** | 隔离测试账号下页面可访问；认证后的 Task API 请求返回 200。 |
| 创建自定义 Task | **PASS** | POST /tasks/ 返回 201，Task ID 为 3299944019724b2889e91ac83a712ac8。 |
| 编辑 DAG 节点与依赖 | **PASS** | 页面保存并重新读取 5 个 CodeStep 节点、5 条依赖；A 分别连到 B/C，B/C 汇入 D，D 连到 E。 |
| 命名输入绑定与并发设置 | **PASS** | D 的 branch_b ← B.output、branch_c ← C.output 已显示；最大并发为 2。 |
| 保存及服务端 DAG 校验 | **PASS** | POST /tasks/validate 返回 200，PATCH /tasks/{id} 返回 200；页面显示“任务已保存”，保存按钮随后变为禁用。 |
| DAG 执行开关关闭时的前端行为 | **PASS** | GET /tasks/capabilities 返回 200，页面显示执行关闭；“DAG 执行未开启”按钮禁用，任务未执行。 |
| DAG 关闭时服务端拒绝 Run | **BLOCKED（仅有源码证据）** | 本轮未发送 POST /tasks/{id}/runs。服务端实现应在关闭时以 HTTP 409 拒绝，详见第 6 节。 |
| DAG 实际执行、并行和汇合 | **BLOCKED** | 未创建 Run；没有运行中 SSE 事件或节点输出。 |
| 失败节点局部重跑与 attempt 历史 | **BLOCKED** | 没有失败节点或 Run；未调用重跑 API。 |
| Pause / Continue / Step / Stop | **BLOCKED** | 没有 Run；未调用控制 API，不能据此判断 Step 是否只派发一个节点。 |
| 旧 SOP 与九大模板兼容 | **PASS（只读页面检查）** | 已打开原有“会前准备” Task，界面标记“标准模板任务”，来源为“会前准备 v3.0.0”，7 个步骤的顺序受服务端约束；未编辑或重新运行。模板入口显示九大场景模板，未启动模板。 |
| 左侧导航 | **PASS（只读页面检查）** | 页面切换期间左侧导航样式与条目未改变；本轮没有导航源码修改。 |

## 2. 隔离环境与前置检查

- 浏览器页面：http://localhost:8000/task/3299944019724b2889e91ac83a712ac8；后端 API 由本地 Task 服务提供。
- 用户确认已切换隔离测试账号，并确认当前 Agent/Session 仅连接本地测试 MCP。浏览器中的 Task 工具目录查询发生在本地环境；未执行工具调用。
- Docker 中 Task Store 使用本地 Redis。Task 列表在本轮创建前显示 22 个 Task，创建后显示 23 个。新增验收 Task 保留在隔离测试账号中；未删除既有数据。
- LXSCOPE_TASK_DAG_ENABLED 未显式设置。Task 服务源码默认将 DAG 执行设为关闭；图编辑开关默认开启。生产 DAG 未开启。
- MCP 已确认是本地测试 MCP，但模型运行环境没有得到相同的本地确认。Task 执行器的节点验收会调用 get_model()，而本地运行配置含远程模型提供方端点。为满足“不连接生产模型”的要求，本轮没有启动任何节点。

## 3. 实际浏览器操作与 API 调用链

操作顺序：

1. 在本地 Web UI 使用当前登录的隔离测试账号进入“我的任务”。
2. 从“新建任务”选择自由任务路径，将 Agent/Session 保持未绑定。目标文字明确说明只使用合成输入和本地纯 Python 节点。
3. POST /tasks/ 创建 Task，返回 201 和 Task ID 3299944019724b2889e91ac83a712ac8。
4. 尝试自由任务生成请求 POST /tasks/{id}/generate；请求未绑定 Agent/Session，服务端返回 422，流程没有使用模型生成节点。随后在 UI 手工编排节点。
5. 通过 Task 页面添加 5 个 CodeStep 节点并保存代码内容。
6. 在 DAG 编辑器中设置 5 条依赖、两个命名输入绑定和最大并发数 2。
7. 保存前页面调用 POST /tasks/validate，返回 200；保存调用 PATCH /tasks/{id}，返回 200。
8. 通过 GET /tasks/capabilities 读取能力，返回 200，editing_enabled=true、execution_enabled=false。页面提示“当前允许保存图定义；DAG 执行开关关闭，服务端不会创建 DAG Run”。

本轮还观察到以下认证后页面请求：GET /tasks/、GET /tasks/{id}、GET /tasks/knowledge-bases 和 GET /tasks/tools 均返回 200。GET /tasks/tools 只读取用户确认的本地测试 MCP 能力；没有调用工具执行接口。没有发送 POST /tasks/{id}/runs、节点 retry、Pause、Continue、Step、Stop 或 SSE 订阅请求。

## 4. DAG 图定义与状态记录

验收 Task 保存的图结构：

~~~text
A (CodeStep)
├──> B (CodeStep) ──┐
└──> C (CodeStep) ──┴──> D (CodeStep) ──> E (CodeStep)
~~~

- 依赖：A→B、A→C、B→D、C→D、D→E。
- D 的输入绑定：branch_b 来源于 B.output；branch_c 来源于 C.output。
- 最大并发：2。
- 五个节点均为 CodeStep，没有 AgentStep 或 ToolStep；知识库选择数为 0。
- D 的代码读取合并输入中的 named_inputs。该输入路径已在 UI 保存，但没有通过真实执行验证运行时输出格式。

保存后的页面仍显示“尚未执行”；指标为 0/5。因此本轮没有 Run ID，也没有 Run 或 Node 状态变化记录。节点并行、D 等待 B/C 成功、输出传播和 E 结束 Run 均未实测。

## 5. 局部重跑与断点执行

这些项目没有运行结果，均为 **BLOCKED**：

- C 失败后保持同一 Run、创建新 attempt、保留 A/B 成功输出及失败记录：未测。
- 下游失效、重复重跑幂等和外部副作用风险确认：未测。
- Pause 后停止派发、等待在途节点的行为：未测。
- Continue 是否保留有效成功节点：未测。
- Step 每次只派发一个 ready 节点：未测。
- Stop 是否进入不可恢复终态：未测。
- SSE 状态更新与重连：未测。
- 请求重复、版本冲突、节点执行中收到 Pause/Stop：未测。

没有把 UI 的待执行状态或源码实现描述为实测通过。

## 6. DAG 关闭时的服务端拒绝证据

**浏览器实测：**能力接口返回 execution_enabled=false；执行按钮禁用，页面没有创建 Run。

**源码证据：**

- [_service.py](./_service.py#L775) 中 _configured_dag_scheduler_enabled() 仅在 LXSCOPE_TASK_DAG_ENABLED 显式为 1/true/yes/on 时启用调度。
- [_service.py](./_service.py#L2180) 中 TaskService.create_run() 对包含图定义的 Task 先校验图，再在调度开关关闭时抛出 TaskWorkflowConflictError。
- [_router.py](./_router.py#L830) 的 POST /{task_id}/runs 路由会将 TaskWorkflowConflictError 映射为 HTTP 409 Conflict。
- [_router.py](./_router.py#L89) 的能力查询需要 get_current_user_id 身份认证。

因此，HTTP 409 有源码路径证据，但本轮没有发出已认证的 Run 创建请求，也没有实际观察该 409 响应；服务端拒绝的集成测试仍为 **BLOCKED**。

## 7. 旧 SOP、九大模板与导航

- 打开隔离账号中已有的“会前准备” Task 后，界面显示“流程类型：标准模板任务”“来源模板：会前准备 v3.0.0”。页面说明标准模板的必需步骤及顺序由服务端约束，且“模板流程顺序固定，服务端会拒绝重排”。
- 该 Task 显示 7 个节点和已有完成历史；这些是原有 Task 页面状态，不是本轮运行结果。本轮未保存该 Task、未改写步骤、未重跑。
- 模板入口显示九大业务场景模板。本轮只检查入口，没有逐个打开、修改或执行九大模板，因此模板兼容结论限于只读页面核查。
- 左侧导航持续可见，未改变其样式或功能；本轮没有修改相关源码。

## 8. 测试与截图记录

- **真实浏览器手工操作：**通过 CUA 在本地已登录页面执行，具体步骤和接口结果见第 3 节。
- **DAG 创建/图编辑/保存：**PASS。
- **DAG Run、重跑、Pause/Continue/Step/Stop、SSE：**BLOCKED，未创建 Run。
- **前端构建、Scoped ESLint、自动化 E2E、后端 pytest：**本轮未运行。
- **截图：**CUA 浏览器操作记录中显示了隔离 DAG Task 的“执行开关关闭、0/5 节点”画面、A→B/C 依赖画面，以及旧标准模板页面。截图由浏览器工具以内嵌图像返回，但当前工具没有将该图像导出为工作区文件的接口，因此报告未附本地图像文件。页面截图没有用于证明未执行的部分功能。

## 9. 工作区与文件变更

本轮开始时，工作区已有多项 Task 后端、Task 前端、Sales Console、测试、验收报告和临时目录的修改/未跟踪内容。保留了这些内容，没有清理、覆盖或还原。

本轮没有修改产品源码、测试、配置、业务模板或导航，只更新本报告。执行结束时的 git status --short 与本轮开始快照的条目一致；git diff --name-only 中的 tracked 文件仍是开始时已修改的文件。报告本身为既有的 untracked 文件，git diff --name-only 不会列出它。

未提交代码。

## 10. 剩余风险与最终判断

要完成剩余浏览器验收，需要一个已确认只使用隔离本地模型的 Agent/Session，并在隔离服务中显式打开 DAG 执行开关。当前节点验证器会调用模型，即使业务步骤使用纯 Python；没有上述条件时运行会违反“不连接生产模型”的约束。

最终判断：**P2-F3 BLOCKED，真实浏览器端到端闭环未完成。** 已有实测结果证明认证、Task 创建、DAG 编辑/校验/保存、能力开关展示及旧标准模板页面可用；DAG 调度、失败重跑、断点控制和服务端 HTTP 409 实际响应仍未验收。未启用生产 DAG，未提交代码。

