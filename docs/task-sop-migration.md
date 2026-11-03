# Task 接入官方 SOP

## 合并基线

- 本地二开基线：`bfc77a9`，共同祖先：`3500806`。
- 官方主分支：`48c4b9ce0f9a3f86e5c3385d43cd2220245eef10`，版本 `2.0.10dev`。
- 使用完整 Git 三方合并，包含 SOP SDK、SOP 服务、AskUser 和该基线之后的官方功能与修复。
- 三处冲突同时保留了本地 Agent 工具预算、技能筛选与官方 usage/SOP 参数。

官方 SOP 仍是实验功能；此实现基于上述提交，不在运行时自动获取新版。

## 实现边界

Task 保留定义、规划、编辑、版本、权限、知识库、运行历史、事件和文件产物。
`task/_sop.py` 的 `TaskSOPStep` 继承官方 `SOPStepBase`，适配 Agent、Tool、Python 和 Input。
官方 `SOPEngine` 唯一负责步骤顺序、验收重做、次数限制与挂起。
TaskService 将 `SOPRunState` 保存到原有 Run JSON，并将其投影为前端节点状态。
Task 的取消、排队和文件生成失败是业务生命周期，官方 SOP 的步骤状态没有 canceled 枚举。

已有运行默认识别为 `engine=legacy`，保持历史内容；新运行使用 `engine=sop`。
执行始终采用 Run 内冻结的节点定义；修改任务不会改变正在等待回答的运行。
Task 的 PostgreSQL/Redis 存储均复用 JSON 字段，不需要增加 Task 数据库列。
PostgreSQL 必须应用 `0004_task_awaiting_input.sql`，为 `task_runs` 和
`task_run_nodes` 的状态约束添加 `awaiting_input`。现有自动迁移会在服务启动时应用；
仅更新 Python 枚举或修改已执行的旧迁移，无法更新已有数据库约束。
使用官方 SQL 主存储的部署仍应按官方升级流程迁移其新增 SOP 表。

## 输入协议

Agent 返回 `AgentStepOutcome`，状态只能是：

- `completed`：有实际结果，随后进行验收。
- `needs_input`：必须携带问题数组；不验收、不消耗失败重做次数、不推进后续步骤。
- `failed`：明确失败，停止后续步骤。

流程也可以显式定义 `type=input` 的步骤，例如：

```json
{
  "name": "确认出差需求",
  "type": "input",
  "config": {
    "type": "input",
    "reason": "生成行程需要目的地和日期。",
    "questions": [
      {"key": "destination", "label": "目的地是哪里？", "required": true, "options": []},
      {"key": "date", "label": "出差日期？", "required": true, "options": []}
    ]
  }
}
```

运行挂起时，Run/节点状态为 `awaiting_input`，Run 的 `pending_input` 提供问题批次 ID、节点 ID、原因和问题。
前端显示必填表单和建议选项；提交接口是：

```http
POST /tasks/runs/{run_id}/input
Content-Type: application/json

{
  "request_id": "pending_input.id",
  "answers": {"destination": "北京", "date": "2026-10-12"}
}
```

成功返回 202，并继续同一个 Run。不存在或不属于当前用户的 Run 返回 404；
过期问题、未知字段、缺少必填信息、重复提交不同答案返回 409；无效请求结构返回 422。
同一问题、同一回答的重发是幂等操作，不能触发第二次执行，也不能回答之后的新问题。

问题、回答和执行状态均持久化；等待时释放执行协程。重建 TaskService 后仍能回答并继续原 Run，已完成步骤不重跑。
每个 Run 有本地/分布式执行锁，跨 worker 的取消通过消息总线通知执行者。
等待状态的 SSE 回放后结束，提交后重新连接；等待页面定期同步其他页面提交的回答。

## 执行器需求收敛

Agent 执行器先用结构化模型整理 `EffectiveRequirements`，合并按时间排序的回答，
以最新明确回答和业务范围为准。日期格式、同义字段和偏好由模型归并；字段 key、
别名与问题标签用于后续去重。未知可选项保持未知，不能改写为“无金额上限”或“无偏好”；
查询也不能扩大成购买、预订。执行与验收共用该摘要，验收同时核对最新原始回答。

只有 `status=unknown` 且有具体 `blocking_reason` 的必要字段才能挂起。
`provided`、`no_preference`、`not_applicable` 和未知可选项不阻塞。
默认每个 AgentStep 最多三轮补充，每轮最多三个问题；通过执行器构造参数
`max_clarification_rounds`、`max_clarification_questions` 调整，日期上下文默认使用
`task_timezone="Asia/Shanghai"`。这些规则属于 Task 执行器，不修改官方 `SOPEngine`。

已回答或已问过的必要字段不会重新询问；真正仍无法执行时返回明确失败原因。
已有足够信息时，即使旧运行已超出轮数也继续执行。模型在信息足够后仍提出问题时，
执行器仅允许一次纠正，再次追问会停止，不能把缺资料模板强制标成完成。

需求摘要、已问字段和停止原因保存在节点 `metadata.clarification`，复用现有 JSON 存储，
无需数据库迁移。TaskService 仅传递该状态和当前节点已回答轮数，重启后规则仍生效。
归并增加一次模型调用；缺必要字段时直接生成受控提问，不再调用执行模型。
`tests/task_clarification_test.py` 覆盖可选项放行、别名去重、复合字段、轮数限制、
模型纠正和服务重建后的挂起恢复；语义归并质量仍取决于所配置模型。

## 验收与数据绑定

Agent 使用结构化模型调用。旧任务中的 Agent 也会区分缺数据和真正完成，并默认进行业务验收。
新规划会生成输入步骤、验收条件与真实数据获取步骤，避免将“待获取”模板当成果。
验收未通过时，官方 SOP 只重做当前步骤，并把验收反馈传给执行者；`max_attempts` 限制尝试次数。
自定义执行器配置验收标准时必须实现 `verify_node`，不能静默跳过验收。

支持的变量：

- Agent/Tool：`{{user_input}}`、`{{previous_output}}`、`{{answers.destination}}` 等已定义输入字段。
- 兼容旧 Agent 提示词中的 `{user_input}`、`{previous_output}`。
- Tool 的完整回答变量按 JSON 基础类型绑定，再根据真实工具 schema 验证。
- Python：`previous_output`、`user_input`、`user_answers` 字典。

缺失变量会报错，不会带着占位符调用真实工具。用户数据不会再次被当作模板解析。
知识库原文独立传递，不参与模板解析；执行和验收使用同一份参考资料。
直接 ToolStep 仍遵守会话权限，不能绕过授权；原生 AskUser/状态注入工具不作为直接 ToolStep 提供，业务提问使用输入协议。
文件只在所有步骤完成并通过验收后生成。Tool/Python 使用绑定会话的 Workspace，产物按 Run ID 隔离路径。

## 验证

新增 `tests/task_sop_test.py`、`tests/task_input_protocol_test.py`：覆盖缺数据阻塞、同 Run 恢复、冻结定义、重复/过期/跨用户回答、验收重做、异常状态一致性、跨 worker 取消、Redis JSON 往返、结构化模型调用及 HTTP/SSE。
`tests/task_postgres_sop_test.py` 使用真实 PostgreSQL 验证旧数据库升级、历史保留、
约束覆盖所有运行/节点状态、无效状态拒绝，以及显式 Input 和模型提问的挂起、服务重建、
重复回答、同 Run 恢复。测试仅接受显式配置的 `LXSCOPE_TASK_TEST_DATABASE_URL`，
数据库名必须以 `lxscope_task_test` 开头；每项测试创建并清理自己的临时数据库。
同时运行官方 SOP、Chat/Agent 兼容、存储、模型、权限、工具和格式器相关回归测试。
本次验证共 658 项测试通过，另有 345 个参数化子测试通过；使用隔离目录、模拟模型与测试存储，未修改生产任务数据。
后续数据库兼容修复额外通过两项真实 PostgreSQL 集成测试，并在现有出差任务的新运行中
验证真实模型能生成问题、保存 `awaiting_input`，保持后续步骤未执行。
前端通过 TypeScript、ESLint 与生产构建；Vite 改用原生加载 `.mjs` 配置，避免 Windows 上将原生依赖当文本打包。

等待输入的运行可以在服务重启后继续；普通执行中的进程崩溃不自动重放有副作用的工具，仍需用户发起重试。
LLM 判断和验收依赖所配置模型的能力，关键业务约束可进一步增加确定性校验。

## 本地 Docker 更新

运行中的容器使用镜像内代码。代码合并后需重建服务镜像才会生效：

```powershell
docker compose build agentscope web-ui
docker compose up -d --no-deps agentscope web-ui
```

保持现有 `.env`、覆盖配置和数据卷。旧的出差规划历史不会自动重新执行；启动一次新运行即可使用输入协议。
