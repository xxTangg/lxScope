# P6-B-0：Task 审批能力前置检查与接口冻结

> 基线：分支 `integrate/dev_l`，HEAD `fdc2193 fix(task): finalize template gallery validation`。
> 设计依据：[P6-A-task-node-capability-design.md](P6-A-task-node-capability-design.md)。
> 本阶段只分析并冻结候选技术协议；未开发 HumanApproval，未修改模板、执行逻辑或其他源码。

## 1. 执行结论

### 结论：**BLOCKED — P6-B-1 暂不能进入编码**

| 强制前置条件 | 现状与源码证据 | 结果 |
|---|---|---|
| 服务端能得到可信认证主体 | `AuthUser` 有服务端校验后的 `id`、`username`、`role`、`status`、`capabilities`，Logto 下还包含 `tenant_id`、`subject_id` 和 `organization_scopes`；但 Task 路由当前只注入 `user_id`。 | 身份来源可信；Task 审批授权尚未定义。 |
| 存在可用于 S03 的审批角色/权限映射 | 当前只有通用 `user/admin` 和通用能力/scope，没有 `task.approve`、S03 reviewer policy 或专家匹配审批范围。 | **不满足**。不得把 `admin`、`tenant.manage` 或 Task 所有者身份自行解释成审批权。 |
| 所有者能否审批自己的任务 | Task/审批需求没有该业务决策。 | **待业务确认**；默认拒绝/不开放，本文不代选策略。 |
| 审批记录能避免并发覆盖、双重决定 | 当前 Input 服务层有 run guard 和重复回答判断，但 Store 没有跨进程条件写入；状态更新与事件追加分开。 | **不满足**。需新增 Store 原子转换协议。 |
| S03 真实闭环契约 | 模板明确标注 demo/configurable；专家目录、回避规则、评分算法没有已批准的服务接口或数据契约。 | **不满足**。只能冻结调用数据形状，不能声称真实接通。 |
| 老模板/任务可兼容 | S03 v1 为 `1.0.0`；Registry 只接受固定九个 JSON 文件；Validator 按当前 template id/version 校验并拒绝版本不匹配。 | 可在 Task 模块内设计版本解析，但尚未实现。 |

**是否能不改共享认证模块：** 技术上可以。Task API 可在既有认证服务上取得经过验证的 `AuthUser`，由 Task 模块执行经批准的审批策略；不需要修改共享认证代码。但当前没有经业务确认的策略，也没有 Task 专属审批权限，因此“可以复用身份”不等于“当前可以授权审批”。

本阶段冻结的是**候选接口/存储边界**。审批角色来源、所有者自批政策、授权范围和 S03 业务规则仍待确认，所以整体接口不能被视为已批准的生产权限合同。缺少任一项时，P6-B-1 必须保持 BLOCKED；不允许退回普通 InputStep 或使用客户端身份字段降级实现。

## 2. 审批身份与权限检查

### 2.1 服务端实际可识别的主体字段

`examples/agent_service/auth.py:42-53` 定义 `AuthUser`：

| 字段 | 可信来源 / 用途 | 限制 |
|---|---|---|
| `id` | JWT/local 账户 ID；Logto 下是租户和 subject 派生的 scoped user ID。是当前 Task 数据隔离的主体键。 | 适合作为审批审计的主体 ID；不能单独表示有审批权。 |
| `username` | JWT 账户记录或 Logto 的 username/email/name 解析结果。 | 展示字段，不应作稳定授权键。 |
| `role` | Local/JWT 从服务端账户配置载入 `user/admin`；Logto 在验证 access token scopes 后按配置的 admin scope 映射为 `admin` 或 `user`。 | 通用应用角色，不含任务类别、审批阶段或被授权范围。 |
| `status` | Local/JWT 账户状态或 Logto 组织成员状态；当前认证依赖检查账户可用状态。 | 只说明主体是否可用，不代表审批资格。 |
| `capabilities` | Local/JWT 按通用 admin/user 角色生成；Logto admin 映射 `tenant.access`、`tenant.manage`，user 映射 `tenant.access`。 | 当前没有 Task/S03 approval capability。 |
| `tenant_id`、`subject_id`、`organization_scopes` | Logto 验证后的 `organization_id`、`sub`、scope；见 `examples/agent_service/identity/logto.py:266-311`。 | 对租户隔离和审计有用；JWT/local provider 不保证这些字段可用。 |
| `token_version` | Local/JWT 的账户版本；认证时与当前账户记录比对。 | 内部撤销/版本字段，不是权限角色。 |

Local/JWT `get_current_user` 会验证 bearer token 的 `sub`、`username`、token version 并重新读取账户记录，再返回 `AuthUser`（`auth.py:604-648`）。Logto `get_current_user` 校验签名/issuer/audience/组织身份及配置 scope，再形成 `AuthUser`（`identity/logto.py:266-311`）。账号名或客户端提交的 `user_id` 不应替代这两个验证结果。

### 2.2 Task API 当前拿到什么

Task router 从 `agentscope.app.deps` 引入 `get_current_user_id`，各 Task route 的依赖参数类型为 `str`；例如模板实例化、Task 读取及 Input 提交见 `examples/agent_service/task/_router.py:213-222, 645-671, 796-810`。应用组装时以 `auth.get_current_user_id` 覆盖该 dependency（`examples/agent_service/main.py:692`），所以部署路径得到的是认证服务解析后的当前用户 ID；底层 AgentScope 默认 dependency 的临时 `X-User-ID` 行为不应被误当成部署鉴权实现。

认证服务有 `is_admin_user(user_id)`，但它只回答通用管理员状态（`auth.py:234-262`、`identity/logto.py:442-444`）。它不回答“能否审批这个 S03 run”，也不处理所有者自批/同租户跨用户 Task 访问。Postgres Task 数据中的 membership `role="member"` 是 Task store 的兼容映射，不是审批角色来源（`task/_postgres_store.py:168-231`）。

### 2.3 可以复用的 Task 内授权边界

未来可以新增 Task 自有主体适配/策略层（例如 `task/_approval_policy.py`），不改 `auth.py`：

1. Task route 从 `request.app.state.auth` 调用现有 `get_current_user(Authorization)`，复用认证服务完成 token、账户状态和 Logto tenant 校验，取得完整 `AuthUser`。不自行解码 JWT、不读取请求体里的 actor、不信任前端角色。
2. Task 层执行默认拒绝的 `authorize(actor, task, run, pending_approval, action)`。只有业务明确指定、经服务端校验的角色/scope/任务范围可以通过；generic `admin` 仅在业务书面确认其拥有 S03 审批职责后才可映射。
3. 将 `AuthUser.id` 和可用时的 `tenant_id`/`subject_id`、授权策略 ID/版本写入决定审计。绝不保存 bearer token；`username` 仅作为可选显示快照。
4. 先按服务端可信 actor 解析 run/task，再校验 tenant、任务/运行快照、待审批 request id 和策略。跨用户、跨租户、已过期或不存在的审批资源应采用不泄露资源存在性的响应；不能只依赖随机 run ID。
5. 业务必须回答“Task 所有者能否审批自己的任务”。未回答前默认 deny；代码不能自行假定 owner=approver，也不能假设 admin 必须与 owner 不同。

**跨用户审批的额外边界：** 当前 `TaskStore.get_run(user_id, run_id)`、Redis user-prefixed key 和 Postgres owner membership 查询均按 Task 所有者隔离。若批准由其他用户执行，Task 必须提供审批 inbox/授权查询，而不能把原 run owner 改成审批人，也不能放宽普通 Task 读取权限。Redis 需增加按授权租户/策略筛选的待审批索引；Postgres 查询需把 tenant 与已授权 reviewer 关系一起校验。该索引/查询不应泄露未授权用户的 Task、输出或专家资料。

## 3. PendingApproval / ApprovalDecision 候选协议

下面的数据形状是本阶段的 Task 专属候选合同；带“待确认”的字段只有业务批准后才能变成强制语义。

### 3.1 PendingApproval（仅服务端创建）

| 字段 | 类型/语义 |
|---|---|
| `request_id` | 服务端生成的随机、不可预测 ID；同一 pending 决定的幂等键。 |
| `task_id`、`run_id`、`node_id` | 指向被审批的 Task、同一运行和 HumanApproval 槽位。 |
| `task_revision` | 创建 run 时的 revision；run 快照不可被后来 Task 编辑替换。 |
| `template_id`、`template_version` | 例如 `s03_expert_matching` / `2.0.0`；把决定绑定到不可变模板版本。 |
| `package_digest` | 服务端对候选、冲突结果、评分、理由证据及版本快照计算的摘要；决定不能被重放到不同候选清单。 |
| `summary` / `evidence_refs` | 最小必要、已授权的审批材料与证据引用；不得在 inbox 泄露完整专家敏感档案。 |
| `created_at`、`expires_at` | 服务端时间和过期时间。过期政策及是否可重新发起待业务确认。 |
| `policy_id`、`policy_version` | 审批发起时适用的 Task policy。当前无值/实现，必须由业务确认政策来源。 |
| `status` | `pending`；决定只通过后端转换，不接受客户端修改。 |

### 3.2 ApprovalDecisionRequest（客户端只提交决定意图）

```json
{
  "request_id": "<pending request id>",
  "decision": "approve",
  "reason": "<可选；拒绝理由是否必填由业务确认>"
}
```

协议中**不包含** `actor_id`、`username`、`tenant_id`、`role`、`approved_by`、`decided_at`、`policy_id` 或客户端生成的审批结论字段。服务端从已认证 `AuthUser` 和已批准策略填充这些值。

### 3.3 ApprovalDecision（仅服务端持久化）

```json
{
  "decision_id": "<server-generated id>",
  "request_id": "<pending request id>",
  "decision": "approve",
  "reason": "<bounded text>",
  "actor": {
    "user_id": "<verified AuthUser.id>",
    "tenant_id": "<verified tenant when available>",
    "subject_id": "<verified subject when available>"
  },
  "policy_id": "<approved policy id>",
  "policy_version": "<policy version>",
  "package_digest": "<same pending package digest>",
  "decided_at": "<server UTC timestamp>"
}
```

预期状态转换：

```text
pending -> approved -> resume the same TaskRun
pending -> rejected -> terminal rejected TaskRun; never schedule downstream steps
```

规则：同一 `request_id`、同一决定内容的重试返回已保存结果，不再创建第二条决定或再次派发；相同 request 的相反决定、request/run/node/template/revision/digest 不匹配、已取消/已结束 run 均拒绝。Approve 与 Reject 竞态只有一个可从 `pending` 条件转换成功；失败一方读回获胜决定，只能返回既有结果或 409，不能覆盖它。

### 3.4 API 候选

* `POST /tasks/runs/{run_id}/approval`：提交上述 `ApprovalDecisionRequest`，由 Task route 取得认证主体并授权；成功响应返回 TaskRun 与服务端决定摘要。
* 若审批主体可能不是 Task owner，再增加 `GET /tasks/approvals/pending`（分页且只返回 actor policy 有权处理的摘要）和必要的授权详情读取。若业务明确规定只有 owner 可审批，可以不增加跨用户 inbox，但需明确自批政策并仍做服务端认证。
* 输入路由 `POST /tasks/runs/{run_id}/input` 保持纯信息收集用途，不复用于正式审批。

以上路径和 409 冲突语义可作为 API 草案。批准人角色、owner 自批、过期/撤回规则未定之前，授权策略合同**没有冻结**。

## 4. SOP 等待、恢复、并发与持久化

### 4.1 已有能力与边界

| 环节 | 当前能力 | 对正式审批的适用性 |
|---|---|---|
| SOP park | `TaskSOPStep` 可令 Task step state 进入官方 `SOPPhase.AWAITING`；TaskService checkpoint `SOPRunState` JSON 和 `pending_input`。见 `task/_sop.py:28-117`、`task/_service.py:796-895, 1215-1337`。 | **状态原语可复用**；它只代表暂停，不代表审批已实现。要在 Task state 中增加独立 `pending_approval`，不可复用 `TaskInputReply` 冒充。 |
| 同一 run 恢复 | `answer_run` 读取原 run 快照，校验 request id/必填字段，把答案写回原 SOP state，再将同一 run 置为 queued 并调度；不新建 run、不调用 Planner。见 `task/_service.py:1003-1088`。 | **执行模式可复用**。批准后同样恢复同一个 `TaskRunRecord`；只在审批通过后继续执行下一步。 |
| 等待跨服务重启 | Postgres/Redis 保存完整 `TaskRunRecord`（含 `sop_state`、`pending_input`）；重启后可从 owner/授权 inbox 重新读取待审批快照。 | **pending 状态可持久化**，但当前没有审批 payload/审核人决定 schema。等待期间没有 live coroutine 需要恢复。 |
| queued/running 崩溃恢复 | `_schedule_run` 使用本地 `asyncio.create_task`、`_run_users` 和 `_active_runs`；未发现启动时扫描并重投 queued/running 的 Task recovery worker。 | **不支持自动恢复**。pending approval 仍可等待；同一服务恢复审批后可调度。若进程在“批准已提交、schedule 前”退出，须有 outbox/reconcile 或幂等请求重试；不能宣称已有自动队列恢复。 |
| 重复提交 | Input answer 的 `request_id` 已答且 payload 相同会返回已有 run；不同 payload 被拒绝（`task/_service.py:1069-1093`）。 | 仅应用于 Input；服务层 guard 不是 Store CAS。Approval 需独立 decision id/唯一条件转换。 |
| 并发执行锁 | `_run_guard` 包含进程内 asyncio lock 和可选 message bus lock（`task/_service.py:972-982`）。当前 `main.py` 默认使用 `InMemoryMessageBus()`，Redis message bus 只是注释中的多进程配置选项（`main.py:394-402`）。 | 不能以当前配置保证多进程 CAS。审批决定正确性最终必须由持久化 Store 原子操作保证。 |
| 拒绝/控制流 | 当前 RunStatus 没有 `awaiting_approval` 或 `rejected`；官方 SOP 仅有 `AWAITING`/`FAILED` 等 phase。TaskService 对 SOP FAILED 会终止 run。 | 需要 Task-owned 的审批状态和 rejection 映射。拒绝绝不安排恢复 run，且必须有测试证明第 8 步未执行。 |

### 4.2 三类 Store 的现状和目标实现

| Store | 当前原子能力 | 当前不足 | P6-B-1 建议 |
|---|---|---|---|
| `TaskStore` 内存实现 | 单个方法内部由一把 `asyncio.Lock` 保护字典读写（`task/_store.py:23-162`）。适合单进程单元测试。 | `update_run()`、`append_event()`、`next_event_sequence()` 是不同调用；不能模拟跨进程，也没有 `expected_status + request_id` 条件转换协议。 | 增加 `commit_approval(...)` 专用协议方法；在同一 lock 内比较 pending/request/decision、写 run state/decision、分配 event sequence、append event，返回 committed/duplicate/conflict。 |
| `RedisTaskStore` | 用户隔离 key；run 快照以 JSON `SET` 保存，事件用 `RPUSH`；Redis 本身可提供事务、WATCH/MULTI/EXEC、脚本及 `SET NX` 等原子原语。 | 当前 `update_run` 是 `GET` 后 `save_run`；run `SET` 与索引 `ZADD` 分离；事件 `RPUSH` 与 `LLEN+1` 序号分开。代码没有使用 Redis transaction/CAS（`task/_redis_store.py:117-223`）。 | 在一个 Redis 事务中 WATCH run snapshot 和 event list，校验 status/request/决策空值，计算下一个 sequence 后 MULTI 同时写 run JSON、decision event；冲突时重读并判重复或 409。也可选 Lua，但必须在同 slot 实现。需要验证注入客户端 API 和 Redis 部署 topology；当前 key 无 hash tag，Redis Cluster 跨 slot 事务不能直接假定可用。若允许跨 owner 审批，还要原子维护 tenant-scoped pending index。 |
| `PostgresTaskStore` | `save_run()`、`append_event()` 各自使用 `engine.begin()` 事务；`task_events` 有 `UNIQUE(run_id, sequence)` 约束（`task/_postgres_store.py:373-457, 536-598`；`migrations/0002_lxscope_persistence_foundation.sql:166-181`）。 | 现有 `update_run()` 先 get 再 save，是两个事务之间的 read-modify-upsert；事件也在单独事务中写；`next_event_sequence()` 独立读 MAX+1（`_postgres_store.py:501-527, 536-615`）。单独的 UPSERT 不保证审批状态 CAS 或 run+event 一致提交。 | 新 `commit_approval` 在同一个 `engine.begin()` 中按 tenant/run/可见权限 `SELECT ... FOR UPDATE`，检查当前快照的 pending id/status/decision，写回 JSON snapshot/生命周期 status，并按锁内 max(sequence)+1 插入审计事件；同一事务提交。相反决定遇到已完成状态返回冲突，重复同决定读回原决定。若出现外部 reviewer，查询必须绑定有效 tenant/reviewer policy，不能沿用 owner-only query 放宽成任意 run lookup。 |

**审计边界：** 建议把 decision 同时保存在 TaskRun 的 SOP step state 中用于幂等查询，并追加 `approval.requested` / `approval.approved` / `approval.rejected` TaskEvent 作为时间线记录；状态与事件必须和原子决定同事务/事务组提交。现有事件追加接口本身不是防篡改/WORM 审计，业务需确认保留期、数据库写权限、备份和审计级别；不能宣传为法律意义的不可抵赖签名。

### 4.3 Run/node 生命周期及旧数据兼容

当前 `RunStatus`/`NodeRunStatus` 只有 `awaiting_input`，没有 approval waiting 或 rejected（`task/_models.py:53-72`）。Postgres `0004_task_awaiting_input.sql` 明确维护 task_runs 和 task_run_nodes 的 status CHECK；加入独立审批状态需新增 Task 专属迁移（建议 `examples/agent_service/migrations/0005_task_approval_status.sql`），同时更新两个 CHECK 约束，并纳入迁移 runner（runner 位于 `examples/agent_service/persistence/database.py`，会按序应用 SQL migration）。

建议新增 Task 状态 `AWAITING_APPROVAL`、`REJECTED`，NodeRun 对应状态也显式化；仅把 `awaiting_approval` 复用为 `awaiting_input` 会让 API/UI 混淆两种权力行为。TaskRun 顶层增加可选 `pending_approval: PendingApproval | None = None`，TaskStepState 新增可选 approval request/decision 字段且有默认值。旧 JSON 快照缺这些字段时应可反序列化；原有 enum 字符串不变。Pydantic snapshot round-trip、Redis/Postgres 两后端旧快照测试必须通过。

同意时：原 pending step 写入 server-side `ApprovalDecision`，run 经 Store 原子转换为 queued 后调度**原 run ID**；Approval step 从 durable state 识别 approved，形成普通 SOP submission，官方引擎继续后续步骤。拒绝时：在原子提交内将该 Task step 的 SOP phase 记录为 `FAILED` 并写入结构化 `approval_rejected` metadata，同时将 Task run/node 映射为 Task-owned `REJECTED` 终态、清除 pending、保存拒绝决定和事件，不调用 `_schedule_run`。这样不需要新增 AgentScope phase，也不让官方顺序引擎进入下一步。必须确保拒绝状态不能被 `retry_run` 或普通输入接口绕过为已批准；若再次运行则必须新建 run 并重新审批。

## 5. S03 专家匹配接口契约

### 5.1 当前八个槽位

依据 `examples/agent_service/task/templates/s03_expert_matching.v1.json:26-127`：

| # | 当前 key / type | P6-B-0 接口边界 |
|---:|---|---|
| 1 | `collect_matching_requirements` / input | 项目匹配需求输入。模板 schema 目前定义 `project_name`、`project_brief`、`professional_fields`、可选 `exclusion_rules`、`required_expert_count`（v1 JSON:13-24）。 |
| 2 | `extract_project_profile` / 可选 agent | 从显式提供的信息归一化画像；任何外部资料必须有授权来源引用。 |
| 3 | `lookup_expert_candidates` / tool | 查询真实专家目录，只返回有权展示的候选字段和来源引用。 |
| 4 | `check_expert_conflicts` / tool | 逐候选回避状态、规则版本及可披露证据。 |
| 5 | `rank_expert_candidates` / tool | 经批准、版本化的确定性评分及分项证据。 |
| 6 | `explain_matching_reasons` / agent | 只对查询/校验/评分产出的事实作有证据的文字说明。 |
| 7 | `collect_human_review` / input | **未来 HumanApproval 的替换位置**；不新增第 9 个节点。v1 仅收集意见，模板文字明确未校验审批身份。 |
| 8 | `produce_expert_shortlist` / agent | 仅在通过批准后格式化结果；保留草稿/批准状态和证据引用，不写业务系统。 |

### 5.2 候选业务协议（不表示已有接口）

| 协议 | 候选输入/输出 | 校验与未决合同 |
|---|---|---|
| **ProjectProfileV1** | `{schema_version:"s03.project-profile.v1", project_name:string, project_brief:string, professional_fields:string[], exclusion_rules?:string, required_expert_count?:integer(1..20), sources:[{field,source_ref}]}`。字段起点与 v1 `input_schema` 对齐。 | 当前模板实例输入允许 array/integer，但运行时 `TaskInputReply.answers` 与 `task-input-form.tsx` 只能提交字符串（`task/_models.py:104-149`；前端 `task-input-form.tsx:10-89`）。`professional_fields` 和 `required_expert_count` 因而存在协议类型不一致。新版本必须添加 Task 专属类型/严格解析（例如增加 question value type 与后端解析），或调整 v2 schema 使其诚实表达文本输入；不能静默把逗号字符串当正式数组。哪些字段必填、回避字段可接受的结构/来源需业务确认。 |
| **ExpertLookupRequest/Result** | 请求：`protocol_version`、`project_profile`、`limit`（有上限）、分页 cursor（如支持）。结果：`experts[]` 中 `expert_id`、业务批准的最小必要 profile fields、`source_ref`、`snapshot_at`、`record_version`，以及可选 `next_cursor`。 | 必须有专家目录 owner、API/MCP 名称、字段 allow-list、可见性/租户范围、分页上限、数据新鲜度/删除语义。当前没有已接通业务 API；无接口/无权限必须返回真实错误而不是生成模拟候选。 |
| **ConflictCheckRequest/Result** | 请求包含项目画像、候选 `expert_id` 列表和来源版本。每个结果 `{expert_id, status:"clear"|"conflict"|"unknown", rule_id, rule_version, checked_at, evidence_refs[], reason_code}`。 | 规则权威方、回避条件、冲突定义/豁免主体、规则生效日期及证据披露策略均待业务确认。`conflict` 不进入可推荐名单；`unknown`、超时、规则版本缺失必须 fail-closed。不得由 LLM 代替确定性检查。 |
| **ExpertRankingRequest/Result** | 请求传 profile 与冲突为 `clear` 的候选，不允许客户端选任意评分版本。结果 `{algorithm_id, algorithm_version, scored_at, candidates:[{expert_id,rank,score,components[],evidence_refs[]}]}`。 | 必须由业务批准算法/权重/版本、分数范围、同分处理、候选不足和公平/合规审查。没有规则 Tool 不得由 Agent 自创分数。 |
| **MatchingReasonsV1** | `{experts:[{expert_id, statements:[{text,evidence_refs[]}], uncertainties:[]}]}`；每个可验证事实至少引用候选/回避/评分输出中的 source/evidence ID。 | Agent 输出必须经结构化 schema 校验并检查 reference 确实存在于本 run 的上游结果；Task 当前只有任意 `result` 载体，没有通用输出 schema 校验。这是 P6-B-1 实施或 S03 实际开放前的 Task 内扩展点。缺少有效证据的理由不能交给审批人或写入 final shortlist。 |
| **PendingApproval / ApprovalDecision** | 使用第 3 节协议；pending 包摘要绑定 `package_digest`、task revision、S03 template version、专家结果和理由证据。decision 由后端附加可信 actor/policy/time。 | 审批主体、owner 自批、拒绝理由是否必填、过期、撤销、二次审批、退回补充语义待业务确认。首版推荐只支持 approve/reject；拒绝终止本 run。 |
| **ExpertShortlistV1** | `{schema_version:"s03.expert-shortlist.v1", decision:{decision_id,request_id,actor_user_id,decided_at,package_digest}, project_profile, candidates:[{expert_id,rank,score,algorithm_version,reasons[],conflict_status:"clear",evidence_refs[]}], disclaimer}`。仅包含可披露最小字段；拒绝/未审批不得生成“已批准”名单。 | 业务确认 Top N、字段和导出格式、保留期限、可见范围。当前末节点是自由文本 AgentStep，需 Task 输出 schema 校验后才能宣称满足该 JSON contract。v1 仍仅是供复核的草稿语义。 |

不得在 P6-B-1 里填入假的业务 URL、凭空注册的专家工具名、真实个人数据或虚构算法。Gate 0 前置材料应由业务系统 owner 提供并用集成测试确认。

## 6. S03 模板版本与旧 Task 兼容

### 6.1 当前版本行为

* v1 文件 `s03_expert_matching.v1.json` 声明 `template_id="s03_expert_matching"`、`template_version="1.0.0"`（第 2–6 行）；不能直接改成 approval 配置或覆盖同一文件。
* Registry 的 `TEMPLATE_FILES` 固定九个文件，目录中多出的 `.json` 会被判为 `unexpected_template_file`（`task/_template_registry.py:15-23, 86-111`）。单纯加 v2 文件会让当前 Registry 加载失败。
* TaskRecord 保存 `source_template_id` 和 `source_template_version`（`task/_models.py:391-414`）。实例化要求请求版本等于 Registry 的当前版本（`task/_template_service.py:72-140`）；编辑/运行校验也按 ID 取当前模板并强制版本相等，旧版本会被拒绝（`task/_template_validation.py:132-168`）。因此只更新“当前文件”将使已有 v1 模板副本无法通过后续修改/运行校验。

### 6.2 推荐的新旧版本方案

1. 永久保留既有 v1 JSON 文件内容和 v1 协议，不原地覆盖。新增 `templates/s03_expert_matching.v2.json`，声明同一稳定场景 `template_id="s03_expert_matching"`、新 `template_version="2.0.0"`；其第 7 槽改为 HumanApproval，总数保持 8。
2. 更新 Task Registry，使 manifest 明确区分 **current template per scenario** 和 **historical immutable versions**。默认 `list_templates()` 仍只返回 9 个场景，其中 S03 返回 v2；目录允许的文件必须来自明确 manifest，不放宽任意额外 JSON。
3. 新增 `get_template_version(template_id, template_version)` 历史解析：新实例化仍用 current template 并精确校验 request version；TaskTemplateValidator 用 TaskRecord 保存的 `(source_template_id, source_template_version)` 取对应的不可变 v1/v2 定义。v1 副本按 v1 的 Input/demo 约束继续编辑/运行；不自动升级为已审批业务 Task。
4. 既有 `TaskRunRecord.nodes` 是创建 run 时的节点定义快照，旧 run 继续读自己的快照。对 legacy Task 的重新运行仍受其 v1 provenance 校验；正式审批结果只可能来自显式实例化/升级到 v2 的新 Task 和新的 approval run。
5. 不使用不兼容配置覆盖 v1，也不隐式迁移用户已有 Task。若需要用户主动升级，另定义可审计的显式复制/迁移流程，并完整重编译 v2 与重新审批；不得沿用旧意见作为 v2 批准。

该方案保留九个 gallery 场景，同时保留历史 v1 校验能力。它需要改 Registry/Validator 的版本查找 API；本阶段未实施。

## 7. P6-B-1 逐文件编码清单与顺序

### Gate 0：业务确认（不改文件，未通过则停止）

1. 审批权限映射：具体 Logto scope / 账户角色 / Task 内策略来源，S03 适用范围，跨用户/跨组织行为，以及 owner 是否可自批。
2. 退回/拒绝、过期、撤回、重复审批、审批人与候选人存在利益关系的语义；首版建议只做 approve/reject，reject 为终态。
3. 审计保留期/查看人/脱敏要求；是否需要不可篡改审计或电子签章（当前 TaskEvent 不提供这项保证）。
4. 专家目录、冲突规则、评分算法的正式 API/MCP schema/owner/test environment；ProjectProfile 类型和字段授权；推荐结果字段/Top N/output schema。
5. Redis 运行模式（standalone/cluster）、Redis client transaction surface、Postgres migration 部署策略和回滚策略。

### Gate 1：先做身份和原子存储基础，再接 SOP

| 顺序 | 允许变更文件 | 具体修改目的 |
|---:|---|---|
| 1 | `examples/agent_service/task/_approval_policy.py`（新增 Task 文件） | 接收由现有认证服务解析出的 AuthUser；以默认拒绝策略校验 Task/run/tenant/owner/reviewer/action；只实现业务已书面批准的映射，不新增假角色。若审批政策需共享认证改造，则在此停止并升级范围评审。 |
| 2 | `examples/agent_service/task/_models.py` | 增加 `HumanApprovalStepConfig`、`PendingApproval`、不含 actor 的 request model、含 server actor/policy/digest 的 `ApprovalDecision`；新增 approval-wait/rejected Task 状态及 `TaskRunRecord.pending_approval`。新增字段有默认值，保留旧 JSON 兼容。 |
| 3 | `examples/agent_service/task/_store.py` | 扩展协议：`commit_approval` 原子比较 request/status/decision、写 run snapshot 和 event；需要跨用户时增加 policy/tenant-filtered `list_pending_approvals`/授权 lookup，不暴露通用任意 owner 查询。 |
| 4 | `examples/agent_service/task/_redis_store.py` | 使用经过验证的 WATCH/MULTI/EXEC 或同 slot 脚本实现同一决定 CAS + run snapshot + audit event + sequence；处理同决定重试和相反决定冲突；按批准的 owner/reviewer 模式建待办索引。 |
| 5 | `examples/agent_service/task/_postgres_store.py` | 在单数据库事务内锁 run 行、验证 tenant/授权查询与 pending correlation、更新 snapshot 和 status、插入有唯一 sequence 的 TaskEvent；实现同决定幂等回读与拒绝冲突。不得使用现有 get-then-save 作为 CAS。 |
| 6 | `examples/agent_service/migrations/0005_task_approval_status.sql`（Task 专属 migration） | 只有新增 `awaiting_approval`/`rejected` 生命周期值时才加 migration；更新 `task_runs` 和 `task_run_nodes` status CHECK，保留全部旧值。migration runner 已自动按序执行 SQL 文件，无需改 persistence 框架。 |
| 7 | `examples/agent_service/task/_sop.py` | 通过 TaskStepState 扩展状态，把 approval 节点无决定时置官方 `SOPPhase.AWAITING`；approve 时读取持久决定形成 submission，reject 时绝不返回继续结果；不扩展/修改 `src/agentscope/sop/**`。 |
| 8 | `examples/agent_service/task/_service.py` | 生成带 package digest 的 pending request；在现有执行锁下处理 store CAS，approved 后恢复原 Run ID，rejected 直接保存终态且不 schedule；投影独立 pending approval/node/run status；避免 `retry_run`/Input API 绕过审批。明确 crash-after-commit-before-schedule 的幂等重试/恢复策略。 |
| 9 | `examples/agent_service/task/_router.py` | 在 Task 路由内取得完整认证主体、调用 Task policy；增加 decision endpoint 和（仅跨用户批准需要时）pending inbox；actor 字段不接受请求体输入，跨 owner/tenant 用策略过滤并防止信息泄露。共享认证模块保持不变。 |

### Gate 2：模板版本化，不改 v1

| 顺序 | 允许变更文件 | 具体修改目的 |
|---:|---|---|
| 10 | `examples/agent_service/task/_template_models.py` | 添加 `approval` kind/config 的严格 schema；将步骤数、配置 discriminator、版本兼容约束保留；输入问题若要 typed responses，需为 v2 做显式 additive contract。 |
| 11 | `examples/agent_service/task/_template_compiler.py` | 把 approval config 编译到 Task-owned node type/config；不绑定假 Tool、不调用 Planner。 |
| 12 | `examples/agent_service/task/_template_registry.py` | 增加显式的 current/history manifest 和 `(template_id, version)` resolver；默认目录仍是九个 current 场景；只接受声明过的历史文件。 |
| 13 | `examples/agent_service/task/_template_validation.py` | 验证模板副本时按 provenance 的准确版本载入 canonical 定义；继续校验必需步骤、顺序、编辑策略和 8 节点限制；审批节点身份/配置不能被副本篡改。 |
| 14 | `examples/agent_service/task/_template_service.py` | 新实例化保持 current version 精确匹配；v1 历史版本只用于已有副本校验，不提供隐式升级。 |
| 15 | `examples/agent_service/task/templates/s03_expert_matching.v2.json`（新增） | 新 schema version/模板版本，保留 8 步并以 HumanApproval 替换 `collect_human_review` 的 Input；仅引用经 Gate 0 确认的业务能力契约；原 `v1.json` 不动。 |

### Gate 3：Task 前端与专项测试

| 顺序 | 允许变更文件 | 具体修改目的 |
|---:|---|---|
| 16 | `examples/web_ui/frontend/src/api/task.ts` | Task 专属节点联合类型、pending/decision/status、Task approval API 类型；不修改共享 API client/index。 |
| 17 | `examples/web_ui/frontend/src/pages/task/task-flow-editor.tsx` | 显示 HumanApproval 专用类型并保持模板约束/节点上限；不把 InputStep 标签改名冒充审批。 |
| 18 | `examples/web_ui/frontend/src/pages/task/task-run-panel.tsx`、`task-view-model.ts`、新增 `task-approval-panel.tsx` | 展示 pending 审批摘要、真实决定/actor/时间/拒绝状态；只有 server 返回可审批能力时显示可操作项；API 仍为最终权限判断。 |
| 19 | `examples/web_ui/frontend/src/pages/task/task-input-form.tsx`（只有 Gate 0 要求 v2 typed project fields 时） | 添加 additive 的 typed Input 控件/严格序列化；旧 v1 自由文本问题继续兼容。若业务接受统一文本协议则不改此文件。 |
| 20 | 新增 `tests/task_approval_test.py`、`tests/task_redis_approval_test.py`；扩充 `tests/task_sop_test.py`、`tests/task_postgres_sop_test.py`、`tests/task_template_test.py` | 测试权限/伪造 actor/跨用户跨租户隔离/self-approval policy；同请求重复、approve/reject 竞争、事务中断和事件一致性；同 run 重启恢复、拒绝不执行后续节点、旧快照、v1/v2 resolver、九场景目录和八节点上限。测试只放 Task 专项文件。 |

前端当前 package scripts 没有单元测试 runner，仓库也没有已发现的 Task 页面 test/spec 文件（`examples/web_ui/frontend/package.json` 仅有 build/lint 等脚本）。不得为此新增依赖；使用既有 Task 页面浏览器验收流程/手动关键交互并记录环境，前端 `pnpm build` 作为类型与构建检查。AgentScope `CONTRIBUTING_zh.md` 要求功能配套单元测试且沿用 `tests/` 结构，AI 代码须逐行人工审阅，PR 聚焦并保持 API 兼容；P6-B-1 的测试/兼容计划遵循这些规则。当前 P6-B-0 不运行测试，因为没有实现改动。

### 明确禁止进入 P6-B-1 的条件

* 业务不确认审批权限来源和 self-approval 规则。
* Task API 无法获得认证服务验证后的 AuthUser，或需通过修改共享认证/全局导航才能授权。
* Postgres/Redis 任一 Store 无法提供单决定 CAS + 决定/状态/审计一致提交，或 Redis 部署 topology 不支持所选原子方案。
* 无法保证拒绝后下游不运行，或 crash 重试可能造成审批跳过/决策覆盖。
* 模板 Registry 不能保留 v1 解析与 9 个 current 场景并存，或要求覆盖已发布 `v1.json`。
* 专家目录/冲突/评分合同没有 owner、schema、授权与可用于集成测试的环境。可以先单独实现通用审批基础，但不得宣称 S03 闭环 ready。

## 8. 风险、待业务确认问题和验收证据

### 8.1 必须由业务/系统 owner 回答

1. S03 的审批人是谁：Logto 哪个明确 scope/组织角色，还是独立的 Task 审批授权名单/策略？授权是否需要按项目、专家领域或组织隔离？
2. Task 所有者能否自批？若不能，如何识别“申请人/候选专家/项目负责人”等关联利益关系？
3. 是否允许同租户 reviewer 审批他人 Task？若允许，谁能看审批摘要/证据/历史，是否需要审批 inbox？
4. 拒绝是否终止，是否要求理由；退回补充能否作为新 Task run，还是必须支持可控回路？首版建议拒绝终态，不实现返工分支。
5. 审批是否允许撤回/过期/二次审批；已批准 package 因规则或专家资料变化如何作废？
6. 决定记录的法律/内控等级、留存期限、审计读取权限、数据脱敏、数据库备份/篡改防护标准是什么？
7. 专家查询、回避规则和评分算法分别由哪个系统 owner 提供？是否有沙箱/稳定版本/测试数据；有哪些最小必要候选字段和评分解释要求？
8. `professional_fields`、`required_expert_count` 等如何从字符串式 Input 变成类型安全输入；是否需要附件/项目资料资产接口？
9. Redis 是否部署为 Cluster；Postgres migration 可否纳入当前 Task 版本发布；审批后进程宕机时是否要求自动恢复还是允许幂等 API 重试触发？

### 8.2 P6-B-1 必须提供的证据

* 身份证据：无 token/客户端 actor 可伪造；有效/禁用账号、Logto tenant/scope、JWT role、策略拒绝路径均有测试。
* 授权证据：经批准的 role/policy 规则可证明某 actor 可批指定 Task；owner self-approval 结果与业务决策一致；跨 owner/tenant 读写被隔离。
* 状态证据：等待审批持久化、服务重启后同一个 run 可见；approve/resume 仍为原 run ID；reject 是独立终态且下游节点运行次数为零。
* 原子性证据：两个 Store 后端分别并发提交 approve/reject，只产生一个决定；同决定重试幂等、异决定冲突；run snapshot 与审计事件同一原子边界提交；事件 sequence 不重复。
* 兼容证据：历史 Task/Run JSON 可读取；v1 模板副本按 v1 校验/运行语义继续工作；新建 v2 使用 8 步、审批替换 Input；gallery 保持九个 current 场景；无自动 upgrade。
* 业务证据：真实专家数据/回避/评分输出携带来源和规则版本，`unknown` fail-closed；没有业务 API 时 UI/API 明确 unavailable，不造假候选或批准结果。
* 工程证据：Task 专项 pytest、Redis/Postgres 并发集成测试、前端 build、浏览器审批/拒绝/待办关键交互结果；符合 Task 白名单，无新增依赖、无 `src/agentscope/**` 变更。

## 9. 本阶段工作区状态

本阶段只新增本文件。未修改 `src/agentscope/**`、Task/其他源码、模板或测试；未运行测试；未暂存、未提交，也未覆盖现有未提交文件。P6-B-1 未开始，当前结论保持 **BLOCKED**，待第 8.1 节授权策略和业务接口确认后重新评估。
