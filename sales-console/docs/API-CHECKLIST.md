# `/api/v1` 接口联调验收清单

本清单严格对应 [API-CONTRACT.yaml](./API-CONTRACT.yaml)。新系统只使用下面的 canonical 路径：JSON 使用 `snake_case`，时间使用 UTC ISO 8601，金额使用字符串，例如 `"1000.00"`。所有改变状态的请求必须携带 `Idempotency-Key`，所有响应带 `request_id`。旧 `/api/*` 仅为迁移兼容别名。

## A. 销售员工接口

| 状态 | 方法   | 路径                                                   | 用途                                          |
| ---- | ------ | ------------------------------------------------------ | --------------------------------------------- |
| [x]  | POST   | `/api/v1/auth/login`                                   | 销售员工登录，返回 HttpOnly Cookie            |
| [x]  | GET    | `/api/v1/auth/me`                                      | 当前员工信息                                  |
| [x]  | POST   | `/api/v1/auth/logout`                                  | 注销会话                                      |
| [x]  | GET    | `/api/v1/dashboard`                                    | 经营总览                                      |
| [x]  | GET    | `/api/v1/customers`                                    | 客户列表，返回 `customers、total、request_id` |
| [x]  | POST   | `/api/v1/customers`                                    | 创建客户并一次性返回 `api_token_once`         |
| [x]  | GET    | `/api/v1/customers/{customer_id}`                      | 客户详情和最近上报摘要                        |
| [x]  | PATCH  | `/api/v1/customers/{customer_id}`                      | 修改客户档案                                  |
| [x]  | DELETE | `/api/v1/customers/{customer_id}`                      | 停用客户档案                                  |
| [x]  | POST   | `/api/v1/customers/{customer_id}/api-token/rotate`     | 轮换 Token，仅一次性返回                      |
| [x]  | POST   | `/api/v1/customers/{customer_id}/verify-connection`    | 双向连接验证                                  |
| [x]  | GET    | `/api/v1/customers/{customer_id}/usage-reports`        | 客户用量快照                                  |
| [x]  | GET    | `/api/v1/customers/{customer_id}/recharge-orders`      | 客户充值订单                                  |
| [x]  | POST   | `/api/v1/customers/{customer_id}/recharge-codes`       | 签发离线充值码                                |
| [x]  | POST   | `/api/v1/customers/{customer_id}/reset-admin-password` | 远程重置客户管理员密码                        |
| [x]  | GET    | `/api/v1/recharge-requests`                            | 待审核充值申请                                |
| [x]  | POST   | `/api/v1/recharge-requests/{order_id}/approve`         | 审批并签发充值码                              |
| [x]  | POST   | `/api/v1/recharge-requests/{order_id}/reject`          | 拒绝充值申请                                  |
| [x]  | GET    | `/api/v1/reconciliation`                               | 充值、额度和消耗对账                          |
| [x]  | GET    | `/api/v1/alerts`                                       | 余额和报表告警                                |
| [x]  | GET    | `/api/v1/audit/events`                                 | 审计摘要                                      |
| [x]  | GET    | `/api/v1/settings`                                     | 读取总部设置                                  |
| [x]  | PATCH  | `/api/v1/settings`                                     | 更新总部设置                                  |
| [x]  | PATCH  | `/api/v1/staff/password`                               | 修改当前员工密码                              |
| [x]  | GET    | `/api/v1/public-key`                                   | 总部员工查看签名公钥                          |

## B. 客户系统机器接口

认证为 `Authorization: Bearer <customer-api-token>`，Token 只对应一个 `system_id`。传入的 `system_id` 仅作一致性校验，不能作为身份来源。

| 状态 | 方法 | 路径                                                   | 用途                     |
| ---- | ---- | ------------------------------------------------------ | ------------------------ |
| [x]  | POST | `/api/v1/integration/recharge-requests`                | 提交线上充值申请         |
| [x]  | GET  | `/api/v1/integration/recharge-requests/status`          | 查询申请审批状态         |
| [x]  | GET  | `/api/v1/integration/recharge-requests/poll`           | 轮询已审批充值码         |
| [x]  | POST | `/api/v1/integration/recharge-requests/{order_id}/ack` | 确认已兑换               |
| [x]  | GET  | `/api/v1/integration/recharge-codes/legacy`            | 同步历史离线充值码 nonce |
| [x]  | POST | `/api/v1/integration/usage-reports`                    | 上报额度/用量快照        |
| [x]  | POST | `/api/v1/integration/verify-connection`                | 请求双向连接验证         |
| [x]  | GET  | `/api/v1/integration/releases/{artifact_type}/latest`  | 下载 `app` 或 `core` 包  |
| [x]  | GET  | `/api/v1/integration/public-key`                       | 获取充值码验签公钥       |

充值申请请求体与当前客户管理中心保持一致：`system_id`、两位小数金额 `amount`、带时区的 `requested_at` 为必填；`note` 为可选字段，最多 300 个字符。轮询请求还必须携带 `system_id` 查询参数、`X-Request-ID` 和 `Idempotency-Key`。

## C. 发布与升级

| 状态 | 方法 | 路径                                  | 关键约定                                            |
| ---- | ---- | ------------------------------------- | --------------------------------------------------- |
| [x]  | GET  | `/api/v1/releases`                    | 返回 `app` 和 `core` 元数据                         |
| [x]  | POST | `/api/v1/releases/{artifact_type}`    | multipart 上传，`artifact_type=app\|core`           |
| [x]  | POST | `/api/v1/upgrade-all/{artifact_type}` | 请求体使用 `customer_ids[]`，下发文档规定的升级命令 |

其中：

- `app` = 龙信业务应用升级包；
- `core` = AgentScope 平台整体升级包；
- `core` 表示 AgentScope 平台整体升级；新的接口、响应和校验标准统一使用 `core` 与 AgentScope 包路径；
- 升级包必须有 `manifest.json`，检查类型、版本、路径穿越、必要文件和 SHA-256；
- 下发给客户系统的地址使用 `/integration/sales/v1/upgrades/{artifact_type}`，Ping 使用 `/integration/sales/v1/ping`。

## D. 必测安全和幂等场景

- [x] 未登录员工访问员工接口返回 `401`。
- [x] 客户 Bearer Token 不能访问员工接口；员工 Cookie 不能访问客户机器接口。
- [x] 状态变更请求缺少 `Idempotency-Key` 返回 `400`。
- [x] 同一作用域、同一幂等键但请求体变化返回 `409 idempotency_key_reused`。
- [x] 同一幂等请求重复提交返回相同结果，不重复签发、扣款或变更。
- [x] 响应只输出 `api_token_once`，不提供 canonical 的明文 Token 查询接口。
- [x] 响应和普通错误包含 `request_id`，错误结构为 `detail.code/detail.message`。
- [x] 金额为十进制定点字符串，时间为 UTC ISO 8601。
- [x] 普通用户的任务、聊天、文档、模型密钥和本地会话不传到销售总台。

## E. 仍需在客户管理系统侧实现

销售总台已经切换为调用下面的本地 canonical 命令；客户管理系统开发方需要实现并返回对应字段：

- `POST /integration/sales/v1/ping`：返回 `system_id、system_name、app_version、core_version、checked_at、request_id`；
- `POST /integration/sales/v1/admin-password-resets`：接收 `operation_id、username、new_password、expires_at`；
- `POST /integration/sales/v1/upgrades/{artifact_type}`：接收版本、SHA-256、下载地址和过期时间，异步执行并可通过 Ping 核实；
- 客户系统下载销售总台发布包时调用 `/api/v1/integration/releases/{artifact_type}/latest`，继续使用客户 Bearer Token；
- 客户系统发起充值申请和用量上报时调用本清单 B 的 `/api/v1/integration/*` 接口。
