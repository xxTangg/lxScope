# R0 Task 统一节点重跑与断点执行差距审计

审计日期：2026-10-22  
分支：`integrate/dev_l`  
阶段开始 HEAD：`e63c3b80278dba2db5ddfd8e195b988481cabd1e`

## 范围与工作区基线

审计前已保存 Git 状态、staged/unstaged 补丁及 Task 白名单文件副本至系统临时目录 `C:\Users\15452\AppData\Local\Temp\lxscope-task-control-baseline-20261022-165208`。该快照显示 Task 后端、Task 前端、Task 测试及多个白名单外文件在本次任务开始前已存在修改；这些既有改动不归因于本次工作，也不应覆盖。阶段开始状态的完整列表见该目录 `git-baseline.txt`。

未经本次修改的全量 `tests/task_*.py` 基线：**43 failed, 330 passed, 14 skipped, 21 subtests passed**。测试日志保存在同一临时目录 `pytest-task-baseline.txt`。测试进程未设置 Redis/PostgreSQL 测试连接变量，未连接真实模型、MCP 或数据库。历史 P2-G 报告中的 40 项失败与当前阶段同环境基线不同，R5 必须按本阶段节点 ID、错误类型及断言逐项比较，不能按数量判断。

## 代码路径证据

- `TaskService.create_run()` 将无 graph 的 Task 冻结为 `engine="sop"`，保存 Run 快照后调用 `_schedule_run()`。
- `_schedule_run()` 对 DAG 调用 `_execute_dag_run()`，对普通 Task 调用 `_execute_run()`；后者通过 `_advance_sop()` 将完整线性步骤序列交给 AgentScope `SOPEngine.reply_stream()`。
- `TaskSOPStep` 负责原有执行回调、`verify_node` AI 验收、显式人工输入以及模板专属输入校验。该验证/输入/产物路径必须保留。
- SOP 的 `_checkpoint_sop()` 当前按 `zip(run.nodes, engine.state.steps, current.node_runs)` 的位置投影；人工输入恢复也按 `request.node_id` 查位置索引。Run 保存了节点定义快照，但 SOP 状态自身没有稳定节点 ID 映射/快照指纹。
- `pause_run()`、`continue_run()`、`step_run()`、`stop_run()` 均拒绝 `engine != "dag"`；`retry_node()` 只接受带 graph 的 DAG Run。
- DAG 有单节点 `SOPEngine` 适配和 `transition_run()` CAS/事件原子提交、claim/lease/fencing；SOP 的检查点、回答恢复、失败和完成仍通过普通 `update_run()` / `update_node_run()` 写入，执行器不获取持久化 claim。
- Lazy recovery 仅覆盖开启开关后的 DAG Run；SOP 读取路径不自动恢复已排队/中断 Run。Stop 终态与 Pause 的持久化语义也只在 DAG 路径有完整实现。
- Task Router 已暴露 `/runs/{run_id}/pause|continue|step|stop` 和 `/runs/{run_id}/nodes/{node_id}/retry`，但服务端限制为 DAG；若能力端点/API 字段需调整，仅在 Task Router、Task models 和 Task client 内完成。

## R0 差距结论

1. DAG 控制 API 不能直接覆盖普通 SOP；需要在 Task Service 层按 `engine` 分流，保留 DAG 调度语义，同时给 SOP 增加安全的节点边界控制。
2. SOP Step 不能通过把整个 `SOPEngine` 当成一次 Step 来实现单步；应使用现有官方 SOP 状态机并通过 Task 适配器选择一个节点，再以 Task 的稳定 `node_id` 合并持久化状态，避免下游被派发。
3. Run 创建时应将节点 ID 顺序/指纹和初始 Task SOP 状态写入 Run 快照。旧 Run 无该映射时，仅当旧状态长度与 Run 节点数一致且快照结构可验证时按既有 frozen Run 顺序兼容恢复；否则 fail closed，不从当前 Task 模板猜测节点位置。
4. SOP 的控制、节点投影及事件须采用 CAS 原子转换并遵从终态/命令幂等；跨 Service/进程恢复需要 Task store claim/lease/fencing。只在控制请求层加局部 asyncio 锁不足以满足并发安全。
5. 局部重跑应基于 SOP 有序后继集合保留成功前驱、失效所有受影响后继、保留 attempt 历史；Tool/Python/MCP 等可能产生副作用的节点要求显式确认。外部副作用状态未知时 fail closed。
6. 统一路径必须继续调用 `TaskSOPStep`、现有 AI verifier、人工输入关联、知识库授权/证据、输出契约验证及 Artifact 交付检查。不能以通用模拟器替代九大模板验收。

## R1–R5 执行计划

- **R1**：在 Task store 范围内扩展可用于 SOP 的 CAS/事件/claim 原语；为 SOP Run 初始化稳定节点快照元数据；服务端 API 控制按 engine 统一但保持 DAG/SOP 各自推进方式；保持 DAG 默认开关不变。
- **R2**：实现 SOP 的节点边界 pause/continue/step/stop 与恢复适配；Step 只允许一个 ready 线性节点；状态恢复按 Run 快照 node ID 映射。
- **R3**：实现 SOP 同 Run 节点局部重跑、attempt 归档、下游失效以及副作用安全门禁；保留 AI verifier 的自动拒绝尝试与人工重跑来源差异。
- **R4**：在 Task 页面和 `api/task.ts` 复用现有 DAG 控件，依据服务端能力分别开放 SOP/DAG 控制和重跑；不改导航及场景模板。
- **R5**：重跑 DAG/SOP 专项与全量 Task 测试，按节点 ID/异常/断言与基线比较；分别验收 S01–S09；真实隔离 Redis/PostgreSQL、登录态浏览器若当前不可用则明确 BLOCKED，不用 Memory/SQLite/模拟登录冒充。

## 当前 R0 修改结果

本阶段仅新增本审计文件。R0 阶段结束执行 `git diff --name-only` 与 `git status --short`，应显示该文件新增且其他状态与保存的阶段开始快照一致。R0 未发现必须修改白名单外文件的功能依赖；后续如果真实执行安全要求无法在 Task 内满足，将停止相应功能并标记 BLOCKED。
