# Task P2-F DAG 前端集成验收报告

日期：2026-10-19  
范围：Task 前端、Task API 客户端；未修改后端、主框架、导航、Sales Console 或业务模板。

## 源码审查与开发计划

现有 Task 页面由 `src/pages/task/index.tsx` 装配，串行步骤仍由 `TaskFlowEditor` 编辑，运行详情及停止/整次重试位于 `TaskRunPanel`。`src/api/task.ts` 已提供 Task CRUD、Run、事件 SSE 和旧整次重试客户端，但原先没有 DAG 字段、节点 attempt 字段及 Pause/Continue/Step/Stop/节点重跑方法。页面收到运行事件后会调用 `GET /tasks/runs/{run_id}` 同步 Run 快照，可沿用该路径更新 DAG 节点状态。

根据 Task 后端模型与路由核对出的契约：

- Graph：`schema_version`、`edges[{source_node_id,target_node_id}]`、`input_bindings[target][name]{source_node_id,source_value}`、`max_concurrency`、`resource_limits`。
- Run：`engine`、`graph`、`run_version`、`control_mode`、`status`。
- 节点运行记录：`attempt`、`attempt_history`；历史 attempt 有 `source`、`status`、输入/输出、错误与时间。
- 控制请求：`request_id`、`expected_run_version`、`side_effects_verified`。
- 控制路由：`POST /tasks/runs/{run_id}/{pause,continue,step,stop}`；局部重跑：`POST /tasks/runs/{run_id}/nodes/{node_id}/retry`。服务端返回 409 表示 Run 状态/版本冲突；422 表示参数或工作流约束不满足。

实现计划及结果：扩展 Task API 类型与方法；新增 Task DAG 画布和依赖布局工具；在 Task 页面根据 Run 的 `engine`/Task 的 `graph` 展示只读 DAG；在运行面板提供断点控制、失败节点原因、attempt 历史及局部重跑入口。既有 `TaskFlowEditor` 串行路径和导航没有改动。

## 实现结果

- DAG 画布按依赖层级显示节点、连线、前驱依赖、多前驱汇合、输入绑定、并发上限和运行状态；节点完成后随已有 SSE + Run GET 同步更新。
- Pause、Continue、Step、Stop 使用服务端要求的请求体。按钮依据 Run 状态与 `control_mode` 显示；Step 在失败节点或活动节点存在时禁用，并说明每次仅派发一个 ready 节点。Stop 需要二次确认，并明确显示为终止操作。
- 控制请求发送期间禁用重复点击。409 后读取最新 Run 并提示用户复核；422 显示参数错误。
- 失败 Run 中显示 failed/succeeded 节点的重跑入口、失败原因、历史 attempt 与受影响下游节点。Tool/Python 节点重跑先展示副作用风险确认，只有确认后才发送 `side_effects_verified: true`；服务端安全门禁仍保留。
- Task 有 Graph 时串行节点编辑器不显示，避免把 DAG Task 当作 SOP 流程编辑。九大模板未改动；非 DAG Task 仍使用原 `TaskFlowEditor` 和原有 Run 控制路径。

## 验收结果

| 项目 | 结果 | 证据/限制 |
|---|---|---|
| TypeScript 与前端生产构建 | PASS | `pnpm run build` 中 `tsc -b` 通过，Vite 转换 6900 个模块并完成打包。构建仅报告既有大 chunk 警告。 |
| 本次修改文件 ESLint | PASS | 对 `src/api/task.ts`、Task 页面、DAG 组件/工具及运行面板运行 scoped ESLint，最终通过。 |
| DAG 页面/API 行为自动化测试 | BLOCKED | `package.json` 没有 test 脚本，也没有前端测试运行器；本阶段未增加依赖。 |
| 本地浏览器页面 | BLOCKED | `http://localhost:5173/task` 被应用重定向至 `/login`。浏览器显示用户名、密码和登录控件；没有输入凭据或提交表单。 |
| DAG 正常执行与并行分支 | UNVERIFIED | API 调用仍通过现有 `taskApi.run`，事件刷新链路已复用；没有已登录的隔离测试身份/Task 数据，未发起 Run。 |
| Pause / Continue / Step / Stop | UNVERIFIED | UI 与精确 API 路由已接线并通过构建；未对本地服务发出有状态请求。 |
| 失败节点局部重跑与副作用确认 | UNVERIFIED | UI 发送版本、幂等及副作用字段；未用实际 Run 验证结果与门禁。 |
| SOP 与九大模板兼容、左侧导航 | SOURCE-ONLY | 导航和模板未改，非 DAG 分支继续渲染原组件；因浏览器未登录，未完成人工交互回归。 |
| 生产 DAG 启用 | NOT ENABLED | 未修改前端/后端开关、环境变量或服务端权限设置。 |

## BLOCKED：DAG 图编辑

服务端没有在 Task、Run、校验响应或独立能力接口中返回当前 `LXSCOPE_TASK_DAG_ENABLED` 状态。源码显示 Task 创建/更新可以保存 Graph，而 DAG 开关是在 `create_run` 阶段检查。仅调用现有 `/tasks/validate` 也不能证明运行期开关已打开。因此前端无法判断服务端当前是否允许图编辑；图画布保持只读，并且 DAG Task 不显示串行节点编辑器。解除阻塞需要后端在允许的接口响应中提供经过授权的 DAG 能力状态，或提供受服务端权限保护的 Graph 编辑接口；本阶段受“禁止修改 Task 后端”约束，未实施该项。

## 修改文件

- `src/api/task.ts`：Graph、Run control、attempt 类型及控制/重跑客户端方法。
- `src/pages/task/index.tsx`：Run 控制请求、幂等/版本冲突处理、重跑确认及 DAG 展示条件。
- `src/pages/task/task-dag-canvas.tsx`：Task 专属只读 DAG 画布。
- `src/pages/task/task-dag-utils.ts`：依赖层级布局和下游节点计算。
- `src/pages/task/task-run-panel.tsx`：断点控制、节点重跑、错误与 attempt 历史展示。
- `src/pages/task/task_dag_p2f_acceptance_report.md`：本报告。

对照阶段开始保存的 `git status --short` 快照，本阶段新增的 tracked diff 仅为上述 `src/api/task.ts`、`src/pages/task/index.tsx`、`src/pages/task/task-run-panel.tsx`；新增的 untracked 文件仅为 DAG 组件、布局工具和本报告。开始前已有的后端、Sales Console、管理页面及临时产物变更仍在状态列表中，未修改或清理。`git diff --check` 未发现空白错误；Git 对三个被编辑的原有文件提示 LF/CRLF 转换警告。

## 进入下一阶段判断

前端类型、打包和静态检查通过，DAG 运行控制与节点重跑已经接线。P2-F 不能标记为完整验收通过：图编辑因缺少服务端开关能力字段而 BLOCKED；真实 DAG 执行、并发、断点控制、重跑以及 SOP 浏览器回归因测试身份和数据缺失而未实测。前端未启用生产 DAG。完成这些端到端验收前，不建议将 P2-F 标为 PASS。
