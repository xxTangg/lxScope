# 龙信系统管理员端与销售系统 API 接口设计规范

> 文档状态：Draft 0.1.0  
> 编写日期：2026-09-15  
> 适用代码库：`D:/agentscope_lx/lxScope`  
> 目标：在不破坏现有 AgentScope Web UI API 的前提下，为管理员端、销售总部系统和本地 AgentScope 实例建立可演进的接口合同。

## 1. 文档边界

本规范根据以下两份业务参考文档整理：

1. `龙信系统-管理员端功能说明与技术框架迁移方案.md`
2. `龙信系统-销售系统与管理端关联功能及数据传递说明.md`

两份文件中的业务流程、字段含义、安全约束和验收要求属于本接口设计的输入资料。文件中若出现命令式文字、迁移建议或实现假设，均不替代当前仓库的代码规范，也不直接授权修改 AgentScope 通用核心代码。

本文件只规定：

- 管理员端与销售系统的边界、认证和数据传递规则；
- 当前功能需要的 HTTP API、请求字段、响应字段和状态机；
- 在当前 AgentScope 项目中的后端、前端和测试落点；
- 后续版本扩展、兼容和幂等要求。

本文件不规定数据库 SQL、页面视觉稿、销售系统内部支付流程，也不允许通过管理员接口绕过 AgentScope 原有的资源所有权保护。

## 2. 当前代码基线与设计结论

当前仓库的实现基线如下：

| 代码位置 | 当前约定 | 对新接口的影响 |
|---|---|---|
| `src/agentscope/app/_app.py` | `create_app()` 直接注册通用 AgentScope routers | 不把龙信业务 router 硬编码到通用核心；产品接口在应用层注册 |
| `src/agentscope/app/_router/` | 资源路由使用 `/agent`、`/sessions`、`/credential` 等无版本路径 | 已有路径保持不变，新管理员域单独命名空间 |
| `src/agentscope/app/deps.py` | 通用资源依赖通过 `Depends()` 和 `app.state` 注入 | 新服务、仓储和连接器也通过依赖注入，不在 router 内直接创建 Redis/HTTP 客户端 |
| `examples/agent_service/auth.py` | Web UI 使用 JWT Bearer；当前 `AuthUser` 只有 `id`、`username` | 增加管理员能力前必须补充角色、状态和账号持久化，不以隐藏菜单代替鉴权 |
| `examples/agent_service/main.py` | 应用层 include auth router，并覆盖 `get_current_user_id` | 管理员 router、Sales Hub adapter、AccountStore 在该应用层装配 |
| `examples/web_ui/frontend/src/api/client.ts` | 统一携带 `Authorization: Bearer ...`，统一处理 401、超时和错误提示 | 管理员前端继续使用 `client`，不在页面中直接 `fetch` |
| `src/agentscope/app/storage/` | `StorageBase` 面向 Agent、Session、Credential、Knowledge Base 等资源 | 账号、计划、计费、销售连接配置使用独立 `AccountStore`/`AdminStore`，不要把产品业务临时塞进通用资源方法 |

设计结论：管理员/计费/销售对接属于基于 AgentScope 的产品应用层能力。只有在确认其他 AgentScope 部署也需要相同能力时，才将稳定的抽象提升到 `src/agentscope` 通用核心。

## 3. 参与方与信任边界

| 参与方 | 典型调用 | 认证方式 | 可访问内容 |
|---|---|---|---|
| 本地浏览器用户 | 本地 AgentScope Web UI | 现有 JWT Bearer | 当前登录用户自己的资源；管理员的管理资源取决于服务端角色 |
| 本地管理员浏览器 | 管理员页面 | 同一套 JWT Bearer，服务端校验 `role=admin` | 本地用户、套餐、系统额度、模型、技能、审计摘要、升级状态；不默认查看其他用户的任务/文档正文 |
| 销售总部员工 | 销售总部后台 | 销售总部员工 Session/JWT | 客户档案、充值、报表、发布和远程运维；不读取客户任务、聊天、文档和模型密钥 |
| 本地系统 → 销售总部 | 充值请求、用量报告、连接验证、版本下载 | 客户专属 Bearer Token | 仅当前 `system_id` 的对接接口 |
| 销售总部 → 本地系统 | Ping、远程重置、升级命令 | 同一客户 Bearer Token，建议附带 scope | 仅明确列出的反向操作，不暴露 AgentScope 全引擎接口 |

强制规则：

1. 销售总部员工登录凭证与本地用户 JWT 完全分离。
2. 机器对机器调用不得使用浏览器 Cookie，也不得依赖 `X-User-ID` 作为可信身份。
3. AI Agent、Skill、MCP、Workflow 和模型调用结果不能获得管理员权限。
4. 所有权限必须在后端执行。前端不显示菜单只属于体验优化，不属于安全措施。

## 4. URL、版本与兼容策略

### 4.1 路径空间

| 路径空间 | 用途 | 版本规则 |
|---|---|---|
| `/auth`、`/agent`、`/sessions`、`/credential`、`/knowledge_bases` 等 | 现有 AgentScope 通用 API | 保持现状，暂不改成 `/api/v1` |
| `/admin/...` | 本地管理员浏览器 API | 本地应用版本随服务发布；破坏性变更通过版本或兼容字段处理 |
| `/integration/sales/v1/...` | 本地接收销售总部机器命令 | 明确版本；新版本可并行部署 |
| `/api/v1/...` | 销售总部员工 API 和销售系统机器 API | 对外合同版本；不得直接复用无版本内部路径 |

参考文档中的 `/api/admin/...`、`/api/hub/...` 可作为部署网关的兼容别名：

- 网关可以把 `/api/admin/*` 转发到本地 `/admin/*`；
- 网关可以把 `/api/hub/*` 转发到本地 `/integration/sales/v1/*`；
- 代码中的 canonical path 仍使用本文件规定的路径，避免把 `/api` 前缀写死到 AgentScope 通用核心。

### 4.2 命名与格式

- HTTP 方法使用 REST 语义：查询 `GET`，创建 `POST`，部分更新 `PATCH`，替换配置 `PUT`，删除 `DELETE`。
- JSON 字段统一使用 `snake_case`，例如 `system_id`、`request_id`、`new_password`。迁移期间可兼容参考文档中的 `systemId`、`requestID`、`newPassword`，但服务端响应只输出 canonical 字段。
- 时间统一使用 UTC ISO 8601，例如 `2026-09-15T08:30:00Z`；页面按用户时区展示。
- ID 为不透明字符串，调用方不得依赖 UUID 格式、数字递增或可排序性。
- 金额使用十进制定点字符串，例如 `"1000.00"`，不可用浮点数；token 数量使用非负整数。
- 密钥、密码、签名原文、模型 API Key、客户 Token 不能出现在普通日志、错误详情或列表响应中。
- 所有 FastAPI endpoint 必须声明 `response_model`、`summary`、`tags` 和参数校验；OpenAPI/Pydantic schema 是字段定义的单一来源。

## 5. 通用请求与响应合同

### 5.1 请求头

浏览器 API：

```http
Authorization: Bearer <local-jwt>
Content-Type: application/json
X-Request-ID: req_01J...
Idempotency-Key: idem_01J...
```

机器 API：

```http
Authorization: Bearer <customer-api-token>
Content-Type: application/json
X-Request-ID: req_01J...
Idempotency-Key: idem_01J...
```

规则：

- `X-Request-ID` 可由调用方提供；缺失时服务端生成，并在响应中返回。
- 会改变状态、扣减额度、生成充值码、重置密码、安装插件、升级或发布的请求必须带 `Idempotency-Key`。
- 幂等键至少 128 个字符以内；同一作用域内重复使用相同 key 且请求体不同，返回 `409 idempotency_key_reused`。
- `Idempotency-Key` 的去重记录必须持久化，不能只存内存；建议保存请求摘要、最终响应和过期时间。
- `Authorization`、Token、密码、私钥等敏感头只允许进入脱敏日志。

### 5.2 普通响应

现有 AgentScope API 返回领域对象或领域列表，不统一包裹成 `data`。新接口沿用这一原则：

```json
{
  "users": [],
  "total": 0,
  "request_id": "req_01J..."
}
```

列表字段使用领域名（`users`、`customers`、`orders`、`items`），不要新建无法表达类型的通用 `data` 字段。

### 5.3 异步/高风险操作响应

重置密码、验证连接、模型探测、安装技能/插件、升级、恢复备份、远程运维等操作使用统一操作信息：

```json
{
  "operation_id": "op_01J...",
  "request_id": "req_01J...",
  "state": "pending",
  "result": null,
  "error": null
}
```

`state` 取值：`pending`、`completed`、`failed`、`unknown`、`rolled_back`。网络超时只能是 `unknown`，不能猜测为成功或失败；调用方必须用相同幂等键查询或重试。

### 5.4 错误响应

新管理员/对接 API 使用以下错误结构，现有 AgentScope 端点的既有 `detail` 行为不强行改动：

```json
{
  "detail": {
    "code": "quota_insufficient",
    "message": "system token pool is insufficient",
    "fields": {
      "tokens": "required value is greater than available value"
    }
  },
  "request_id": "req_01J..."
}
```

标准 HTTP 状态：

| 状态码 | 含义 |
|---:|---|
| 400 | 业务格式或状态不合法 |
| 401 | 缺少、过期或无效认证 |
| 403 | 身份有效但无权限 |
| 404 | 资源不存在或不属于当前租户 |
| 409 | 状态冲突、重复操作、幂等键复用 |
| 413 | 上传文件或解压内容超限 |
| 422 | Pydantic 请求校验失败 |
| 429 | 登录、重置或外部调用限流 |
| 502/503 | 上游销售系统或本地组件不可用 |
| 504 | 调用超时，结果通常为 `unknown` |

## 6. 身份、角色与租户字段

### 6.1 核心 ID 定义

| 字段 | 所属系统 | 说明 |
|---|---|---|
| `user_id` | 本地 | 本地用户唯一 ID，任务、Session、Credential、知识库等资源的 owner |
| `staff_id` | 销售总部 | 销售员工唯一 ID |
| `customer_id` | 销售总部 | 销售总部客户档案的内部 ID，不等于 `system_id` |
| `system_id` | 本地/双方 | 一个本地龙信助手实例的稳定身份；克隆/恢复后必须重新核验 |
| `order_id` | 双方 | 业务订单 ID；本地订单与总部订单是两个系统中的关联记录 |
| `operation_id` | 发起方 | 一次异步或高风险操作的稳定 ID |
| `request_id` | 链路 | 一次 HTTP 请求链路 ID，可跨系统传递但不替代幂等键 |

### 6.2 本地角色

当前业务角色保持两级：`user`、`admin`。当前没有细粒度管理员角色，不在首版接口中伪造 `super_admin`、`auditor` 等角色。

现有 `AuthUser` 需要扩展为至少：

```json
{
  "id": "usr_01J...",
  "username": "admin",
  "role": "admin",
  "status": "active",
  "capabilities": ["admin.users.read", "admin.users.write"]
}
```

`capabilities` 是服务端计算后的展示信息，不能作为唯一授权依据；每个 handler 仍必须执行后端权限检查。密码重置时不返回密码的哈希、salt 或 JWT。

### 6.3 销售总部角色

首版只有 `staff`。若未来增加只读、区域、审核、运维角色，新增权限 scope，不改变客户对接 Token 的语义。

## 7. 业务对象与字段合同

### 7.1 本地 User

```json
{
  "id": "usr_01J...",
  "username": "alice",
  "status": "active",
  "role": "user",
  "plan_id": "plan_basic",
  "plan_name": "基础版",
  "monthly_quota": 100000,
  "monthly_used": 1200,
  "bonus_tokens": 5000,
  "account_type": "standard",
  "created_at": "2026-09-15T08:30:00Z",
  "updated_at": "2026-09-15T08:30:00Z"
}
```

状态：`active`、`locked`、`banned`、`deleted`。`locked` 是登录失败的临时状态，`banned` 是管理员设置的业务状态；解锁不等同于修改密码。

约束：用户名 3–32 字符，大小写不敏感，允许字母、数字、`.`、`-`、`_`；密码至少 8 位；用户名唯一；不得通过创建用户接口创建管理员。

### 7.2 SystemAccount 与 LedgerEntry

```json
{
  "system_id": "lx_01J...",
  "pool_tokens": 4200000,
  "total_recharged": "999.00",
  "test_default_tokens": 0,
  "account_count": 12,
  "admin_count": 1,
  "updated_at": "2026-09-15T08:30:00Z"
}
```

- `pool_tokens` 是系统未分配 token 池，不是所有用户余额之和。
- `total_recharged` 是本地已确认到账的累计充值金额，报表可能有延迟。
- `cumulative_consumed` 必须来源于不可抵赖的用量账本，不能由池余额变化反推。
- 额度扣减、分配、退款、充值码兑换、套餐审批必须在一个事务或等价的原子流程内完成。

账本记录至少包含：`ledger_id`、`type`、`delta_tokens`、`balance_after`、`amount`、`order_id`、`related_user_id`、`operator_id`、`source`、`idempotency_key`、`created_at`。账本追加后不可更新；纠错使用反向记录。

### 7.3 SalesHubConfig

```json
{
  "system_id": "lx_01J...",
  "hub_url": "https://sales.example.com",
  "token_masked": "lx_****9d2a",
  "public_key_fingerprint": "sha256:...",
  "outbound_status": "ok",
  "inbound_status": "ok",
  "last_verified_at": "2026-09-15T08:30:00Z"
}
```

读取配置只返回脱敏 Token；更新时空字符串表示保留原值，不允许通过 GET、错误响应或审计结果读取明文 Token。签名私钥只存在销售总部，客户端只保存验证用公钥。

## 8. 本地管理员浏览器 API

认证：JWT Bearer + `role=admin`。所有路径默认需要管理员；缺失或无效 JWT 返回 401，普通用户返回 403。

### 8.1 总览与成员

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/overview` | 系统额度、累计充值、成员数、AI/连接健康和版本 |
| GET | `/admin/users` | 分页查询成员；支持 `keyword`、`status`、`plan_id`、`page`、`page_size` |
| POST | `/admin/users` | 创建普通成员 |
| PATCH | `/admin/users/{user_id}` | 修改状态、套餐或额外额度 |
| DELETE | `/admin/users/{user_id}` | 删除/回收成员；首选软删除，兼容旧实现时由服务层执行完整清理 |
| POST | `/admin/users/{user_id}/reset-password` | 管理员确认后生成一次性密码 |
| DELETE | `/admin/users/{user_id}/sessions` | 撤销目标用户全部会话，可作为封禁和重置密码的内部步骤 |

创建成员请求：

```json
{
  "username": "alice",
  "initial_password": "至少8位的密码",
  "plan_id": "plan_basic",
  "bonus_tokens": 5000
}
```

重置密码请求：

```json
{
  "reason": "用户电话申请重置登录密码",
  "admin_password": "当前管理员密码"
}
```

重置成功只显示一次新密码：

```json
{
  "operation_id": "op_01J...",
  "state": "completed",
  "user_id": "usr_01J...",
  "username": "alice",
  "temporary_password": "一次性明文，仅此响应返回",
  "expires_at": "2026-09-15T09:00:00Z",
  "request_id": "req_01J..."
}
```

该操作必须验证管理员当前密码、记录原因、撤销目标会话、清理锁定计数并写审计；5 分钟内同一管理员最多 5 次；密码不得写入日志。当前管理员和最后一个可用管理员不能被删除、封禁或降级。

### 8.2 套餐、订单与系统额度

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/orders` | 管理员查看本地套餐订单 |
| POST | `/admin/orders/{order_id}/approve` | 审批订单，原子扣减系统池并分配额度 |
| POST | `/admin/orders/{order_id}/reject` | 拒绝待处理订单，不扣额度 |
| GET | `/admin/quota` | 查询系统池和额度统计 |
| PATCH | `/admin/quota` | 修改测试账号默认额度等系统配置 |
| GET | `/admin/quota/ledger` | 查询额度账本，支持时间、类型、用户和订单过滤 |
| POST | `/admin/quota/redeem-code` | 验证并兑换总部签发的离线/在线充值码 |
| POST | `/admin/quota/recharge-requests` | 发起线上充值请求，由后端 SalesHub adapter 调总部 |
| GET | `/admin/quota/recharge-requests` | 查看本地充值请求状态 |

审批订单请求：

```json
{
  "reason": "确认付款和成员套餐",
  "admin_password": "当前管理员密码"
}
```

兑换充值码请求：

```json
{
  "code": "LXRC2....",
  "confirm": true
}
```

兑换成功返回账本结果，但不要求客户端重新计算签名内的金额和 token：

```json
{
  "operation_id": "op_01J...",
  "state": "completed",
  "system_id": "lx_01J...",
  "amount": "1000.00",
  "tokens": 10000000,
  "ledger_id": "led_01J...",
  "pool_tokens_after": 14200000,
  "request_id": "req_01J..."
}
```

充值码必须校验版本、签名、`system_id`、nonce、过期时间和格式；nonce/订单号唯一。重复兑换返回相同业务结果或明确的 `409 code_already_redeemed`，不得再次增加额度。

### 8.3 模型、技能、插件和策略

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/models` | 模型提供商、模型列表、连接状态；只返回 `api_key_exists` |
| POST | `/admin/models` | 校验 URL、模型和限制，先探测真实 API，再保存并重载 |
| PUT | `/admin/models/default` | 切换默认模型，失败必须回滚 |
| DELETE | `/admin/models/{provider_id}` | 删除模型提供商；不能删除最后一个模型 |
| GET | `/admin/skills` | 本地技能列表和展示元数据 |
| POST | `/admin/skills/upload` | 上传并安全解压 `.md`、`.zip`、`.tar.gz` |
| PATCH | `/admin/skills/{skill_id}/metadata` | 修改名称、描述、图标等展示元数据 |
| DELETE | `/admin/skills/{skill_id}` | 删除本地技能 |
| GET | `/admin/skill-store/catalog` | 查询远程技能目录 |
| POST | `/admin/skill-store/install` | 下载、校验并原子替换技能 |
| POST | `/admin/plugins/install` | 安装高风险插件；必须显式确认并审计 |
| GET | `/admin/policy` | 返回执行保护状态，不提供“全部放开”开关 |

模型响应禁止返回 API Key、密码和私钥。模型添加/切换/删除涉及核心重载时，使用第 5.3 节的操作响应。

技能和插件安装必须检查文件类型、压缩包路径穿越、符号链接、解压大小、`SKILL.md`、版本和 SHA-256；使用临时目录和原子替换，失败时保留旧版本。插件安装属于可能执行本地代码的高风险操作，不能与普通技能安装共用无确认接口。

### 8.4 审计

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/audit/events` | 查询审计事件，管理员只能看授权范围内的摘要 |
| POST | `/admin/audit/overview` | 选定目标用户后查看任务、文档等概要 |
| POST | `/admin/audit/sessions/{session_id}` | 输入新原因后查看指定 Session 内容 |
| POST | `/admin/audit/documents/{document_id}` | 输入新原因后查看指定文档内容 |

查看其他用户数据必须采用两步授权：

1. 提交目标用户名和至少 4 个字符的原因，获取任务/文档概要；
2. 再次提交新原因和明确资源 ID，才可读取正文或结果。

每一步都追加审计事件。审计字段至少包括：`event_id`、`actor_type`、`actor_id`、`actor_name`、`target_user_id`、`target_user_name`、`action`、`resource_type`、`resource_id`、`reason`、`request_id`、`status`、`result_summary`、`created_at`。审计日志追加写入，不允许编辑或删除。

### 8.5 升级、备份和恢复

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/upgrade/status` | 应用/核心版本、健康、最近升级结果 |
| GET | `/admin/update-hub/config` | 读取更新中心配置，Token 脱敏 |
| PATCH | `/admin/update-hub/config` | 修改更新中心配置 |
| GET | `/admin/backups` | 查看备份元数据，不返回备份内容 |
| POST | `/admin/upgrades` | 提交应用或核心升级任务，`artifact_type=app|core` |
| POST | `/admin/backups/{backup_id}/restore` | 先创建安全备份，再恢复指定版本 |
| DELETE | `/admin/backups/{backup_id}` | 删除备份，必须确认并审计 |

升级任务请求：

```json
{
  "artifact_type": "app",
  "version": "3.8.2",
  "confirm": true
}
```

升级必须执行：备份、manifest/版本校验、文件类型和路径校验、临时目录下载、SHA-256 校验、原子切换、重启、健康检查、失败回滚。代码回滚与业务数据回滚是两个不同操作，不得互相默认触发；运行中的任务需要按策略暂停或拒绝升级。

### 8.6 Workflow API 预留

Workflow 属于 AgentScope 产品能力，不属于销售系统数据交换。首版保持资源接口语义：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/workflows` | 当前用户可见的流程 |
| PUT | `/workflows/{workflow_id}` | 保存草稿 |
| POST | `/workflows/{workflow_id}/publish` | 校验并发布不可变版本 |
| GET | `/workflows/{workflow_id}/versions/{version}` | 查询版本 |
| POST | `/workflows/runs` | 启动运行，运行实例固定启动时版本 |
| GET | `/workflows/runs/{run_id}` | 查询自己的运行状态 |

管理员不因角色自动获得其他用户流程运行输入/结果；如确需查看，走审计两步流程。发布校验禁止脚本、任意外部 URL、XML 外部实体、任意表达式和无界循环。

## 9. 本地接收销售总部命令 API

基础路径：`/integration/sales/v1`。认证为客户专属 Bearer Token，Token 映射到唯一 `system_id`；请求体中的 `system_id` 只用于一致性检查，不作为身份来源。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/integration/sales/v1/ping` | 总部验证反向连通性和本地版本 |
| POST | `/integration/sales/v1/admin-password-resets` | 总部请求重置本地管理员密码 |
| POST | `/integration/sales/v1/upgrades/{artifact_type}` | 总部下发应用/核心升级命令 |

### 9.1 Ping

请求体为空对象 `{}`。成功响应：

```json
{
  "ok": true,
  "system_id": "lx_01J...",
  "system_name": "龙信助手-客户A",
  "app_version": "3.8.1",
  "core_version": "agentscope-...",
  "checked_at": "2026-09-15T08:30:00Z",
  "request_id": "req_01J..."
}
```

本地必须把 Token 对应的 `system_id` 与响应中的 `system_id` 绑定校验，发现不一致返回 409，不得仅因 HTTP 200 就判定连接成功。

### 9.2 远程重置管理员密码

请求：

```json
{
  "operation_id": "op_01J...",
  "username": "admin",
  "new_password": "一次性随机密码",
  "expires_at": "2026-09-15T09:00:00Z"
}
```

本地只允许 `role=admin` 的目标账号；重置密码哈希、失败计数、锁定状态并撤销现有会话，不修改用户数据和配额。响应中的 `new_password` 只允许一次性返回给总部；普通日志、审计摘要和后续查询均不得显示。

### 9.3 升级命令

请求：

```json
{
  "operation_id": "op_01J...",
  "artifact_type": "app",
  "version": "3.8.2",
  "sha256": "sha256:...",
  "size_bytes": 12345678,
  "download_url": "https://sales.example.com/api/v1/integration/releases/app/latest",
  "issued_at": "2026-09-15T08:30:00Z",
  "expires_at": "2026-09-15T10:30:00Z"
}
```

本地先返回接收状态，再异步下载、校验、备份、应用、重启和健康检查。下载接口仍使用客户 Token；命令中的 URL、类型、版本、大小和 SHA-256 必须全部校验。最终结果通过本地 Ping/状态上报让总部确认，未确认状态为 `unconfirmed`，不能标为成功。

## 10. 销售总部 API

基础路径：`/api/v1`。员工 API 使用总部员工 JWT/Session；`/integration` 子路径同时提供客户系统 Bearer 接口。员工权限和客户 Token 权限必须分开校验。

### 10.1 员工认证与设置

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/login` | 销售员工登录 |
| GET | `/api/v1/auth/me` | 当前员工信息 |
| PATCH | `/api/v1/staff/password` | 修改当前员工密码 |
| GET | `/api/v1/settings` | 总部设置（密钥只返回指纹/脱敏值） |
| PATCH | `/api/v1/settings` | 修改总部设置 |

### 10.2 客户档案

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/customers` | 客户分页、状态和环境过滤 |
| POST | `/api/v1/customers` | 创建客户并生成客户专属 Token |
| GET | `/api/v1/customers/{customer_id}` | 客户详情和最近报表摘要 |
| PATCH | `/api/v1/customers/{customer_id}` | 修改客户名称、协议、地址、联系人和状态 |
| POST | `/api/v1/customers/{customer_id}/api-token/rotate` | 轮换客户 Token，旧 Token 按策略失效 |
| POST | `/api/v1/customers/{customer_id}/verify-connection` | 触发双向连接验证 |
| GET | `/api/v1/customers/{customer_id}/usage-reports` | 查看客户上报快照 |
| GET | `/api/v1/customers/{customer_id}/recharge-orders` | 查看客户充值订单和送达状态 |

客户字段：`customer_id`、`name`、`system_id`、`protocol`、`base_url`、`configured_ip`、`port`、`contact`、`notes`、`environment`、`status`、`api_token_masked`、`last_report_at`、`last_source_ip`。`configured_ip` 与报表中的 `last_source_ip` 必须分开保存，用于发现地址漂移。

创建客户响应可以返回一次性 Token：

```json
{
  "customer": {
    "customer_id": "cus_01J...",
    "system_id": "lx_01J...",
    "name": "客户A",
    "status": "enabled"
  },
  "api_token_once": "仅创建或轮换时显示一次",
  "token_expires_at": null,
  "request_id": "req_01J..."
}
```

### 10.3 充值与对账

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/recharge-requests` | 总部员工查看充值请求 |
| POST | `/api/v1/recharge-requests/{order_id}/approve` | 审核实际付款并生成签名充值码 |
| POST | `/api/v1/recharge-requests/{order_id}/reject` | 拒绝充值请求 |
| POST | `/api/v1/customers/{customer_id}/recharge-codes` | 生成绑定客户的离线充值码 |
| GET | `/api/v1/reconciliation` | 对比充值、额度和累计消耗快照 |
| GET | `/api/v1/alerts` | 余额/报表过期/地址不一致告警 |

审批请求：

```json
{
  "amount": "1000.00",
  "request_id": "local-request-01J...",
  "reason": "已确认到账"
}
```

总部审批只负责产生订单和签名充值码，不代表客户本地已经兑换。订单必须区分：

- `pending`：等待总部处理；
- `approved` + `delivery_status=not_delivered`：已审批，客户尚未领取/确认；
- `approved` + `delivery_status=delivered`：客户已 ACK；
- `rejected`：已拒绝；
- `unknown`：调用超时，待核实。

Ed25519 私钥仅在总部签发服务使用；离线码至少包含 `system_id`、`amount`、`tokens`、`version`、`order_id`、`nonce`、`issued_at`、`signature`。同一订单重复审批、轮询和 ACK 都必须幂等。

### 10.4 客户系统机器接口

以下接口由本地客户系统调用，使用客户 Bearer Token：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/integration/verify-connection` | 总部验证 Token、客户和系统身份，并触发反向 Ping |
| POST | `/api/v1/integration/recharge-requests` | 接收本地线上充值申请 |
| GET | `/api/v1/integration/recharge-requests/poll` | 客户轮询已审批但未送达订单 |
| POST | `/api/v1/integration/recharge-requests/{order_id}/ack` | 客户本地兑换后确认送达 |
| POST | `/api/v1/integration/usage-reports` | 接收客户定期额度/用量快照 |
| GET | `/api/v1/integration/releases/{artifact_type}/latest` | 客户下载已发布的应用/核心包 |
| GET | `/api/v1/integration/public-key` | 获取总部签名公钥 |

线上充值申请：

```json
{
  "system_id": "lx_01J...",
  "amount": "1000.00",
  "note": "季度充值",
  "requested_at": "2026-09-15T08:30:00Z"
}
```

总部必须从 Token 解析客户，不信任任意传入的 `system_id`；传入值不匹配返回 403/409。成功返回：

```json
{
  "order_id": "ord_01J...",
  "status": "pending",
  "delivery_status": "not_delivered",
  "request_id": "req_01J..."
}
```

用量报告：

```json
{
  "system_id": "lx_01J...",
  "pool_tokens": 4200000,
  "total_recharged": "999.00",
  "app_version": "3.8.1",
  "cumulative_consumed": 1250000,
  "cumulative_credits": 5200000,
  "client_reported_at": "2026-09-15T08:30:00Z"
}
```

总部生成 `reported_at`，并根据最近报告时间判断在线状态；报告是快照，不是实时余额。默认约 65 分钟内视为近期在线，阈值应配置化。累计消耗通过相邻快照做对账，不通过 `pool_tokens` 的变化推导。

## 11. 端到端时序

### 11.1 首次接入与双向验证

```text
销售总部 staff
  └─ POST /api/v1/customers
       └─ 返回 customer_id、system_id、api_token_once
本地管理员浏览器
  └─ PATCH /admin/sales-hub/config
  └─ POST /admin/sales-hub/verify
       └─ 本地 → 总部 POST /api/v1/integration/verify-connection
            └─ 总部 → 本地 POST /integration/sales/v1/ping
                 └─ 校验 token 映射的 system_id
```

连接结果必须分别记录 `outbound` 和 `inbound`，单向成功不能显示为“连接正常”。

### 11.2 线上充值

```text
本地管理员
  └─ POST /admin/quota/recharge-requests
       └─ 本地 → 总部 POST /api/v1/integration/recharge-requests
            └─ 总部返回 pending order_id
总部员工
  └─ POST /api/v1/recharge-requests/{id}/approve
       └─ 生成签名码，approved/not_delivered
本地定时任务
  └─ GET /api/v1/integration/recharge-requests/poll
       └─ 本地校验并兑换签名码
       └─ POST /api/v1/integration/recharge-requests/{id}/ack
            └─ 总部标记 delivered
```

“总部审批成功”“客户本地兑换成功”“总部收到 ACK”是三个独立状态，不可合并为一个 `success`。

### 11.3 远程重置与升级

两类命令都遵循：总部记录意图 → 调用本地 → 本地执行并审计 → 总部记录结果。网络超时只记录 `unknown`/`unconfirmed`，后续通过相同 `operation_id` 或 Ping 核实。

## 12. 绝不跨系统传输的数据

默认禁止以下数据从本地传到销售总部：

- 用户密码、密码哈希、salt、JWT 和本地会话；
- 任务、聊天、Session、Workflow 输入/输出和文档正文；
- 模型 API Key、MCP/Plugin 密钥和本地私钥；
- 完整额度账本、完整审计正文、模型请求/响应；
- 银行、财务或客户业务文件。

总部只接收本文件明确规定的客户标识、版本、健康结果、充值请求和聚合额度/用量快照。

## 13. 后端实现落点

建议新增产品层目录（具体命名可按后续模块化调整）：

```text
examples/agent_service/
├─ auth.py                    # 扩展角色/状态；JWT 仍由此统一处理
├─ admin_api.py               # /admin router、Pydantic 请求/响应模型
├─ admin_service.py           # 成员、额度、订单、审计、升级编排
├─ account_store.py           # User/Plan/Session 撤销的产品级持久化抽象
├─ billing_store.py           # SystemAccount/Ledger/Order 原子操作
├─ audit_store.py             # 追加式审计抽象
├─ sales_hub_client.py        # 本地 → 总部 HTTP adapter、重试、幂等
├─ sales_hub_api.py           # /integration/sales/v1 反向命令接收
└─ upgrade_service.py         # 下载、校验、备份、切换、回滚

examples/web_ui/frontend/src/
├─ api/admin.ts               # 管理员 API 方法
├─ api/salesHub.ts            # 连接配置、验证和充值 API
├─ api/adminTypes.ts          # 管理域类型；不要把所有类型塞进 api/types.ts
├─ hooks/useAdmin*.ts         # React Query 查询/变更 hooks
└─ pages/admin/               # 管理员页面
```

装配方式：

1. 在 `examples/agent_service/main.py` 创建并注入 `AccountStore`、`BillingStore`、`AuditStore`、`SalesHubClient`。
2. 把实例放入 `app.state`，通过 `Request` 或专用 `Depends()` 获取。
3. `app.include_router(admin_router)` 和 `app.include_router(sales_hub_router)`，不要在 endpoint 内 new Redis/HTTP client。
4. 通用 AgentScope 的 `get_current_user_id` 继续服务普通资源；管理员接口使用 JWT 用户对象并检查角色。
5. 如果以后多个产品部署都需要管理员能力，再给 `create_app()` 增加可选 `extra_routers`/`extra_services` 扩展点，而不是直接把龙信业务写入通用 router 列表。

### 13.1 账号存储建议

当前 `StorageBase` 没有登录账号、角色、密码版本、计划和账本方法。建议定义产品层 Protocol：

```python
class AccountStore(Protocol):
    async def get_by_id(self, user_id: str) -> Account | None: ...
    async def list_accounts(self, query: AccountQuery) -> Page[Account]: ...
    async def revoke_sessions(self, user_id: str) -> None: ...
```

Redis 和 SQL 实现都必须遵循同一接口；不要让 router 直接依赖当前 `auth.py` 的私有 `_accounts`，也不要把账号管理临时写进 `get_token_usage()`。

### 13.2 事务边界

以下操作必须具备数据库事务或等价的分布式幂等保障：

- 审批订单：校验 pending → 扣系统池 → 写用户额度 → 写账本 → 更新订单；
- 兑换充值码：验签 → 占用 nonce/order → 增加系统池 → 写账本；
- 删除用户：保留消费账本/对账信息，同时清理所属资源、会话和索引；
- 远程重置：更新凭证 → 撤销会话 → 写审计；
- 升级：创建安全备份 → 应用 → 健康检查 → 成功或回滚。

## 14. 前端实现规范

- 在 `api/admin.ts`、`api/salesHub.ts` 中封装所有请求，使用现有 `client.get/post/patch/delete`。
- 使用 React Query：查询 key 包含 `user.id` 或 `system_id`，变更完成后按资源粒度 invalidate；不要在页面组件内维护第二套缓存。
- 管理员菜单通过 `user.role` 做显示控制，但路由和 API 都必须再次检查权限。
- 路由建议：`/admin`、`/admin/users`、`/admin/quota`、`/admin/models`、`/admin/skills`、`/admin/audit`、`/admin/upgrades`。
- `App.tsx` 增加管理员路由，`AppSidebar.tsx` 增加管理员入口；普通用户访问管理员路径显示 403 页面或重定向，不因前端未显示入口而假设安全。
- 上传技能使用统一 client 的 multipart 能力；若当前 client 没有封装，应新增 `client.upload()`，不要在页面中直接拼接 Authorization。
- 金额按字符串展示和提交；token 计数使用整数；密码、Token、API Key 仅在一次性场景显示，并提供明确的复制/隐藏提示。
- 长任务（验证、重置、安装、升级）展示 `operation_id` 和状态查询，不用固定 sleep 猜测完成。

## 15. 测试与验收规范

测试风格沿用当前仓库：`unittest.IsolatedAsyncioTestCase`、FastAPI `TestClient`、fakeredis 测试后端和临时目录；涉及 SQL 时增加 SQL 实现同构测试。

最低测试集合：

| 测试文件建议 | 必测内容 |
|---|---|
| `tests/admin_auth_router_test.py` | 未登录 401、普通用户 403、管理员成功、JWT 角色/状态失效 |
| `tests/admin_account_router_test.py` | 用户名校验、并发创建唯一性、封禁/解封、最后管理员保护、会话撤销 |
| `tests/admin_billing_test.py` | 订单审批原子性、额度不足回滚、充值码验签、nonce 和幂等 |
| `tests/sales_hub_contract_test.py` | Token 映射 system_id、双向验证、超时 unknown、错误脱敏 |
| `tests/sales_hub_recharge_test.py` | submit/poll/redeem/ack 重试不重复加额 |
| `tests/sales_hub_report_test.py` | 快照字段语义、累计消费增量、过期报告、地址漂移 |
| `tests/admin_audit_test.py` | 两步审计、原因校验、资源所有权、追加不可变 |
| `tests/admin_upgrade_test.py` | 文件安全、hash、备份、健康检查、回滚和 unconfirmed |
| `examples/web_ui/frontend/src/...` | `npm run build`、管理员路由守卫、React Query cache invalidation |

关键验收断言：

1. 普通用户不能调用任何 `/admin/*` 和本地反向命令接口。
2. 客户 Token 不能访问总部员工接口，员工 JWT 不能访问客户机器接口。
3. Token、密码、模型 API Key、私钥不出现在响应和日志。
4. 同一幂等请求无论重试多少次，都不会重复扣款、加额、重置或升级。
5. 所有“已审批”“已执行”“已送达”“已验证”状态都有明确的业务证据，不以 HTTP 200 代替。
6. 管理员查看他人任务/文档正文必须有两次原因确认和两条审计记录。
7. 报表缺失、网络超时和数据不足不会被填成 0，也不会被推断成成功。

## 16. 兼容与演进规则

- 首版先实现本文件 canonical path；旧路径只在网关或 adapter 层做兼容，不在业务代码中复制两套逻辑。
- 新增字段默认可选；删除或改变语义必须提升 API 主版本或提供迁移期双读。
- 枚举只能追加，不能复用旧值表达新语义。
- 订单、报告、操作和事件保留 `schema_version`，事件保留 `event_id`；消费方按 `event_id` 去重。
- 后续可将客户 Token 拆成 `report`、`command`、`download` scope，但必须支持过渡期 Token，并在客户配置中显示 scope。
- 发布、升级和充值相关响应应保留 `request_id`、`operation_id`、`order_id` 等关联字段，便于总部和本地日志对账。
- OpenAPI 文档、Pydantic 模型、前端 TypeScript 类型和契约测试必须在同一变更中更新。

## 17. 首期实现顺序

1. 先扩展本地身份模型：账号持久化、`role/status`、会话撤销、管理员依赖。
2. 实现本地成员/系统额度/账本/审计 API，先不连接总部。
3. 实现 `SalesHubConfig`、Token 脱敏、连接验证和双向 Ping。
4. 实现离线充值码验签，再实现线上充值 submit/poll/redeem/ack。
5. 实现用量快照和总部对账。
6. 实现远程重置，补齐一次性密码和超时核实。
7. 实现应用/核心升级任务、备份、回滚和总部发布任务。
8. 最后接入管理员前端页面、权限菜单和 React Query hooks。

在上述接口和状态机稳定前，不建议把销售系统对接直接写进 AgentScope 通用核心，也不建议先做“管理员可以查看全部数据”的快捷接口。
