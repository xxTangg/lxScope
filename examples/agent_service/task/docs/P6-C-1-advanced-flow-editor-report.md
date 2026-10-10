# P6-C-1：Task 高级流程编辑与自定义副本交付报告

## 范围与结果

在 `integrate/dev_l`、基准 `c0300de` 上完成标准模板 Task 的私有自定义副本流程。没有新增节点类型、SOP 调度逻辑或依赖，也没有改 AgentScope 官方代码、共享认证模块、数据库迁移或全局页面。

标准模板实例、自由 AI Planner 任务和自定义流程使用可选 `workflow_mode` 区分。旧 Task/TaskRun 没有该字段时仍以 `None` 读取；标准模板任务继续依赖 `source_template_id` 和版本走旧的锁定规则。自定义副本保留模板 ID、版本、输入及节点绑定，并增加来源 Task ID 与修订号。

## 实现文件

| 文件与位置 | 修改内容 |
|---|---|
| [`_models.py`](../_models.py:43) | 增加三种可选工作流模式；TaskRecord、TaskRunRecord 增加兼容旧数据的来源追溯字段（TaskRecord 从 422 行，TaskRunRecord 从 489 行）。 |
| [`_service.py`](../_service.py:198) | 创建 Task 时记录模式；模板来源任务拒绝 Planner 再生成；437 行新增私有自定义副本深复制和新 ID 映射；运行快照写入工作流模式及模板/来源 Task 追溯字段（create_run 从 913 行）。 |
| [`_router.py`](../_router.py:330) | 模板来源 Task 请求 Planner 时返回 409；365 行新增需认证的 `POST /tasks/{task_id}/custom-flow-copy`，按所有权读取并返回 201/404/409。 |
| [`_template_validation.py`](../_template_validation.py:145) | 保持普通模板实例的原有限制；473 行新增自定义模式校验：来源版本和映射必须有效，节点 ID 有界且合法、Input 问题键唯一、最终节点可输出；来源场景承载必需能力的 Tool 节点必须保留且不得改型，必需 Tool 节点相对顺序不得改变；额外节点不能声明/冒用模板能力或未支持能力。继续执行通用节点、变量、Schema、Artifact 和实际 Tool 参数校验。 |
| [`task.ts`](../../../web_ui/frontend/src/api/task.ts:410) | 增加工作流模式/来源追溯类型及自定义副本 API 调用。 |
| [`index.tsx`](../../../web_ui/frontend/src/pages/task/index.tsx:34) | Task 草稿保留来源模式；650 行实现另存副本（有未保存修改时先保存）；详情页展示流程类型、模板和来源修订信息，标准模板页提供“另存为自定义流程”入口（906 行起）。 |
| [`task-flow-editor.tsx`](../../../web_ui/frontend/src/pages/task/task-flow-editor.tsx:67) | 自定义模式复用自由流程编辑器，新增唯一节点 ID、四种现有节点类型的合法默认配置、增删/类型切换/上移下移；对必需能力 Tool 节点的删除、改型和相互换序禁用并说明原因。保留 Artifact 在最终节点的约束；不能合法重排或在 Artifact 后添加节点时会提示先处理产物配置。 |
| [`task_custom_flow_test.py`](../../../../tests/task_custom_flow_test.py:119) | 新增 6 项 Task 专项测试，覆盖副本隔离、Planner 边界、节点编辑、依赖/数量/必需 Tool 拒绝、Preflight 和 Run 来源快照、旧记录兼容。 |
| [`P6-C-1-advanced-flow-editor-report.md`](./P6-C-1-advanced-flow-editor-report.md:1) | 本交付报告。 |

### 随本阶段一并保留的既有 Task-only 改动

工作区进入本阶段前已有模板可创建/编辑而不要求 Tool 在线、改为运行前 Preflight 的未提交 Task 改动。它们是当前复制/编辑流程和专项测试的前置条件，已保留在 Task 白名单范围内：

- [`_template_compiler.py`](../_template_compiler.py:84)、[`_template_service.py`](../_template_service.py:58)：实例化延后 Tool 就绪检查。
- [`task-template-create-dialog.tsx`](../../../web_ui/frontend/src/pages/task/task-template-create-dialog.tsx:126)、[`task-template-preview.tsx`](../../../web_ui/frontend/src/pages/task/task-template-preview.tsx:20)：创建说明和可见使用按钮。
- [`task_template_test.py`](../../../../tests/task_template_test.py:906)：验证缺 Tool 时可创建/编辑且 Preflight 阻断运行。

## 模式与执行约束

1. 系统模板仍是只读目录内容，九个模板 JSON 未修改。
2. 标准模板 Task 仍由原模板验证器保护必需节点、类型、受控内容和原流程顺序。
3. 自定义副本拥有新 Task ID、新节点 ID、独立修订序列；保存不更改原 Task 或系统模板。`source_task_id` 与 `source_task_revision` 记录复制时来源。
4. 自定义模式仍保留 `source_template_id/version`、`template_inputs` 和服务端节点绑定。缺少来源版本或绑定不匹配时失败关闭；客户端不能通过 PATCH 改来源元数据。
5. 来源模板中承载必需能力的 Tool 节点必须存在、保持 Tool 类型和相对顺序；Tool 名称和参数可编辑，但运行前仍按来源能力契约检查 Schema、当前会话真实工具、用户权限、业务数据验证状态和外部副作用。删除或改型会被服务端拒绝。
6. 自定义节点不能声明模板能力键或借用模板未支持能力。普通 Tool 仍需在当前会话实际存在并由 Task Preflight 检查；没有 Tool 的模板仍可实例化、复制和保存编辑后的草稿。
7. 节点继续按线性顺序运行，最多 8 个。`{{answers.*}}` 只能引用前序 InputStep 字段；Artifact 只能位于最终非 InputStep。结构化 Schema 保留并由现有结果校验器验证；切换成 InputStep 前必须先移除最终文件产物。
8. 运行快照在创建时保存模式、来源模板版本和来源 Task 修订信息。现有 TaskRun 快照机制未变，编辑 Task 不会改写已持久化的 Run 节点快照。
9. S03 v1 及所有其他模板文件未修改；InputStep 仍只是收集输入，不展示或记录为正式审批。自由任务仍走原 Planner，模板实例和自定义副本均不会调用 Planner。

## 测试及验证

- `.\.venv\Scripts\python.exe -m pytest tests/task_custom_flow_test.py -q`：6 passed。
- 全部可收集 Task 专项测试（排除 `task_input_protocol_test.py`）运行结果：116 passed、2 skipped、2 个子测试通过。两个 skip 来自 PostgreSQL 测试没有配置一次性测试数据库 URL，不能视为真实 PostgreSQL 集成验证。
- 收集完整 Task 测试目录时，`task_input_protocol_test.py` 因环境没有安装 `fakeredis` 而在导入期失败；本阶段没有添加依赖。该模块未被描述为通过。
- `pnpm build`（`examples/web_ui/frontend`）：通过，TypeScript 与 Vite production build 成功；构建报告已有的单个大 JS chunk 警告。
- Ruff：当前 Python 环境无 `ruff` 模块，静态 lint 未运行。
- 浏览器：尝试在 Codex in-app browser 打开 `http://localhost:8000/task`，实际跳转到 `http://localhost:3001/sign-in?app_id=...`。当前浏览器没有已认证会话，因此未完成复制、编辑、保存和 Preflight 的真实浏览器交互；本报告不将 API/React build 测试冒充为浏览器验收。
- 新增的 Tool/业务验证测试使用 Task 范围 fake executor，只验证管理层保护、Preflight 阻断和 Run 追溯字段。它不代表九个场景已接通真实业务系统，也不代表 S03 专家推荐闭环已完成。

## 白名单与风险

本阶段新增代码仅涉及 `examples/agent_service/task/**`、`examples/web_ui/frontend/src/api/task.ts`、`examples/web_ui/frontend/src/pages/task/**` 和 `tests/task_*`。未修改 `src/agentscope/**`、Task 外后端、共享 API Client、认证、导航、迁移、依赖或构建配置。原有 `sales-console/**` 和其他非 Task 未提交内容未纳入本阶段提交。

真实浏览器交互仍待有效登录会话后验证；`fakeredis` 专项和 PostgreSQL 专项也因当前环境依赖/数据库缺失未能完成。当前无正式 HumanApproval、跨系统副作用授权或业务系统接入，本阶段没有为这些能力作出成功承诺。

## 验收结论

- 自定义副本 API、来源追溯、节点编辑和服务端安全校验：**PASS**
- Task 专项回归（排除无法导入的 fakeredis 测试模块）：**PASS，116 passed；数据库专项 2 skipped**
- 前端生产构建：**PASS**
- 浏览器交互：**未验证，因本地登录重定向**
- 白名单外修改：**否**
- P6-C-1：**代码和自动化测试通过；由于浏览器登录及环境专项缺口，完整端到端验收未全部完成**

建议提交信息：`feat(task): support advanced editing for custom task flows`
