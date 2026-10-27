# Task 通用节点重跑与断点执行验收报告

日期：2026-10-22  
分支：`integrate/dev_l`  
阶段开始 HEAD：`e63c3b80278dba2db5ddfd8e195b988481cabd1e`  
总体结论：**PARTIAL / BLOCKED；不宣称生产就绪**

## 1. 范围与基线

本次按 R0→R5 完成 Task 层 SOP 适配、Task API/页面集成与隔离测试。DAG 仍使用 DAG Scheduler；普通 Task 和 S01–S09 仍使用官方 AgentScope `SOPEngine` 的线性语义。没有修改 AgentScope 主框架、`main.py`、业务模板、依赖、全局配置或数据库 Migration，也未提交代码。

开始前保存了 `git status --short`、`git diff --name-only`、staged/unstaged patch、白名单文件快照和完整 pytest 基线，目录为：

`C:\Users\15452\AppData\Local\Temp\lxscope-task-control-baseline-20261022-165208`

基线环境未设置 `LXSCOPE_TASK_TEST_DATABASE_URL`、`LXSCOPE_TASK_TEST_REDIS_URL` 或 `LXSCOPE_TASK_TEST_REDIS_ISOLATED`。没有连接生产模型、生产 MCP、生产 Redis 或 PostgreSQL。

初始 Task 全套基线：`43 failed, 330 passed, 14 skipped, 21 subtests passed`。阶段结束同环境全套：`43 failed, 340 passed, 14 skipped, 30 subtests passed`。逐个比较失败 nodeid，**基线与结束时的 43 项完全相同：新增失败 0，消失失败 0**。异常断言仍是原有失败；对照日志中少数输出仅因每次运行生成的 Run ID、内存地址以及新增的 `automatic_verifications_recorded` 元数据不同，不改变原断言。日志：

- 基线：`pytest-task-baseline.txt`
- 阶段结束：`pytest-task-final-2.txt`

两份日志均在上述临时目录中。完整套件仍有 43 个既有失败，不能把它描述为全绿。

## 2. R0：差距审计

**结果：PASS（审计阶段）**

审计记录：[R0_task_control_gap_audit.md](R0_task_control_gap_audit.md)。确认原 Task Service 将 SOP 全流程一次性交给 `SOPEngine.reply_stream()`，SOP 状态投影按位置匹配；原来 Pause/Continue/Step/Stop 和局部重跑仅支持 DAG，SOP 写入也未统一使用 CAS/租约/栅栏。

R0 修改文件：`R0_task_control_gap_audit.md`。R0 没有改运行代码。

遗留风险：基线已有 43 个 Task 测试失败；真实数据库和浏览器测试环境需在后续隔离验收中确认。

## 3. R1：统一 Task Run 状态与存储契约

**结果：PASS（Task 层实现及隔离测试）；真实 Redis/PostgreSQL 为 BLOCKED**

- 普通 SOP Run 仍由 `TaskService.create_run()` 创建，Run 快照固定节点 ID 顺序并计算 `node_snapshot_fingerprint`；SOP checkpoint 以 `task_node_ids` 对齐，旧 Run 仅在冻结节点数量一致时兼容恢复，无法安全映射时 fail closed。
- SOP 与 DAG 共用 Task store 的原子 `transition_run()`、CAS 版本、幂等 command ID、事件追加和 claim/lease/fencing；阻止对已持久化 DAG/SOP Run 使用 `save_run()`、`update_run()`、`update_node_run()` 或 `append_event()` 绕过 CAS。
- Router 保持现有 Pause/Continue/Step/Stop/节点重跑 API，并允许服务端按 engine 返回 SOP 控制/重跑能力。

阶段修改文件：`_models.py`、`_sop.py`、`_store.py`、`_redis_store.py`、`_postgres_store.py`、`_router.py`、`_service.py`。

隔离证据：DAG store/control/retry/editing 与 SOP 控制专项联合运行曾得到 `37 passed, 3 skipped`；未连接真实数据库。存储适配器的原子性、多进程互斥、lease/fencing 和数据库故障恢复不因 Memory 单元测试而被视为已验收。

遗留风险：真实 Redis/PostgreSQL 执行仍 BLOCKED，具体用例见第 10 节。

## 4. R2：SOP Pause、Continue、Step、Stop 与恢复

**结果：PASS（TaskService/Task API 的内存隔离路径）；真实多进程持久化恢复为 BLOCKED**

SOP 执行器每次通过官方 `TaskSOPStep` 驱动一个冻结节点，继续使用现有节点执行器、AI verifier、人工 InputStep、知识上下文及输出/产物校验。已成功节点从 Run 快照跳过；节点状态和 SOP 状态通过同一 fenced CAS 检查点保存。

Run/Node 转换由测试观察到：

| 操作 | Run 转换 | 节点行为 |
|---|---|---|
| 创建/开始 | `queued → running` | ready 节点变为 `running`，保存后变为 `succeeded`、`failed` 或 `awaiting_input` |
| Pause（节点在途） | 写入 `pause_requested`，节点安全完成后 `running → queued` 且 `control_mode=paused` | 不派发下一 SOP 节点 |
| Continue | `queued + paused → queued + continuous → running` | 跳过仍有效的成功前缀，从第一个未完成节点继续 |
| Step | `queued + paused → queued + single_step`，一个节点 checkpoint 后回到 `queued + paused` | 只派发一个 ready SOP 节点；后续节点保持 `pending` |
| Stop | `→ canceled + stopped` | 未开始节点取消；终态不可 Continue |
| 等待人工输入 | `→ awaiting_input` | Continue/Step 不跳过输入；通过原输入 API 在同一 Run 回答后恢复 |
| Worker 恢复 | 运行中节点转 `failed`，Run 转 `failed` | 标注 `recovery_required/manual_verification`；副作用状态未知时先拒绝重跑 |

定向测试覆盖串行 Pause/Continue/Step/Stop、暂停时在途节点处理、重复 request_id、stale version、损坏节点映射、人工输入同 Run 恢复、TaskService 重建，以及跨 Service 的取消订阅。跨 Service 取消缺少监听的问题已补上；兼容保留旧 `step.rejected` 事件并同时记录 `step.attempt_rejected`。

阶段修改文件：`_service.py`、`_sop.py`、`_store.py`、`tests/task_sop_control_test.py`、`tests/task_sop_test.py`、`tests/task_clarification_test.py`。

遗留风险：同进程 Memory/消息总线的测试不能证明进程崩溃、数据库事务或共享 Redis 锁语义。

## 5. R3：同一 Run 内 SOP 节点局部重跑

**结果：PASS（合成 Task 执行路径）；外部副作用执行保障仍需隔离环境验收**

- 重跑失败节点保留原 `run_id` 和成功前驱输出；选中节点建立新的 manual attempt，保存原失败 attempt；线性 SOP 将该节点之后所有后继置为待执行并清理旧产物、交付状态和输出。
- 重跑成功节点时同样使其后继失效；运行中的/等待输入节点、非 FAILED Run、非法 snapshot 和 stale version 被拒绝。
- Tool、Python、外部副作用 capability 或 `recovery_required` 节点要求 `side_effects_verified=true`，由服务端再次校验；该确认不会声称外部副作用 exactly-once 或已回滚。
- `retry_node` 后 Run 保持 `queued + paused`，必须通过 Continue/Step 明确恢复，不会自动越过确认流程。

证据：`tests/task_sop_control_test.py` 中失败节点重跑、成功节点重跑及恢复门禁用例；认证 FastAPI 节点重跑请求验证相同 Run ID、A 成功结果保留、B 待重跑、C 未派发和重复 request_id 幂等。

阶段修改文件：`_service.py`、`_models.py`、`tests/task_sop_control_test.py`、`tests/task_postgres_sop_test.py`、`tests/task_sop_template_retry_test.py`。

遗留风险：真实 Tool/Python 副作用状态、Worker 被杀后的服务级确认、真实执行权 fencing 尚未通过 Redis/PostgreSQL 多进程验收。

## 6. R4：Task 页面和 API Client

**结果：PASS（类型检查、构建、Scoped ESLint）；真实浏览器验收 BLOCKED**

Task 页面复用现有 Run 控制面板：按服务端 SOP 能力开放线性 Run 控制/重跑，DAG 仍按 graph 能力显示；SOP 下游影响按线性顺序计算；Tool/Python/recovery 状态在调用节点重跑时显示副作用确认；按钮发送 `request_id` 和 `expected_run_version`，以服务端返回 Run 状态为准。只改了 Task API Client 和 Task 页面，没有改左侧导航或业务模板。

验证通过：

- `pnpm exec eslint src/pages/task/index.tsx src/pages/task/task-run-panel.tsx src/api/task.ts`：通过。
- `pnpm run build`（`tsc -b && vite build --configLoader native`）：通过。Vite 仅报告主 bundle 大于 500 kB 的体积警告。

阶段修改文件：`examples/web_ui/frontend/src/api/task.ts`、`examples/web_ui/frontend/src/pages/task/index.tsx`、`examples/web_ui/frontend/src/pages/task/task-run-panel.tsx`。

浏览器验收未执行：尝试读取现有 Chrome 隔离页面时，Computer Use 返回“无法以足够置信度确定当前 browser URL 以执行策略检查”，并停止本轮 UI 自动化。按工具要求停止，没有继续点击、输入或伪造登录；因此按钮交互、SSE、409/422、登录态和九大模板页面视觉兼容均为 BLOCKED。

## 7. R5：S01–S09 与全量回归

**结果：PARTIAL；Task 控制契约逐模板通过，九场景完整业务执行 BLOCKED**

新增 `tests/task_sop_template_retry_test.py`：从当前 `TemplateRegistry` 读取 S01–S09 的实际版本化定义，通过 `TaskTemplateService.instantiate()` 生成真实模板节点快照；每个模板分别用 synthetic 输入和本地纯 Python 执行器/假 verifier，验证真实 TaskService `retry_node()`、同一 Run、成功前缀保留、失败 attempt 归档，以及随后 Step 只派发一个 Agent 节点、节点 checkpoint 后暂停、后继仍为 pending。**这 9 个子场景的初始失败 Run 是测试夹具写入的 TaskStore 快照，没有调用 `create_run()` 执行业务 SOP。**

另有既有 `tests/task_nine_scenarios_test.py`：九个模板均可实例化为 `workflow_mode=template_instance` 的线性 Task，模板政策和产物定义可读取；未绑定真实业务依赖时 Preflight fail closed。全量 Task 测试中该文件通过。

所以可以确认：Task 控制/重跑适配兼容九个当前模板的真实节点定义，不会把它们转成 DAG；不能据此宣称 S01–S09 的实际业务节点、知识库引用、AI 验收、权限、人工输入完整路径或产物交付已逐场景验收通过。各场景的完整 TaskService `create_run()` 路径需要补齐隔离的授权知识库、模板所需能力、fake verifier/执行器和 artifact writer，逐场景调用现有门禁并记录结果。

全套测试最终结果：

| 类别 | 结果 |
|---|---:|
| 通过 | 340 |
| 失败 | 43（与开始时 43 个失败 nodeid 完全一致；无新增/消失） |
| 跳过 | 14 |
| Subtests | 30 passed |

已有 43 个失败涉及工具输出 schema、输入/重启测试、S02/S07 输出契约与 RAG、模板版本/目录等。详见基线与最终 pytest 日志；逐项 nodeid 集合相同。全量测试不是全绿。

阶段测试文件：`tests/task_sop_control_test.py`、`tests/task_sop_template_retry_test.py`、`tests/task_sop_test.py`、`tests/task_clarification_test.py`、`tests/task_mvp_pipeline_test.py`、`tests/task_dag_editing_test.py`、`tests/task_postgres_sop_test.py`。

## 8. API 调用链和状态持久化证据

实际 Task API 路由保持在现有 Task Router：

- `POST /tasks/runs/{run_id}/pause`
- `POST /tasks/runs/{run_id}/continue`
- `POST /tasks/runs/{run_id}/step`
- `POST /tasks/runs/{run_id}/stop`
- `POST /tasks/runs/{run_id}/nodes/{node_id}/retry`
- `POST /tasks/runs/{run_id}/input`

API 测试通过 FastAPI Router、`get_current_user_id` 测试身份覆盖和真实 `TaskService` 实例发请求；不是直接调用独立 Scheduler。SOP worker 经过 `claim_run()`、`run.claimed`、lease heartbeat、fenced `transition_run()`，逐节点把 TaskSOPStep 状态、NodeRun、事件和 Run 版本一起保存。Run checkpoint 不依赖当前 Task 模板重建，而使用冻结 Run 的 node ID 映射及指纹。

测试用 Memory `TaskStore` 与 fake executor/verifier 是隔离单元/Service 测试，不代表真实数据库验收。

## 9. 本阶段相对工作区起始快照的修改文件

与阶段开始白名单文件副本比较，本次 Task 白名单新增或修改共 18 个文件，其中实现/测试文件 17 个、R0 审计文件 1 个：

- `examples/agent_service/task/R0_task_control_gap_audit.md`
- `examples/agent_service/task/_models.py`
- `examples/agent_service/task/_postgres_store.py`
- `examples/agent_service/task/_redis_store.py`
- `examples/agent_service/task/_router.py`
- `examples/agent_service/task/_service.py`
- `examples/agent_service/task/_sop.py`
- `examples/agent_service/task/_store.py`
- `examples/web_ui/frontend/src/api/task.ts`
- `examples/web_ui/frontend/src/pages/task/index.tsx`
- `examples/web_ui/frontend/src/pages/task/task-run-panel.tsx`
- `tests/task_clarification_test.py`
- `tests/task_dag_editing_test.py`
- `tests/task_mvp_pipeline_test.py`
- `tests/task_postgres_sop_test.py`
- `tests/task_sop_control_test.py`
- `tests/task_sop_template_retry_test.py`
- `tests/task_sop_test.py`

本验收报告是额外的交付文档，不计入以上 18 个文件；按“代码、测试、审计、验收报告”合计，本次新增或修改 19 个 Task 白名单文件。

## 10. Redis/PostgreSQL 验收与剩余生产风险

没有配置专用测试连接。明确执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/task_postgres_sop_test.py tests/task_dag_redis_integration_test.py -q -rs
```

结果：8 skipped，原因分别是 `Set a disposable PostgreSQL Task test URL`（3 项）和 `Set LXSCOPE_TASK_TEST_REDIS_URL to run real Redis tests`（5 项）。完整 Task 套件其余 6 个 skip 属于套件既有 skip。

进入真实验收前必须由用户/环境提供：

1. 专用、可销毁的 PostgreSQL 实例及空的 Task 专用测试数据库；`LXSCOPE_TASK_TEST_DATABASE_URL` 指向该实例。禁止从开发/生产 URL 自动推断测试目标。
2. 专用 Redis 实例（首选）或明确隔离的 Redis DB/命名空间；`LXSCOPE_TASK_TEST_REDIS_URL` 指向测试实例，且测试代码要求的隔离确认变量 `LXSCOPE_TASK_TEST_REDIS_ISOLATED` 显式启用。禁止对未知实例执行 FLUSHDB/FLUSHALL。
3. 在上述隔离资源中执行 CAS 并发、单 Run claim 竞争、租约续期/fencing、旧 Owner 拒写、重复 request_id、终态保护、状态/事件原子提交、新 Service 读取以及独立子进程强杀/重启恢复。外部副作用未知的节点必须验证人工确认门禁。

Redis/PostgreSQL 真实事务/脚本、多进程 claim/fencing、持久化重启恢复和数据库旧数据兼容仍为 **BLOCKED**。不可由 Memory、fakeredis 或 SQLite 结果替代。因此，生产启用条件不满足。

## 11. Git 白名单检查与最终判断

基线保存了完整 `git status --short` 和 `git diff --name-only`。结束时再次执行并与基线比对：15 条白名单外既有状态行完全相同，白名单外新增/变化为 0；本次 18 个 Task 白名单 delta 均在允许路径内。开始时已有的 `admin_api.py`、管理前端、Sales Console、`docker-compose.dev.yml`、输出/临时文件等状态保持原状，不归因于本次改动，也未清理。

**进入后续发布/生产启用：NO-GO。** Task 内 SOP 节点控制和重跑适配、Task 前端编译以及合成节点级测试已完成；完整 43 项既有失败仍在，浏览器验收 BLOCKED，真实 Redis/PostgreSQL 和 S01–S09 完整业务链验收 BLOCKED。DAG 开关及九大业务模板未被改变；不得据本报告宣称生产就绪。
