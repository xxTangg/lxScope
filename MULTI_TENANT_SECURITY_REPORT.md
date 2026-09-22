# Phase 6 多租户安全收口报告

日期：2026-09-22  
范围：Platform Admin、Upgrade、Sales Hub、Credential、跨租户攻击测试

## 1. 结论

Phase 6 的安全边界已经落地：

- Platform Admin 使用显式 `platform:manage` 作为全租户管理根权限；该权限覆盖 `platform:upgrade`、`platform:integration` 和 `platform:observe`。
- Upgrade 的所有管理端接口只接受 Platform Admin。Tenant Admin 即使具备 `tenant:manage`，或仅携带 `platform:upgrade`，也会被拒绝。
- Platform Admin 查询租户目录时使用专用的全租户读取路径，并保留每条成员记录的 `tenant_id`，避免把多个租户压缩到当前请求租户的命名空间。
- Sales Hub 明确 `tenant -> customer -> system` 映射；Customer Token 先绑定 Customer，再校验 `system_id`，不能通过请求体切换到其他系统。
- Credential 通过 `LONGXIN_CREDENTIAL_SCOPE` 选择单一边界：`tenant` 或 `platform`。不会把两个 Credential 池合并，也不会把其他租户的管理员 Credential 注入当前租户。

## 2. 权限矩阵

| 能力 | Platform Admin | Tenant Admin | 普通用户 |
| --- | --- | --- | --- |
| `platform:manage` | 允许 | 禁止 | 禁止 |
| `platform:upgrade` | 允许 | 禁止 | 禁止 |
| `platform:integration` | 允许 | 按显式授权 | 禁止 |
| `platform:observe` | 允许 | 按显式授权 | 禁止 |
| `tenant:manage` | 可拥有 | 当前租户内允许 | 禁止 |
| 升级发布、应用、回滚 | 允许全部平台目标 | 禁止 | 禁止 |
| 当前租户成员管理 | 允许 | 允许 | 禁止 |

Upgrade 路由使用 `is_platform_admin()`，要求显式存在 `platform:manage`；
因此单独发放 `platform:upgrade` 不会形成平台管理员权限。

## 3. 商业模型映射

| 层级 | 主键 | 责任 | 安全用途 |
| --- | --- | --- | --- |
| tenant | `tenantId` | 龙信应用租户 | 用户、Skill、Knowledge、MCP、Billing 的隔离边界 |
| customer | `Customer.id` | Sales Hub 客户档案 | Customer Token、订单、上报、升级目标 |
| system | `systemId` | 实际交付实例 | 充值码签发、Ping、升级回执和运行版本 |

服务端创建或修改 Customer 时拒绝重复的 `tenantId` 或 `systemId` 映射。
对历史记录，读取侧以 `systemId`（缺失时以 Customer ID）提供兼容 tenant
映射；新记录应显式保存 `tenantId`。

## 4. Credential 边界

配置项：

```text
LONGXIN_CREDENTIAL_SCOPE=tenant
```

- `tenant`：仅当前已验证租户的管理员 Credential 可被共享；Credential 不跨租户枚举。
- `platform`：使用平台 Credential 池，仅 Platform Admin 能够维护；该模式不读取 Tenant Credential 池。
- 两种模式不能混合配置。Credential policy 会过滤带有不匹配 scope 元数据的记录，并把当前选择暴露在 `/admin/policy` 的 `credential_scope` 字段中。
- Credential API 仍只返回脱敏的共享视图；运行时解析才读取密文。

## 5. Cross-tenant attack test matrix

| 攻击面 | 攻击动作 | 预期结果 | 防护位置 |
| --- | --- | --- | --- |
| 用户隔离 | Tenant A 使用 Tenant B 的 membership ID | 只能得到 A 租户结果；B ID 解析为不可见 | `TenantBindingRepository.get_member/list_members` |
| Token 隔离 | Customer A Token 携带 Customer B 的 `system_id` | 400/拒绝；Token 仍绑定 A | Sales Hub `customerFromBearer` + `requiredSystemQuery` |
| Skill 隔离 | A 请求 B 的 tenant Skill | 不可见；platform Skill 仍可见 | `resource_is_visible` + resource registry |
| Knowledge 隔离 | A 请求 B 的 Knowledge Base | 404/不可见 | `TenantScopedKnowledgeBaseService` |
| MCP 隔离 | A 请求 B 的 MCP 发布记录 | 不可见 | `resource_is_visible` + publication tenant_id |
| Billing 隔离 | A 扣减额度或读取 B 账本 | A 余额独立变化，B 不变 | tenant-scoped quota keys / ledger |
| Credential 隔离 | A 枚举管理员 Credential | 只返回 A 的 Tenant Credential | `AdminManagedCredentialPolicy` |
| Upgrade 越权 | Tenant Admin 调用升级接口 | 403 `platform_admin_required` | upgrade router guard |

## 6. 新增测试

- `tests/test_phase6_security.py`
  - Tenant Admin 不能被识别为 Platform Admin。
  - Tenant Credential catalog 不返回其他租户 Credential。
  - Tenant Admin 调用 Upgrade guard 返回 403。
  - Platform Admin 可以通过 Upgrade guard。
- `sales-console/services/sales-api/test/phase6-security.test.mjs`
  - Tenant、Customer、System 映射的一对一约束。
  - 重复 tenant 或 system 映射被拒绝。

矩阵中的既有隔离回归测试：

- `tests/test_user_tenant_isolation.py`：用户、membership 和 Logto 组织隔离。
- `tests/test_resource_isolation.py`：Knowledge、Skill、MCP 的租户可见性隔离。
- `tests/test_quota_tenant_isolation.py`：Billing/quota 余额隔离。
- `tests/test_audit_observability_isolation.py`：审计与观测数据隔离。

## 7. 验证记录

- Python 模块静态编译检查：通过。
- 无外部 Web 依赖的既有隔离回归测试：7 项通过（resource、quota、audit/observability）。
- Phase 6 Python 测试需要 FastAPI/AgentScope 运行依赖；当前工作区没有安装 FastAPI/pytest，因此未能在本环境实际执行。
- Sales API 工作区没有安装 Node 依赖，TypeScript build 未能在本环境实际执行；新增 Node 测试已放入现有 API test suite，安装依赖后可使用项目原有测试命令执行。

## 8. 后续上线检查

1. 生产环境显式设置 `LONGXIN_CREDENTIAL_SCOPE`，并完成一次 Credential 池迁移核对。
2. 为每个 Sales Hub Customer 补齐 `tenantId`，确认 `systemId` 全局唯一。
3. 使用 Tenant Admin、Platform Admin、普通用户三类真实 Token 做一次接口回归。
4. 在具备依赖的 CI 环境执行新增 Python/Node 测试，并将结果附入发布记录。
