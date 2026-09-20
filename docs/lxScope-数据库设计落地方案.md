# lxScope 数据库设计落地方案

本方案用于把 `lxScope_数据库设计说明.md` 落成项目内可执行的第一版数据库基线，目标是为后续持久化提供稳定边界，不在本阶段一次性改造所有运行时逻辑。

## 本阶段结论

- PostgreSQL 作为 lxScope 业务事实源，使用独立的 `longxin_app` schema。
- AgentScope Core 和 `src/agentscope/app/storage/` 不做任何业务字段改造。
- Redis 继续承载缓存、锁、实时进度、Pub/Sub、Token 黑名单和短期幂等锁。
- Qdrant 继续承载向量数据；文件系统/OSS/MinIO 继续承载文件本体。
- 所有租户业务表都带 `tenant_id`；Repository 查询必须从服务端 `TenantContext` 获取租户，不信任请求体中的 `tenant_id`。
- `tenant_memberships.id` 是 AgentScope 兼容层的隔离身份：后续传入 AgentScope 的 `user_id` 使用 membership ID，而不是全局用户 ID。`users.external_user_id` 用于兼容当前 Redis 账户里的字符串用户 ID。
- 现有可观测性表先增加可空的 `tenant_id`、`membership_id`，等历史数据完成回填后再收紧为必填。

## 表分组

| 分组 | 表 | 作用 |
| --- | --- | --- |
| 租户与身份 | `tenants`、`users`、`tenant_memberships`、`tenant_settings` | 全局身份、租户成员关系和租户配置 |
| Task 持久化 | `tasks`、`task_runs`、`task_run_nodes`、`task_events`、`task_artifacts` | 任务定义、执行快照、节点过程、事件回放、文件元数据 |
| 配额与计费 | `tenant_accounts`、`plan_orders`、`recharge_orders`、`quota_ledger`、`idempotency_records` | 当前余额、订单、充值、追加式账本、幂等事实 |
| 资源与集成 | `resource_publications`、`resource_grants`、`integration_configs`、`system_configs` | Skill/MCP 等资源控制面、外部集成和系统配置 |
| 审计 | `audit_events` | 租户和平台级操作审计，追加写入 |

具体 DDL 位于：

```text
examples/agent_service/migrations/0002_lxscope_persistence_foundation.sql
```

回滚 DDL 位于：

```text
examples/agent_service/migrations/0002_lxscope_persistence_foundation.down.sql
```

## 与现有代码的映射

### 认证

当前 `auth.py` 以 Redis 和环境配置为主。后续迁移时：

- `users` 保存全局用户名、密码哈希、账号状态和 `token_version`。
- `tenant_memberships` 保存租户角色；当前 `admin/user` 可先映射为 `tenant_admin/member`。
- Redis 只保留 Token 黑名单、登录限流、会话缓存和锁。
- JWT 的推荐字段为 `sub=users.id`、`tid=tenants.id`、`mid=tenant_memberships.id`、`role=membership.role`。

### Task

当前 `RedisTaskStore` 的任务 JSON 先整体落在 `tasks.definition`，同时把执行过程拆到 `task_runs`、`task_run_nodes`、`task_events` 和 `task_artifacts`。这样既能保留当前 Pydantic 模型的完整性，也能支持按租户、任务、状态和时间范围查询。

迁移顺序建议为：

1. 保持现有 `TaskStoreProtocol` 不变。
2. 新增 `PostgresTaskStore`，先做双写或可切换读写。
3. 校验 PostgreSQL 与 Redis 的快照一致性。
4. 读取优先 PostgreSQL，Redis 降级为运行时缓存/临时状态。

### 计费与配额

当前套餐、订单、余额和账本仍在 Redis 逻辑中。后续审批必须在一个 PostgreSQL 事务内完成：

```text
锁定 tenant_accounts
→ 更新余额和 version
→ 写 plan_orders/recharge_orders
→ 追加 quota_ledger
→ 写 audit_events
→ 提交事务
```

`quota_ledger` 和 `audit_events` 只追加，不通过普通业务流程更新或删除。外部充值订单使用 `external_system + external_order_id` 幂等；API 幂等结果写 `idempotency_records`。

### Skill / MCP / Knowledge

资源本体仍由 AgentScope Storage 管理。`resource_publications` 只保存企业级发布、可见性和来源记录；`resource_grants` 保存授权对象和权限。这样不会把 Core 的资源表与 lxScope 的企业控制面耦合起来。

### 可观测性

现有 `skill_observability_events` 和 `project_observability_events` 保留。新写入必须补齐租户维度，查询必须包含：

```sql
WHERE tenant_id = :ctx_tenant_id
```

历史无法确定租户的数据保留 `NULL`，不能伪造归属；管理端可在迁移回填完成前将其标记为“未归属历史数据”。

## 事务和安全约束

- Router 不直接执行 SQL；调用链为 `Router → Service → Repository → SQLAlchemy → PostgreSQL`。
- Repository 接受 `TenantContext`，不能接受由前端单独传入的租户 ID。
- 所有租户表的外键尽可能使用 `(tenant_id, id)` 复合约束，避免跨租户引用。
- `config`、`metadata` 和任务 JSON 不应写入密码、Token、私钥或原始大文件。
- `task_artifacts.storage_uri` 只保存外部存储地址和元数据，文件本体不进入 PostgreSQL。
- `tenant_accounts.version` 用于乐观锁；高并发扣减也可以使用 `SELECT ... FOR UPDATE`。
- 迁移脚本按编号执行；生产发布前先在备份库执行回滚演练。

## 迁移建议

1. 在 PostgreSQL 中执行 `0002_lxscope_persistence_foundation.sql`。
2. 创建平台初始租户、默认管理员用户和 membership，并记录映射关系。
3. 为现有 Redis 用户、Task、订单和账本编写一次性导入脚本；不直接删除 Redis 数据。
4. 先双写，再校验，再切换读取来源。
5. 观测表完成历史回填后，再另行增加 `NOT NULL` 和外键约束。

数据库 schema 和迁移基线已经建立；应用层接入采用配置开关控制，不会修改 AgentScope Core，也不会强制改变已有 Redis 运行路径。

## 当前项目接入状态

当前代码已经接入应用层 PostgreSQL 基础设施：

- `LXSCOPE_DATABASE_URL` 配置后，服务启动会执行应用层正向迁移，并记录到 `longxin_app.schema_migrations`。
- `LXSCOPE_TASK_STORE_BACKEND=postgres` 时，TaskService 使用 `PostgresTaskStore`；任务定义和运行快照保持现有 Pydantic JSON 结构，事件和文件元数据写入 PostgreSQL。
- 未设置 PostgreSQL 或保持 `LXSCOPE_TASK_STORE_BACKEND=redis` 时，继续使用原来的 `RedisTaskStore`。
- AgentScope Core 仍通过 `RedisStorage` 工作；认证、计费等其他应用模块暂时仍走原有 Redis 路径，后续按迁移阶段逐步切换。
