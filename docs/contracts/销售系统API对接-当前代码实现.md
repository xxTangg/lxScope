# 销售系统 API 对接说明（当前代码实现）

> 本文只依据当前工作区源代码整理，不以其他历史或参考文档作为接口事实来源。
>
> 整理范围：管理员前端、本地 AgentScope 服务、销售系统之间的配置、充值、用量上报、连接校验、密码重置和升级接口。

## 1. 对接架构

当前代码是三段式调用关系：

```text
管理员浏览器
    │  本地管理员 JWT
    ▼
Longxin AgentScope 本地服务
    │  配置的 customer token
    │  Authorization: Bearer <customer-token>
    ▼
销售系统 Sales Hub
```

- 浏览器不直接调用销售系统地址。
- 浏览器调用 `http://<本地服务>/admin/...`。
- 本地服务通过 `SalesHubClient` 调用销售系统的 `/api/v1/integration/...`。
- 销售系统反向调用本地服务时，使用 `/integration/sales/v1/...`，并携带配置中的 customer token。
- 本地服务和销售系统之间使用 `X-Request-ID` 关联一次请求；可重试的写操作还使用 `Idempotency-Key`。

## 2. 通用请求头和错误格式

### 2.1 浏览器调用本地服务

前端 `client.ts` 当前行为：

| 请求类型 | 认证 | `X-Request-ID` | `Idempotency-Key` |
|---|---|---|---|
| `GET` | `Authorization: Bearer <admin-jwt>` | 自动生成 | 不自动添加 |
| `POST`、`PATCH`、`DELETE` | `Authorization: Bearer <admin-jwt>` | 自动生成 | 自动补充；销售总台相关方法也会显式生成 |

管理员相关的状态变更接口要求当前登录用户是 active admin。前端 `admin.ts` 显式为以下操作生成幂等键：配置更新、连接校验、充值提交、充值同步、用量上报和充值码兑换。

### 2.2 本地服务调用销售系统

`SalesHubClient` 对每次请求统一发送：

```http
Authorization: Bearer <configured-customer-token>
Content-Type: application/json
X-Request-ID: <request-id>
Idempotency-Key: <idempotency-key>
```

其中 `Idempotency-Key` 仅在调用方传入时发送；当前充值、轮询、ACK、用量上报和连接校验都会传入。

销售系统地址由配置中的 `hub_url` 加上代码中的路径拼接得到，例如：

```text
hub_url = http://192.168.31.197:44100
最终地址 = http://192.168.31.197:44100/api/v1/integration/recharge-requests
```

`hub_url` 应填写销售系统根地址，不要把具体 API 路径再次拼进去。

### 2.3 本地错误响应

`/admin/*` 和 `/integration/sales/v1/*` 的错误在 HTTP 响应中统一包装为：

```json
{
  "detail": {
    "code": "error_code",
    "message": "Human-readable message",
    "request_id": "req-..."
  }
}
```

请求没有携带 `X-Request-ID` 时，本地服务会生成一个；响应头也会回写同一个 `X-Request-ID`。

参数校验失败时返回 HTTP `422`，`detail.code` 为 `validation_error`，具体字段位于 `detail.fields`。

## 3. 销售系统连接配置

### 3.1 查询配置

```http
GET /admin/sales-hub/config
Authorization: Bearer <admin-jwt>
X-Request-ID: <request-id>
```

响应模型：

```json
{
  "system_id": "456",
  "hub_url": "http://192.168.31.197:44100",
  "token_masked": "cust****oken",
  "public_key_fingerprint": "sha256:...",
  "outbound_status": "unknown",
  "inbound_status": "unknown",
  "last_verified_at": null,
  "request_id": "req-..."
}
```

实际代码不会返回明文 token，只返回掩码；配置了公钥时只返回 SHA-256 指纹前缀。

### 3.2 更新配置

```http
PATCH /admin/sales-hub/config
Authorization: Bearer <admin-jwt>
Content-Type: application/json
X-Request-ID: <request-id>
Idempotency-Key: <unique-idempotency-key>
```

请求体字段全部可选：

```json
{
  "system_id": "456",
  "hub_url": "http://192.168.31.197:44100",
  "token": "<customer-api-token>",
  "public_key": "<Ed25519-public-key>"
}
```

代码行为：

- `system_id` 更新时，同时更新本地系统记录中的 `system_id`。
- `hub_url` 必须是 HTTP 或 HTTPS URL，保存时会去掉末尾 `/`。
- `token` 保存到服务端配置；查询接口只显示掩码。
- 非空 `public_key` 会按 Ed25519 公钥校验；查询接口只显示指纹。
- 该接口只更新本地配置，不直接调用销售系统。
- 缺少 `Idempotency-Key` 返回 HTTP `400`：`idempotency_required`。

## 4. 本地服务向销售系统发起的接口

以下接口是 `SalesHubClient` 当前实际调用的销售系统路径。请求体和响应字段是本地代码实际生成或校验的内容。

### 4.1 连接校验

管理员调用本地接口：

```http
POST /admin/sales-hub/verify
Authorization: Bearer <admin-jwt>
X-Request-ID: <request-id>
Idempotency-Key: <unique-idempotency-key>
```

本地服务向销售系统发送：

```http
POST {hub_url}/api/v1/integration/verify-connection
Authorization: Bearer <customer-api-token>
Content-Type: application/json
X-Request-ID: <same-request-id>
Idempotency-Key: <same-idempotency-key>

{}
```

本地代码要求销售系统响应至少满足：

```json
{
  "request_id": "<same-request-id>",
  "outbound": true,
  "ping": {
    "ok": true,
    "system_id": "456"
  },
  "inbound": true
}
```

兼容形式：`outbound` 可以是 `true`，也可以是 `{"ok": true}`；`inbound` 可以是 `true`，也可以是 `{"ok": true}`。`ping.system_id` 必须等于当前本地系统 ID。

成功后本地配置状态为：

```json
{
  "outbound_status": "ok",
  "inbound_status": "ok"
}
```

### 4.2 提交线上充值申请

管理员调用本地接口：

```http
POST /admin/quota/recharge-requests
Authorization: Bearer <admin-jwt>
Content-Type: application/json
X-Request-ID: <request-id>
Idempotency-Key: <unique-idempotency-key>

{
  "amount": "0.01",
  "note": "线上充值"
}
```

本地接口请求体只定义了 `amount` 和可选的 `note`：

| 字段 | 类型 | 规则 |
|---|---|---|
| `amount` | string | 金额格式为整数或最多两位小数；必须大于 0；发送到销售系统前规范化为两位小数 |
| `note` | string/null | 可选，最多 300 个字符 |

`system_id` 和 `requested_at` 不由浏览器传入本地接口，而是由本地服务生成销售系统请求：

```http
POST {hub_url}/api/v1/integration/recharge-requests
Authorization: Bearer <customer-api-token>
Content-Type: application/json
X-Request-ID: <request-id>
Idempotency-Key: <idempotency-key>

{
  "system_id": "456",
  "amount": "0.01",
  "requested_at": "2026-09-16T05:00:00+00:00",
  "note": "线上充值"
}
```

其中：

- `system_id` 来自本地系统记录；
- `amount` 已由本地代码格式化为 `0.01` 这种固定两位小数；
- `requested_at` 是本地服务生成的当前 UTC 时间；
- `note` 只有在本地请求提供时才发送。

销售系统返回 HTTP `2xx` 时，本地代码实际要求：

```json
{
  "order_id": "ord-...",
  "request_id": "<same-request-id>",
  "status": "pending",
  "delivery_status": "not_delivered"
}
```

响应校验规则：

- `order_id` 必须是非空字符串；
- `request_id` 必须存在，并且必须等于本次发送的 `X-Request-ID`；
- `status` 必须是 `pending`、`approved`、`rejected` 或 `unknown`；
- `delivery_status` 必须是 `not_delivered` 或 `delivered`；
- 如果响应包含 `system_id`，必须等于本地系统 ID；如果缺少，当前代码会按本地系统 ID处理。

本地成功响应会补充本地 `created_at`，并保存订单：

```json
{
  "order_id": "ord-...",
  "system_id": "456",
  "amount": "0.01",
  "status": "pending",
  "delivery_status": "not_delivered",
  "created_at": "2026-09-16T05:00:00+00:00",
  "request_id": "<same-request-id>"
}
```

### 4.3 轮询已审批充值订单

管理员调用本地接口：

```http
POST /admin/quota/recharge-requests/sync
Authorization: Bearer <admin-jwt>
X-Request-ID: <request-id>
Idempotency-Key: <unique-idempotency-key>
```

本地服务向销售系统发送：

```http
GET {hub_url}/api/v1/integration/recharge-requests/poll?system_id=456
Authorization: Bearer <customer-api-token>
Content-Type: application/json
X-Request-ID: <request-id>
Idempotency-Key: <idempotency-key>
```

本地代码优先接受以下响应：

```json
{
  "orders": [
    {
      "order_id": "ord-...",
      "system_id": "456",
      "amount": "0.01",
      "tokens": 1,
      "status": "approved",
      "delivery_status": "not_delivered",
      "recharge_code": "<recharge-code>",
      "expires_at": "2026-09-17T00:00:00+00:00"
    }
  ],
  "request_id": "<same-request-id>"
}
```

单个订单也可以由当前兼容逻辑转换为 `orders` 数组；如果订单字段叫 `code`，当前代码会把它转换为 `recharge_code`。轮询响应的订单必须满足：

- `status` 固定为 `approved`；
- `delivery_status` 固定为 `not_delivered`；
- `tokens` 必须大于 0；
- `amount` 必须是两位小数格式；
- `request_id` 必须等于轮询请求的 `X-Request-ID`。

本地服务接着检查本地订单、系统 ID 和金额，兑换充值码，最后向销售系统发送 ACK。

### 4.4 充值订单 ACK

对每个成功兑换的订单，本地服务发送：

```http
POST {hub_url}/api/v1/integration/recharge-requests/{order_id}/ack
Authorization: Bearer <customer-api-token>
Content-Type: application/json
X-Request-ID: <request-id>
Idempotency-Key: <sync-idempotency-key>:<order-id>

{
  "operation_id": "op-...",
  "system_id": "456",
  "redemption_operation_id": "op-...",
  "ledger_id": "led-..."
}
```

销售系统响应必须满足：

```json
{
  "order_id": "ord-...",
  "status": "approved",
  "delivery_status": "delivered",
  "request_id": "<same-request-id>"
}
```

ACK 通过后，本地订单更新为 `delivery_status: delivered`。

### 4.5 上报用量

管理员调用本地接口：

```http
POST /admin/sales-hub/usage-report
Authorization: Bearer <admin-jwt>
X-Request-ID: <request-id>
Idempotency-Key: <unique-idempotency-key>
```

该本地接口不接收请求体；本地服务根据当前系统和用户数据生成销售系统请求：

```http
POST {hub_url}/api/v1/integration/usage-reports
Authorization: Bearer <customer-api-token>
Content-Type: application/json
X-Request-ID: <request-id>
Idempotency-Key: <idempotency-key>

{
  "system_id": "456",
  "pool_tokens": 1000,
  "total_recharged": "100.00",
  "app_version": "3.8.1",
  "cumulative_consumed": 120,
  "cumulative_credits": 1000,
  "client_reported_at": "2026-09-16T05:00:00+00:00"
}
```

字段来源：

| 字段 | 当前代码来源 |
|---|---|
| `system_id` | 本地系统记录 |
| `pool_tokens` | 本地系统余额 |
| `total_recharged` | 本地累计充值金额，格式化为两位小数 |
| `app_version` | `LONGXIN_APP_VERSION`，默认 `3.8.1` |
| `cumulative_consumed` | 所有非删除普通用户的累计用量 |
| `cumulative_credits` | 本地系统累计 credits，缺省为 `0` |
| `client_reported_at` | 当前 UTC 时间 |

销售系统响应必须包含：

```json
{
  "report_id": "report-...",
  "system_id": "456",
  "accepted": true,
  "reported_at": "2026-09-16T05:00:01+00:00",
  "cumulative_consumed_delta": 120,
  "cumulative_credits_delta": 1000,
  "request_id": "<same-request-id>"
}
```

本地代码还会校验 `system_id` 和 `request_id`，并要求 `accepted` 为 `true`；通过后才更新本地上报时间和累计用量。

## 5. 销售系统调用本地服务的接口

这些接口由本地服务提供给销售系统，路由前缀是 `/integration/sales/v1`。认证不是本地管理员 JWT，而是配置中的 customer token。

### 5.1 Ping

```http
POST /integration/sales/v1/ping
Authorization: Bearer <customer-api-token>
X-Request-ID: <request-id>
```

请求体为空。token 必须与本地配置的 token 完全匹配。

响应：

```json
{
  "ok": true,
  "system_id": "456",
  "system_name": "Longxin AgentScope",
  "app_version": "3.8.1",
  "core_version": "unknown",
  "checked_at": "2026-09-16T05:00:00+00:00",
  "request_id": "<request-id>"
}
```

`system_name` 默认来自 `LONGXIN_SYSTEM_NAME`，`app_version` 和 `core_version` 优先读取升级服务当前版本或对应环境变量。

### 5.2 远程管理员密码重置

```http
POST /integration/sales/v1/admin-password-resets
Authorization: Bearer <customer-api-token>
Content-Type: application/json
X-Request-ID: <request-id>
Idempotency-Key: <unique-idempotency-key>

{
  "operation_id": "op-...",
  "username": "admin",
  "new_password": "<new-password>",
  "expires_at": "2026-09-17T00:00:00Z"
}
```

字段规则：

- `operation_id`：非空，最多 128 个字符；
- `username`：非空，最多 64 个字符，目标必须是本地管理员；
- `new_password`：长度 8 至 1024；
- `expires_at`：可选；如果提供，必须是带时区且晚于当前时间的 ISO-8601 时间。

成功响应：

```json
{
  "ok": true,
  "operation_id": "op-...",
  "username": "admin",
  "request_id": "<request-id>"
}
```

同一个已处理的 `operation_id` 不能用于另一个密码重置操作。

### 5.3 远程升级命令

```http
POST /integration/sales/v1/upgrades/{artifact_type}
Authorization: Bearer <customer-api-token>
Content-Type: application/json
X-Request-ID: <request-id>
Idempotency-Key: <unique-idempotency-key>
```

`artifact_type` 只能是 `app` 或 `core`。请求体：

```json
{
  "operation_id": "upgrade-...",
  "artifact_type": "app",
  "version": "3.8.2",
  "sha256": "<64-hex-sha256>",
  "size_bytes": 123456,
  "download_url": "https://sales.example/releases/app-3.8.2.tar.gz",
  "issued_at": "2026-09-16T05:00:00Z",
  "expires_at": "2026-09-16T06:00:00Z"
}
```

字段规则：

- `sha256` 必须是 64 位十六进制字符串；
- `size_bytes` 必须大于 0；
- `download_url` 必须是 HTTP 或 HTTPS URL；
- `issued_at`、`expires_at` 必须带时区，且请求有效期未过期；
- URL 下载包时，本地服务会继续使用请求中的 `Authorization` 访问下载地址；
- 本地服务会校验下载包大小、SHA-256 和归档内容，然后异步执行升级。

成功时立即返回一个异步 `UpgradeOperation`，初始状态通常为 `pending`：

```json
{
  "operation_id": "upgrade-...",
  "artifact_type": "app",
  "version": "3.8.2",
  "state": "pending",
  "source": "sales_hub",
  "request_id": "<request-id>",
  "backup_id": null,
  "started_at": "2026-09-16T05:00:00+00:00",
  "finished_at": null,
  "result": null,
  "error": null
}
```

## 6. 充值完整流程

```text
1. 管理员浏览器
   POST /admin/quota/recharge-requests
   body: amount, note
        │
        ▼
2. 本地服务
   从本地配置读取 system_id、hub_url、customer token
   生成 requested_at
        │
        ▼
3. 销售系统
   POST /api/v1/integration/recharge-requests
   返回 order_id + pending
        │
        ▼
4. 管理员点击同步，或后台同步循环触发
   GET /api/v1/integration/recharge-requests/poll?system_id=...
        │
        ▼
5. 本地服务校验订单和充值码，完成本地入账
        │
        ▼
6. 销售系统
   POST /api/v1/integration/recharge-requests/{order_id}/ack
   返回 delivered
```

本地前端的充值订单列表：

```http
GET /admin/quota/recharge-requests?limit=20
```

这个列表接口只查询本地已保存订单，不会直接查询销售系统。前端当前使用 20 作为查询数量，后端允许范围是 1 至 200，默认值是 50。

## 7. 当前 `HTTP 400` 问题的代码解释

如果销售系统返回：

```http
HTTP/1.1 400 Bad Request

{
  "status": "pending",
  "delivery_status": "not_delivered",
  "request_id": "req-..."
}
```

按当前代码会发生两层判断：

1. 因为远端 HTTP 状态是 `400`，`SalesHubClient` 先把它转换为本地 `hub_request_failed`，本地充值接口返回 HTTP `502`。
2. 即使远端把状态码改成 `2xx`，这个响应仍然缺少必需的非空 `order_id`；本地代码会返回 `hub_invalid_response`，不会猜测或生成订单号。

因此，`status: pending` 不能抵消 HTTP `400`，也不能代替 `order_id`。销售系统在接受充值申请时，至少应返回 HTTP `2xx`、非空 `order_id`、与请求头相同的 `request_id`、合法的 `status` 和 `delivery_status`。

## 8. 当前代码来源

本文对应的主要源文件：

| 文件 | 内容 |
|---|---|
| `examples/agent_service/sales_hub_client.py` | 销售系统 URL 拼接、请求头、超时、重试、远端错误处理 |
| `examples/agent_service/admin_api.py` | 配置、充值、轮询、ACK、用量、Ping、密码重置路由和模型 |
| `examples/agent_service/longxin_admin/upgrade/models.py` | 远程升级请求和升级操作模型 |
| `examples/agent_service/longxin_admin/upgrade/router.py` | 销售系统调用的升级接口 |
| `examples/agent_service/longxin_admin/upgrade/service.py` | 远程升级校验、下载、校验和异步执行 |
| `examples/web_ui/frontend/src/api/client.ts` | 浏览器认证、`X-Request-ID` 和幂等键处理 |
| `examples/web_ui/frontend/src/api/admin.ts` | 前端实际调用的本地管理员 API 和请求体类型 |
| `examples/agent_service/main.py` | 路由挂载、请求 ID 中间件和后台充值同步任务 |

