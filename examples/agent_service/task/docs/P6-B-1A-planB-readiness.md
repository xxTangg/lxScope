# P6-B-1A：Task 模板版本化与方案 B 就绪度

> 基线：分支 `integrate/dev_l`，提交 `fdc2193`。本阶段只实施 Task 模板版本解析，并分析审批方案；未开发或启用 HumanApproval。

## 1. 结论摘要

| 项目 | 结论 | 依据 |
|---|---|---|
| 模板版本化 | **PASS** | Registry 分离当前版本与显式版本文件清单；历史 Task 按保存的模板 ID/版本校验；新增模板仍只取当前版本。默认目录仍只发布九个场景，S03 v1 未改，也没有发布 v2。 |
| 审批状态方案 B | **GO（状态映射层面）** | `awaiting_input`、`queued`、`failed`、`running` 均已被 Task 模型和 PostgreSQL 当前约束接受。TaskRun 快照可以增加可选的 Task 专属审批字段，普通 Input 与审批可按字段和 API 分开识别，因此无需增加数据库生命周期枚举。原子存储与恢复尚未实现，不能据此声称审批就绪。 |
| 正式 HumanApproval | **BLOCKED** | 认证身份虽可由现有认证服务验证，但没有获批的 Task 审批角色/策略或自批规则；Redis 部署拓扑未知；当前 Store 无审批 CAS/统一审计事务，服务无 queued run 自动恢复/领取机制；S03 专家业务接口仍未提供。 |

方案 B 的“GO”只确认既有生命周期状态可承载候选审批状态。进入实现前仍须完成第 5–8 节中的策略、原子提交、恢复触发和业务接口前置条件。

## 2. 本阶段源码实现

### 2.1 变更文件

| 文件 | 修改 |
|---|---|
| `examples/agent_service/task/_template_registry.py:15-47, 102-235` | 增加显式的 `(template_id, version, filename)` 版本文件清单与每场景当前版本清单；新增精确历史解析和当前版本解析；未知 JSON 仍报错；文件内容必须与清单中的 ID/版本一致；保留 `get_template()` 作为当前版本兼容入口。 |
| `examples/agent_service/task/_template_service.py:72-96` | 新实例化显式解析当前版本，版本请求仍需与当前模板精确相等；服务路径仍直接编译并创建 Task，不调用 Planner。 |
| `examples/agent_service/task/_template_validation.py:131-164` | 已有模板 Task 按 `TaskRecord.source_template_id` 和 `source_template_version` 解析 canonical 历史版本，不再错误地和最新版比较。 |
| `examples/agent_service/task/_router.py:169-204` | 模板详情路由显式读取当前版本；列表与详情仍只面向当前场景版本。 |
| `tests/task_template_version_test.py:90-283` | 新增版本化专项测试：九场景目录、历史与当前解析、历史 Task 校验、未知版本、未登记 JSON、实例化不调用 Planner、模板副本隔离、Run 节点快照隔离、8 节点上限。 |
| `examples/agent_service/task/docs/P6-B-1A-planB-readiness.md` | 本交付报告。 |

没有修改前端、数据库迁移、共享认证、其他业务模块、AgentScope 主代码或构建/依赖文件。工作区原有的非 Task 修改与未跟踪文件保持原样，未纳入本阶段提交。

### 2.2 版本目录行为

Registry 现在分别维护：

1. `TEMPLATE_VERSION_FILES`：所有已声明版本及文件的允许清单。
2. `CURRENT_TEMPLATE_VERSIONS`：每个场景唯一的当前版本，并决定 gallery 顺序。

默认两份清单当前都只包含九个 v1 文件；本阶段没有在默认目录添加第二个版本。测试通过独立临时目录声明同一 S03 的 v1、v2，验证 Registry 能精确解析旧版且 gallery 只返回指定的 v2。目录中任何不在 manifest 的 `.json` 仍会被拒绝。

新实例化继续读取当前版本并校验客户端版本。现有 Task 的保存/运行校验读取其 provenance 对应的精确版本。未知的来源版本会失败关闭，不会回退到当前模板，也不会自动升级。TaskRun 创建时持有独立的节点定义快照；后续 Task 或模板变化不改写既有 Run 节点。

### 2.3 S03 v1/v2 兼容

`templates/s03_expert_matching.v1.json` 保持原始字节不变，继续是 8 节点、含 `collect_human_review` InputStep 的 demo/configurable 模板。工作树文件与基线 HEAD 的 Git blob ID 均为 `0442ffee06179570e46e611cf2a29b8045c772cc`。本阶段没有创建 `s03_expert_matching.v2.json`，没有把 Input 解释为审批，也没有更改模板数据格式。

后续若业务 Gate 通过，可在版本清单中同时登记 v1/v2，并将当前版本指向 v2。旧 Task 仍用 v1 canonical 规则；新实例化取 v2；旧 Run 继续使用自己的 `TaskRunRecord.nodes` 快照。此流程不迁移或升级用户已有 Task。

## 3. 方案 B 生命周期状态核验

源码中的 `RunStatus` 已包含 `queued`、`running`、`awaiting_input`、`failed`；`NodeRunStatus` 已包含 `awaiting_input` 与 `failed`（`task/_models.py:41-61`）。PostgreSQL migration `0004_task_awaiting_input.sql` 对 `task_runs` 和 `task_run_nodes` 的 CHECK 约束均允许 `awaiting_input`，原有约束也允许 `queued`、`running`、`failed`（`examples/agent_service/migrations/0004_task_awaiting_input.sql:7-23`）。这些值可继续沿用，不需要修改 migration。

| 审批业务语义 | 已有生命周期值 | 后续 Task 专属快照字段（本阶段未实现） | 必须保持的区分 |
|---|---|---|---|
| 等待审批 | Run/节点 `awaiting_input` | `pending_approval` 存请求；`pending_input` 必须为空 | 普通 Input 有 `pending_input`，审批有 `pending_approval`。运行详情和 UI 先按 Task 字段区分；Input API 在发现审批请求时拒绝。 |
| 审批通过，等待继续 | Run `queued` | `approval_decision.status="approved"`，并保留可信 actor、policy 和 request/digest 关联 | 仅决策端点可触发原 Run 恢复；普通输入端点不可批准。 |
| 审批拒绝 | Run/节点 `failed` | `approval_decision.status="approval_rejected"`，保留结构化理由和决定证据 | 不调度 Run；FAILED 是终态；重试只能创建新 Run 并重新审批。 |
| 审批后运行 | Run/节点 `running` | 保留已批准决定及对应 SOP step 状态 | 正常顺序执行，不能仅凭 Tool/Agent 文本跳过审批。 |

Pydantic 中当前不存在 `pending_approval` 或 `approval_decision`；`TaskRunRecord` 当前只有 `pending_input`（`task/_models.py:449-471`），因此上述是兼容性验证和后续协议要求，不是已交付字段。TaskRun 在 Postgres 以 JSON snapshot 保存（`task/_postgres_store.py:261-266`），在 Redis 以 JSON 字符串保存（`task/_redis_store.py:117-128`）；新增带默认值的可选字段可兼容旧快照。TaskService 当前把 `SOPPhase.AWAITING` 映射成节点和 Run 的 `awaiting_input` 并保存 `pending_input`（`task/_service.py:1215-1289`），后续必须增加审批专属投影，不能把审批请求写入 `pending_input` 或发出普通 `input.requested` 语义。

**状态映射结论：GO。** 既有枚举与数据库约束允许该映射，而且通过 TaskRun 快照字段可保留正式审批的独立业务语义。若后续实现需要新增 `awaiting_approval` 或 `rejected` 数据库状态，则会超出本方案 B，也会需要 Task 外 migration；本阶段及既定边界均不允许这样做。

## 4. 审批快照、原子提交与审计

候选 TaskRun JSON 字段：

```python
pending_approval: PendingApproval | None = None
approval_decision: ApprovalDecision | None = None
```

`PendingApproval` 至少绑定 `task_id`、`run_id`、`node_id`、唯一 `request_id`、`task_revision`、模板 ID/版本、待审包摘要及 `package_digest`。`ApprovalDecision` 至少绑定 request/run/node/revision/digest、决定类型、服务端生成的 `decision_id`/时间、可信 `AuthUser` 主体、已批准的 `policy_id`/版本和结构化拒绝理由。请求体不得接受 actor 身份作为可信值。审批数据必须同时保存在 TaskRun JSON 和结构化 TaskEvent 中，不能只留在自由文本或 Agent 输出中。当前两类模型和审批事件均不存在。

| Store | 当前源码事实 | Task 内目标实现 | 结论/限制 |
|---|---|---|---|
| 内存 `TaskStore` | 每个方法由一把 `asyncio.Lock` 保护；Run 更新、事件序号和 append 是不同方法（`task/_store.py:23-162`）。 | 新增 `commit_approval`，在同一锁内检查 pending/request/decision，保存 run 快照、生成事件序号并追加结构化事件，返回 committed/duplicate/conflict。 | 可作单进程测试实现，不能证明跨进程互斥。 |
| `PostgresTaskStore` | 每个 save/update/event 操作各自开事务；`update_run` 是读后保存；事件另行插入；`next_event_sequence` 单独读 MAX+1（`task/_postgres_store.py:373-443, 501-615`）。`task_events` 对 `(run_id, sequence)` 有唯一约束（`migrations/0002_lxscope_persistence_foundation.sql:166-181`）。 | 在一个 `engine.begin()` 中对 tenant/run/授权范围执行 `SELECT ... FOR UPDATE`；校验 pending 和 decision；更新 status 与 JSON snapshot；在同事务内分配序号并插入 approval event。重复同决定读回原结果，相反决定返回 conflict。 | Task 模块内可以实现同一事务提交，无需改表结构；需要按实际连接隔离/行锁测试。跨用户审批不可复用 owner-only 查询而绕过策略。 |
| `RedisTaskStore` | Run key 为 `lxscope:task:user:{user_id}:run:{run_id}`，事件 key 为 `lxscope:task:run:{run_id}:events`（`task/_redis_store.py:30-55`）。Run 使用 `SET`，事件使用 `RPUSH`；更新是 GET 后保存，序号为 `LLEN+1`，当前没有 WATCH/MULTI/CAS（`task/_redis_store.py:117-218`）。 | 单机 Redis 可用 WATCH/MULTI/EXEC 对 run 与 event keys 进行乐观 CAS，并在 EXEC 中写 snapshot 和事件；冲突后重读判 duplicate/conflict。 | 当前 key 没有 Redis Cluster hash tag，两个 key 的 slot 不保证相同；Cluster 下跨 slot MULTI/Lua 不能直接使用。部署拓扑未确认，因此 Redis 原子方案尚未验证通过。必须先确认 topology，再选同 slot key 设计或经验证的替代原子协议。 |

TaskRun snapshot 与 Postgres status 列必须在同一事务更新；不能把 `update_run()` 与 `append_event()` 的先后调用称为原子审批。Redis/内存也必须扩展为单个 store 协议方法，不能由服务层的 get-then-save 代替 CAS。

**审计边界：** 结构化 `ApprovalDecision` 加 `approval.approved`/`approval.rejected` TaskEvent 可满足应用层的结构化审计记录设计；当前 TaskEvent 不是 WORM 或防篡改审计。审计保留期、可读角色、脱敏、备份及签章等级仍需业务/安全方确认，不能宣传为不可抵赖签名。

## 5. 审批后的运行恢复核验

当前等待 Input 时，`TaskRunRecord` 保存 `sop_state` 和 `pending_input`；`answer_run` 校验 request ID 后，将原 Run 置为 queued 并恢复同一 Run。此能力只适用于普通 Input，不是审批 API 或审批恢复。

`TaskService._schedule_run()` 使用进程内 `asyncio.create_task`、`_active_runs`、`_run_users`（`task/_service.py:984-1001`）。等待 Input 的 `answer_run()` 会把同一 Run 置为 queued 后调度（`task/_service.py:1003-1066`）。没有启动时扫描 queued/running Run 的 Task recovery worker，也没有 Store 级运行领取/租约/CAS。`main.py` 禁止修改，故本阶段不能加入全局启动恢复入口。

| 故障点/动作 | 当前支持 | 后续可接受的 Task 内设计 |
|---|---|---|
| 审批等待期间服务重启 | Postgres/Redis 快照保存后可由 Task owner 查询；未运行中的等待 coroutine 无需恢复。当前无 approval payload/API。 | 按授权主体读取 durable `pending_approval`，继续等待决定；不伪造自动恢复。 |
| 决定提交后、schedule 前宕机 | 不支持自动恢复。queued 状态会留存，但没有启动扫描。 | 决定端点收到同 request 的幂等重试时读取既有 approved 决定并触发同一 Run；调度前通过 Store 原子领取/租约防止多实例重复派发。仍需有后续请求作为触发，除非另增 Task-only worker 生命周期。 |
| 同一批准并发重试 | 当前没有审批 CAS 或执行领取。 | `commit_approval` 只允许 pending→approved 一次；另以原子 claim/lease 保护调度。相同决定返回既有结果；反向决定冲突。 |
| queued Run 进程重启后无人重试 | 不自动执行。 | 要求显式 Task 内恢复触发（如授权的 Run 详情/重试触发）或 Task-only worker；确认触发责任方和扫描范围。不能声称现有自动恢复。 |
| Run 已 running 时宕机 | 当前无安全自动续跑，节点外部副作用可能重复。 | 未设计节点级幂等键/外部副作用去重前，不自动重放 running Run。先定义领取租约、checkpoint 边界和不确定副作用的终止策略。 |
| 批准后取消 | 当前没有 approval 状态。 | 取消 CAS 与审批/claim 状态共用同一 Store 原子边界；取消成功后禁止 claim，已经运行则走既有取消语义并保留批准事件。 |
| 拒绝后重新发起 | 当前没有审批拒绝语义。 | 原 Run 保持 failed + `approval_rejected` 结构化决策，不 schedule；重新发起必须创建新 Run 并重新走审批，不能通过 Input API 或 retry 继承批准。 |

Postgres 可用事务行锁实现单 Run 的 CAS/claim。Redis 只有在拓扑及 key slot 兼容后才能给出相同保证。要覆盖审批后崩溃，必须实现幂等触发与跨实例领取；当前只具备 Task 内可扩展边界，没有已交付的安全恢复机制。

## 6. 身份、授权及 S03 业务阻塞项

Task 路由当前依赖认证层返回的 `user_id`。`examples/agent_service/auth.py` 的 `AuthUser` 含 `id`、`role`、`capabilities`、`tenant_id`、`subject_id`、`organization_scopes`（`auth.py:42-53`）；认证服务的 `get_current_user()` 会校验 Bearer token 和账户状态（`auth.py:625-648`）。Task API 当前只取得 user ID（`task/_router.py:174-178, 191-204`）。角色 `admin/user` 和已有 capabilities 并未定义 S03 审批政策，Task 所有权也不能等同审批权限。

不修改共享认证模块的前提下，Task 路由技术上可从现有认证服务取得已验证的 `AuthUser`，再交给 Task-owned 默认拒绝策略。但以下业务决策仍未提供：

1. 哪类 reviewer 有权审批哪个 Task、组织或项目；是否允许 owner 自批。
2. 是否允许跨 owner/tenant 审批，谁可读取候选专家及冲突证据。
3. 拒绝理由、过期、撤回、重复审批、批准撤销的正式规则。
4. 审计保留、可见范围、脱敏和防篡改等级。
5. S03 专家目录查询、回避校验和确定性评分的真实接口、schema、授权及集成测试环境。

S03 v1 当前 8 个步骤的第 7 个 `collect_human_review` 是普通 Input，只收集意见。未来 v2 应替换该位置而不增加节点；没有真实专家查询/回避/评分服务前不得生成真实候选人或推荐结果。本阶段保持 v1 语义不变。

## 7. 不修改迁移文件的结论

对本阶段方案 B 状态映射，**可以不修改任何数据库迁移文件**：生命周期状态都使用已存在的枚举和 CHECK 值；审批请求、决定、可信 actor/policy、证据摘要作为 TaskRun JSON 中带默认值的可选字段保存，结构化事件写入既有 TaskEvent 表/Redis 事件列表。

此结论依赖于继续使用现有生命周期状态。不得新增 `awaiting_approval`/`rejected` 数据库值；若未来业务坚持独立数据库枚举，本方案就不成立，且需要重新审批边界和迁移范围。本阶段不触碰任何迁移。

## 8. P6-B-1 后续编码顺序

只有在业务方确认审批策略、审计约束、Redis topology 和恢复触发方案后才进入。拒绝所有未确认策略，不从用户传入字段推断 actor/policy。文件顺序如下：

| 顺序 | Task 白名单文件 | 修改目的 |
|---:|---|---|
| 0 | 业务方签字确认（不改代码） | 冻结 reviewer 授权来源、self-approval、跨用户/租户范围、拒绝/撤回规则、审计和 S03 真实接口契约；Redis topology 及审批后恢复触发需有可测试结论。缺一项则停在 BLOCKED。 |
| 1 | `examples/agent_service/task/_approval_policy.py`（新增） | Task-owned 默认拒绝；仅基于服务端验证的 `AuthUser`、Task/run/tenant 和已批准 policy 授权。 |
| 2 | `examples/agent_service/task/_models.py` | 增加 HumanApproval 节点配置、PendingApproval、ApprovalDecision；TaskRun 新增 optional approval 快照字段，复用现有生命周期枚举。 |
| 3 | `examples/agent_service/task/_store.py` | 在 Store 协议中定义 approval CAS/决定事件和 run claim/lease 原子操作；内存实现同锁完成。 |
| 4 | `examples/agent_service/task/_postgres_store.py` | 单事务、tenant/run 授权范围行锁、decision/status/snapshot/event/sequence 一致提交，以及幂等重试和调度 claim。 |
| 5 | `examples/agent_service/task/_redis_store.py` | 在 topology 确认后实现 WATCH/MULTI 或同 slot 原子脚本；验证 CAS、事件序号、claim/lease 和相同/冲突重复提交。Topology 不兼容时不得伪称通过。 |
| 6 | `examples/agent_service/task/_sop.py` | TaskSOPStep 使用官方 `SOPPhase.AWAITING` 停放审批，批准后从持久化决定恢复；拒绝写结构化失败并禁止产生继续 submission。 |
| 7 | `examples/agent_service/task/_service.py` | 生成绑定 digest 的 pending；校验/提交决定；原 Run 恢复；接入 claim/lease、可触发恢复、取消/拒绝及 retry 防绕过；普通 Input 路径保持隔离。 |
| 8 | `examples/agent_service/task/_router.py` | Task 专属审批端点；服务端提取 AuthUser；请求体不接受 actor/policy；授权后才展示/提交 pending；跨 owner 查询只走策略过滤。 |
| 9 | `examples/agent_service/task/_template_models.py` | 把新增 HumanApproval template config 加入严格类型联合；不改变旧模板 schema。 |
| 10 | `examples/agent_service/task/_template_compiler.py` | 编译已批准配置到 Task-owned 节点，不调用 Planner、不绑定假 Tool。 |
| 11 | `examples/agent_service/task/_template_registry.py` | 仅在 Gate 通过后显式登记 S03 v2 并将当前版本指向它；历史 v1 继续留在 manifest。 |
| 12 | `examples/agent_service/task/_template_validation.py` | 按 Task 来源版本校验，锁定审批身份/决定配置，保持必需节点、顺序和 8 节点规则。 |
| 13 | `examples/agent_service/task/templates/s03_expert_matching.v2.json`（新增） | 在 8 节点内用 HumanApproval 替换 v1 的第 7 个普通 Input；只引用有真实 owner/schema 的接口。v1 不覆盖。 |
| 14 | Task 专项测试 | 新增/扩展 `tests/task_approval_test.py`、`tests/task_redis_approval_test.py`、`tests/task_sop_test.py`、`tests/task_postgres_sop_test.py`、`tests/task_template_version_test.py`；覆盖身份伪造、self-approval、跨用户/租户、并发 approve/reject、幂等、事务中断、恢复、拒绝不执行后续、v1/v2 和旧快照。 |
| 15 | `examples/web_ui/frontend/src/api/task.ts`、`examples/web_ui/frontend/src/pages/task/**` | 只在 API/数据模型定稿后增加 Task 专属审批摘要、决定表单/面板和状态区分；普通 Input 保持独立。 |

不修改 `main.py`、`auth.py`、共享 API Client、全局导航或任何迁移文件。若需自动启动恢复，必须先证明可以通过 Task 模块已有生命周期触发且可原子领取；否则只能交付明确的用户/API 幂等恢复触发，不得宣称自动恢复。

## 9. 测试和边界验收

本阶段最终 Task 回归命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/task_template_version_test.py tests/task_template_test.py tests/task_sop_test.py tests/task_mvp_pipeline_test.py tests/task_tool_test.py tests/task_postgres_sop_test.py -q
```

结果：**67 passed, 2 skipped**。版本专项测试 7 项全部通过。两个 skip 是 `tests/task_postgres_sop_test.py` 要求配置一次性 PostgreSQL 测试 URL；本结果不代表真实业务系统、Postgres 部署或 Redis Cluster 联调通过。未改前端，因此本阶段不运行前端 build 或浏览器测试。仓库要求的 pre-commit 工具及 Black 在当前虚拟环境不可用（`No module named pre_commit` / `No module named black`）；没有新增或安装依赖。

最终 `git diff --check` 与 `git status --short` 结果在本报告提交前复核。源代码改动仅在 `examples/agent_service/task/**`，测试仅为 `tests/task_template_version_test.py`；预先存在的非 Task 改动/未跟踪文件未覆盖、未暂存、未提交。

## 10. 最终判断

1. **模板版本化：PASS。** 默认九个当前场景保持不变；测试验证精确历史解析和历史 Task 校验；S03 v1 内容不变；没有创建或发布 S03 v2。
2. **方案 B：GO（状态映射可行）。** 复用已被模型和数据库接受的生命周期值可以避免 migration；普通 Input 和审批依靠独立的 Task 状态字段、路由和 UI 语义区分。
3. **正式 HumanApproval：BLOCKED。** 授权/self-approval 业务政策缺失；Redis Cluster/standalone 拓扑未确认，原子跨 key 行为未验证；当前无原子 Store CAS、执行领取或 queued 自动恢复；S03 专家查询、回避和评分真实接口也未交付。

本阶段停止于 P6-B-1A，不自动进入 P6-B-1B。
