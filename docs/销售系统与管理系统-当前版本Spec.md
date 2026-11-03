# 销售系统与管理系统当前版本 Spec

> 文档状态：Current implementation spec
> 核对日期：2026-09-17（Asia/Shanghai）
> 代码基线：`4e2bc5a`（`merge: publish latest sales and management systems to h`）
> 适用范围：当前工作区中的新销售运营中心、龙信 AgentScope 管理系统及两者之间的对接协议

## 1. 文档定位

本文以当前工作区代码为第一事实来源，整理销售系统和管理系统的产品边界、角色、数据模型、接口、核心流程和当前交付状态。

状态口径如下：

| 标记 | 含义 |
| --- | --- |
| 已实现 | 当前代码中存在对应路由、服务或页面实现 |
| 部分实现 | 主流程存在，但仍有数据、权限、部署或生产化限制 |
| 待联调 | 需要销售系统地址、客户 Token、网络、证书或现场策略才能验证 |
| P0 | 会阻塞双方真实联调或造成安全/数据一致性风险 |

当前不能把“代码已实现”解释为“已完成生产验收”。当前工作区没有销售系统生产地址、正式客户 Token、完整证书链和双方真实网络验收记录。

## 2. 产品目标与边界

### 2.1 销售运营中心

销售运营中心是面向销售人员的多客户运营控制面，负责：

- 客户系统档案、连接地址、客户 Token 和系统状态管理；
- 系统级充值申请审核、离线充值码签发、充值交付状态跟踪；
- 客户额度/用量汇总、趋势、对账和余额预警；
- `app` 与 `core` 发布包管理及向客户系统下发升级命令；
- 总部 Ed25519 公钥、汇率和操作审计管理。

销售运营中心不承载普通用户聊天，不接收普通用户个人订单，不读取客户管理系统数据库，也不与历史销售总台共享数据库、进程或内部实现。

### 2.2 客户管理系统

客户管理系统是单套龙信 AgentScope 实例的业务管理和运行控制面，负责：

- 登录、管理员权限、成员生命周期和会话撤销；
- 本地套餐目录、套餐订单、系统 Token 池和额度账本；
- 普通用户的聊天、知识库、模型、技能和 MCP 资源使用；
- 管理员维护模型凭证，并按范围向普通用户发布资源；
- 与销售运营中心进行充值、用量上报、连接验证、升级包下载和远程命令接收。

普通用户的套餐申请只在客户管理系统内部生成订单。只有客户管理员触发系统级额度补充时，才通过客户 Bearer Token 调用销售运营中心。

### 2.3 非目标

- 本版本不实现销售支付网关、退款、发票和外部支付回调；
- 本版本不把客户系统的普通用户、聊天内容、文档正文、模型密钥或个人用量明细同步到销售运营中心；
- 本版本不允许通过前端隐藏菜单替代后端权限校验；
- 本版本不将销售运营中心的销售员工身份与客户系统管理员身份合并。

## 3. 总体架构与信任边界

```text
销售人员浏览器
    │ HttpOnly Cookie: sh_session
    ▼
销售运营中心 Web ──▶ 销售 API ──▶ PostgreSQL / JSON 兼容存储
                              ├─ 客户档案、订单、报表、设置、审计
                              └─ 数据卷：发布包、Ed25519 私钥/公钥
                                      │
                     客户专属 Bearer Token（双向）
                                      │
        ┌─────────────────────────────┴─────────────────────────────┐
        ▼                                                           ▼
客户管理系统 Admin Web                                      客户管理系统 API
        │ JWT Bearer                                                │
        ▼                                                           ▼
成员/套餐/额度/资源/升级管理 ──▶ Redis + Qdrant + 文件数据 ──▶ AgentScope 运行时
```

双方通信方向：

| 方向 | 调用方 | 主要用途 | 认证 |
| --- | --- | --- | --- |
| A | 管理系统 → 销售系统 | 充值申请、轮询、ACK、用量上报、连接验证、公钥/升级包下载 | 客户专属 Bearer Token |
| B | 销售系统 → 管理系统 | Ping、远程密码重置、`app/core` 升级命令 | 同一客户专属 Bearer Token |
| C | 销售人员浏览器 → 销售系统 | 客户、订单、发布和设置管理 | 销售员工 HttpOnly Cookie |
| D | 用户/管理员浏览器 → 管理系统 | 聊天、套餐、成员和系统管理 | 管理系统 JWT Bearer |

`system_id` 是双方业务关联的稳定客户系统身份；`customer_id` 是销售运营中心内部客户档案 ID。两者不能混用。

## 4. 当前组件与运行基线

| 系统 | 组件 | 代码位置 | 当前技术/存储 | 默认地址或端口 |
| --- | --- | --- | --- | --- |
| 销售系统 | Admin Web | `sales-console/apps/admin-web` | React 19 + TypeScript + Vite | `http://127.0.0.1:44101` |
| 销售系统 | Sales API | `sales-console/services/sales-api` | TypeScript + Express | `http://127.0.0.1:44100` |
| 销售系统 | 健康检查 | `/health` | 返回 `service`、版本和数据存储类型 | `GET :44100/health` |
| 管理系统 | Web UI | `examples/web_ui/frontend` | React + TypeScript + React Router | Docker 对外默认 `:8000` |
| 管理系统 | Agent API | `examples/agent_service` | FastAPI + AgentScope | 容器内 `:8000`，Docker 默认映射 `:8001` |
| 管理系统 | 主数据 | AgentScope Storage | Redis；Qdrant 保存向量数据 | 由环境变量配置 |

### 4.1 销售系统存储

- 配置 `DATABASE_URL` 时使用 PostgreSQL；当前兼容存储层将业务集合写入 `sales_console_json_store` JSONB 表；
- 未配置 `DATABASE_URL` 时回退到 `SALES_DATA_DIR` 下的 JSON 文件，适合本地开发和联调；
- 发布包位于 `SALES_DATA_DIR/releases`；签名密钥位于 `SALES_DATA_DIR/keys`；
- 私钥缺失、损坏或公私钥不匹配时，销售 API 启动失败，不自动换钥；
- 当前销售员工会话存于进程内 Map，销售 API 重启后会话失效，生产环境仍需持久化会话或接入统一会话服务。

### 4.2 管理系统存储

- 账号由 JWT + Redis 持久化账户记录组成；
- 管理员、系统额度、账本和审计数据位于 `longxin:admin:v1` 命名空间；
- 套餐订单位于 `longxin:plan-billing:v1` 命名空间；
- 升级发布、备份和 operation 元数据位于 `longxin:upgrade:v1` 命名空间，实际包和备份位于 `LONGXIN_DATA_DIR`；
- 销售中心配置位于 `longxin:sales-hub:v1:config`，Token 在配置了 `LONGXIN_CONFIG_ENCRYPTION_KEY` 或 `AGENTSCOPE_JWT_SECRET` 时使用 Fernet 封装；生产环境必须配置加密密钥，不能使用明文兼容模式。

## 5. 角色与权限

### 5.1 角色矩阵

| 角色 | 所属系统 | 核心能力 | 销售系统直接影响 |
| --- | --- | --- | --- |
| 普通用户 `user` | 客户管理系统 | 聊天、查看自己的套餐/用量、提交本地套餐申请、使用已发布资源 | 无 |
| 客户管理员 `admin` | 客户管理系统 | 成员管理、套餐审批、系统额度、销售中心配置、充值同步、资源发布、升级管理 | 间接提交系统级充值申请、接收销售命令 |
| 销售员工 `admin` | 销售系统 | 客户、充值审核、离线码、发布包、批量升级、设置和审计 | 直接改变客户档案、订单、发布和交付状态 |
| 销售员工 `operator` | 销售系统 | 角色字段已存在 | 当前路由主要只校验 active 会话，尚无细粒度操作权限 |

管理系统账号状态为 `active`、`locked`、`banned`、`deleted`；销售员工状态为 `active`、`disabled`。

### 5.2 权限硬规则

- 管理系统 `/admin/*` 和销售系统员工接口均由后端二次校验；
- 客户 Bearer Token 不能访问销售员工接口；销售员工 Cookie 不能访问客户机器接口；
- 客户 Token 绑定一个 `system_id`，请求体中的 `system_id` 只能用于一致性校验，不能作为身份来源；
- 管理员删除成员采用软删除，保留额度账本和审计记录；不能删除管理员自身或其他管理员；
- 远程重置成员密码、远程管理员密码、升级和回滚均需要操作确认、幂等键和审计；
- 模型供应商密钥、客户 Token、充值签名私钥和临时密码不得出现在普通响应、前端日志或业务日志中。

## 6. 核心业务模型

### 6.1 销售系统模型

| 模型 | 关键字段 | 说明 |
| --- | --- | --- |
| `Staff` | `id`、`username`、`role`、`status`、密码摘要 | 销售员工，密码为 salt + scrypt 摘要 |
| `Customer` | `id`、`systemId`、`name`、地址、环境、`apiToken`、状态 | 一个客户管理系统实例；`apiToken` 只在创建/轮换时一次性返回 |
| `RechargeOrder` | `id`、`customerID`、`method`、`status`、金额、Token、code、交付字段 | `method=online` 表示客户申请；`method=code` 表示离线码 |
| `UsageReport` | `poolTokens`、`totalRecharged`、累计消耗/赠送、应用版本、来源 IP | 客户系统周期性汇总快照，不是单笔交易凭证 |
| `ReleaseMeta` | 类型、版本、文件、大小、SHA-256、上传人 | `app` 为龙信业务应用；`core` 为 AgentScope 平台整体 |
| `Settings` | `tokenExchangeRate` | 只影响后续签发/批准的充值码，默认值为 `41841` |

客户状态为 `active` / `disabled`。最近一次报告距当前小于 65 分钟时，页面标记为近期在线；该状态不是实时探活结果。

### 6.2 管理系统模型

| 模型 | 关键字段 | 说明 |
| --- | --- | --- |
| `AuthUser` | `id`、`username`、`role`、`status`、`token_version` | JWT 身份；状态或 token version 变化可撤销会话 |
| 用户档案 | `plan_id`、月额度、已用额度、bonus、有效期 | 普通用户套餐和额度投影 |
| 套餐 | `plan_id`、月 Token、价格、有效期、特性 | 当前为可替换演示目录，无支付闭环 |
| 套餐订单 | 订单类型、目标套餐、状态、分配 Token、审批原因 | `activation` / `renewal` / `upgrade` / `downgrade` |
| 系统账户 | `system_id`、`pool_tokens`、累计充值、累计 credits | 供管理员审批套餐和接收销售充值 |
| 额度账本 | `ledger_id`、增量、余额、来源、订单/用户、幂等键 | 追加式记录，不用余额变化反推消费 |
| `SalesHubConfig` | `system_id`、`hub_url`、Token 掩码、公钥指纹、连接状态 | 管理系统与销售中心的本地连接配置 |
| `UpgradeOperation` | operation、artifact、版本、状态、备份、结果/错误 | 升级状态：pending → downloading → backing_up → applying → health_check → completed/rolled_back/failed |
| 资源发布 | 类型、来源记录、范围、目标用户、启用状态 | 管理员发布技能或 MCP；范围为 all/selected/none |

### 6.3 当前套餐目录

以下值来自 `examples/agent_service/longxin_admin/plan_billing/catalog.py`，价格属于演示配置，正式生产定价需要业务确认。

| `plan_id` | 名称 | 月 Token | 价格 | 有效期 |
| --- | --- | ---: | ---: | ---: |
| `plan_basic` | Basic | 100,000 | `99.00 CNY` | 30 天 |
| `plan_pro` | Pro | 500,000 | `299.00 CNY` | 30 天 |
| `plan_flagship` | Flagship | 2,000,000 | `999.00 CNY` | 30 天 |

套餐审批分配规则：首次开通/续费分配目标套餐全额额度；升级/降级分配目标与当前额度差额；已用额度高于降级后目标额度时拒绝降级；分配不足时返回 `quota_insufficient`。普通用户聊天前检查套餐有效期和剩余额度，管理员不受普通用户套餐拦截。

## 7. 核心业务流程

### 7.1 普通用户套餐流程

```text
普通用户 GET /plans
    → POST /account/orders                         本地 pending 套餐订单
    → 管理员 GET /admin/orders
    → 管理员 POST /admin/orders/{id}/approve|reject
    → approve：校验管理员密码
             → 计算系统池分配/退回
             → 更新用户套餐与有效期
             → 写额度账本和审计
```

该流程不调用销售系统。系统池不足时，管理员需另行发起系统级充值。

### 7.2 在线系统充值流程

```text
客户管理员
  → POST 管理系统 /admin/quota/recharge-requests
  → 销售系统 /api/v1/integration/recharge-requests
  → 销售员工审核：pending → approved / rejected
  → approved 生成 LXRC2 充值码
  → 管理系统轮询 /api/v1/integration/recharge-requests/poll
  → 验签、校验 system_id/订单/金额/时间窗口/nonce
  → 增加系统池并写本地账本
  → POST 销售系统 /api/v1/integration/recharge-requests/{order_id}/ack
  → 双方 delivery_status=delivered
```

管理系统默认启动后台同步循环，间隔 `LONGXIN_RECHARGE_SYNC_INTERVAL_SECONDS`，默认 30 秒；管理员页面也可手动同步。网络失败返回 `unknown` 或部分失败，不得直接视为充值成功。

### 7.3 离线充值码流程

```text
销售员工
  → POST /api/v1/customers/{customer_id}/recharge-codes
  → 按总部汇率计算 Token
  → Ed25519 私钥签发 LXRC2 code，默认有效 1 小时
  → 客户管理员 POST /admin/quota/redeem-code
  → 公钥验签 + system_id/订单/nonce/时间窗口校验
  → 系统池、累计充值、credits 和账本原子化更新
```

同一 nonce 只能成功兑换一次。管理系统只有在配置销售中心公钥时才使用生产级 Ed25519 验签；旧 HMAC 仅在显式设置 `LONGXIN_ALLOW_LEGACY_HMAC_CODES=true` 且存在兼容密钥时可用。

### 7.4 用量上报流程

管理系统汇总当前系统 Token 池、累计充值、普通用户累计消耗、累计赠送和应用版本，向销售系统发送快照。销售系统保存最近报告、来源 IP、历史报告，并用最近报告驱动客户在线/过期展示、对账和预警。

`cumulative_consumed` 当前来自所有非删除普通用户的 AgentScope Token 使用累计值；管理员不计入该汇总。销售系统默认保留最多 20,000 条报告，管理页面展示最近数据。

### 7.5 双向连接验证流程

```text
管理员 POST 管理系统 /admin/sales-hub/verify
  → 管理系统调用销售系统 /api/v1/integration/verify-connection
  → 销售系统使用客户 Token 反向 POST 管理系统 /integration/sales/v1/ping
  → 校验 request_id、system_id、ping.ok、outbound、inbound
  → 保存 outbound_status / inbound_status / last_verified_at
```

### 7.6 升级流程

销售系统：上传包 → 校验 manifest/类型/版本/路径/SHA-256 → 保存发布包 → 选择客户 → 调用客户升级接口 → 重启期间轮询 Ping → 按 `app_version` 或 `core_version` 确认结果。

管理系统：接收命令 → 校验 Bearer Token、类型、版本、有效期、下载地址、大小和 SHA-256 → 下载至 staging → 校验归档 → 备份当前目标 → 原子替换 → 执行重启命令 → 健康检查 → 成功记录版本，失败自动回滚。

`app` 与 `core` 的含义固定如下：

| 类型 | 内容 | 销售页面名称 |
| --- | --- | --- |
| `app` | 龙信业务应用 | 龙信业务应用升级包 |
| `core` | AgentScope 平台整体 | AgentScope 平台升级包 |

### 7.7 远程管理员密码重置

销售系统生成一次性新密码和 5 分钟有效期，调用管理系统 `/integration/sales/v1/admin-password-resets`。管理系统校验调用 Token、目标必须是管理员、`operation_id` 幂等和密码有效期，更新凭证、递增 token version、撤销已有会话并写审计。新密码只在销售系统响应中一次性显示。

## 8. API 规范

### 8.1 通用规范

销售系统新客户端统一使用 `/api/v1` canonical 路径；旧 `/api/*` 仅为兼容别名。

| 项目 | 规范 |
| --- | --- |
| JSON 字段 | canonical 接口使用 `snake_case` |
| 时间 | 带时区的 UTC ISO 8601，例如 `2026-09-17T08:30:00Z` |
| 金额 | 十进制定点字符串，例如 `"1000.00"` |
| Token 数量 | 非负整数；充值码 Token 必须大于 0 |
| 链路 ID | 请求头 `X-Request-ID`，响应回显 `request_id` |
| 状态变更幂等 | `Idempotency-Key` 必填；充值轮询 GET 也要求幂等键 |
| 幂等冲突 | 同一作用域、同一幂等键但请求内容不同返回 409 |
| 错误 | `{ "detail": { "code", "message", "fields?" }, "request_id" }` |

管理系统的用户/管理员 API 使用 JWT Bearer；销售系统反向调用管理系统的命令 API 使用客户 Bearer Token。所有写操作由后端生成或透传 `request_id`，并应保留 `operation_id`、`order_id` 供对账。

### 8.2 销售员工接口

认证：Cookie `sh_session`，Cookie 为 HttpOnly、SameSite=Strict，当前默认会话 12 小时。

| 方法 | 路径 | 用途 | 状态 |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/login` | 销售员工登录 | 已实现 |
| GET | `/api/v1/auth/me` | 当前员工 | 已实现 |
| POST | `/api/v1/auth/logout` | 注销 | 已实现 |
| GET | `/api/v1/dashboard` | 经营总览、趋势、待办、客户概况 | 已实现 |
| GET/POST | `/api/v1/customers` | 客户列表/创建客户并一次性返回 Token | 已实现 |
| GET/PATCH/DELETE | `/api/v1/customers/{customer_id}` | 客户详情、档案更新、停用 | 已实现 |
| POST | `/api/v1/customers/{customer_id}/api-token/rotate` | 轮换客户 Token，一次性返回 | 已实现 |
| POST | `/api/v1/customers/{customer_id}/verify-connection` | 销售系统到客户系统的连接验证 | 已实现 |
| GET | `/api/v1/customers/{customer_id}/usage-reports` | 客户用量历史 | 已实现 |
| GET | `/api/v1/customers/{customer_id}/recharge-orders` | 客户充值订单 | 已实现 |
| POST | `/api/v1/customers/{customer_id}/recharge-codes` | 签发离线充值码 | 已实现 |
| POST | `/api/v1/customers/{customer_id}/reset-admin-password` | 远程重置客户管理员密码 | 已实现 |
| GET | `/api/v1/recharge-requests` | 查询在线充值申请 | 已实现 |
| POST | `/api/v1/recharge-requests/{order_id}/approve` | 审核通过并签发 code | 已实现 |
| POST | `/api/v1/recharge-requests/{order_id}/reject` | 拒绝申请 | 已实现 |
| GET | `/api/v1/reconciliation` | 充值、Token、消耗对账 | 已实现 |
| GET | `/api/v1/alerts` | 余额预警 | 已实现 |
| GET | `/api/v1/audit/events` | 操作审计 | 已实现 |
| GET/PATCH | `/api/v1/settings` | 汇率设置 | 已实现 |
| GET | `/api/v1/public-key` | 员工查看总部公钥 | 已实现 |
| GET/POST | `/api/v1/releases`、`/api/v1/releases/{artifact_type}` | 发布包查询/上传 | 已实现 |
| POST | `/api/v1/upgrade-all/{artifact_type}` | 向选定客户下发升级 | 已实现，但为同步编排 |

### 8.3 销售系统客户机器接口

认证：`Authorization: Bearer <customer-api-token>`。Token 必须映射到唯一客户，`system_id` 只做一致性校验。

| 方法 | 路径 | 请求关键字段 | 响应/结果 |
| --- | --- | --- | --- |
| POST | `/api/v1/integration/recharge-requests` | `system_id`、两位小数 `amount`、`requested_at`、可选 `note` | `order_id`、`pending`、`not_delivered` |
| GET | `/api/v1/integration/recharge-requests/poll?system_id=...` | `system_id`、`X-Request-ID`、`Idempotency-Key` | 已批准订单和 `LXRC2` code |
| POST | `/api/v1/integration/recharge-requests/{order_id}/ack` | `operation_id`、`system_id`、兑换操作 ID、账本 ID | `delivered` |
| GET | `/api/v1/integration/recharge-codes/legacy` | 无 | 历史 nonce 兼容同步 |
| POST | `/api/v1/integration/usage-reports` | 系统池、累计充值、应用版本、累计消耗/赠送、客户端时间 | 接收确认、报告 ID、累计增量 |
| POST | `/api/v1/integration/verify-connection` | 空对象 | 销售系统反向 Ping 结果 |
| GET | `/api/v1/integration/releases/{artifact_type}/latest` | `app` 或 `core` | tar.gz 二进制包 |
| GET | `/api/v1/integration/public-key` | `X-Request-ID` | `public_key_pem`、公钥指纹、`system_id` |

### 8.4 管理系统管理员接口

认证：管理系统 JWT Bearer；以下接口要求 `role=admin` 且 `status=active`。

| 模块 | 路径 | 用途 |
| --- | --- | --- |
| 总览 | `GET /admin/overview` | 系统池、累计充值、成员数、版本和健康 |
| 成员 | `GET/POST /admin/users` | 成员查询/创建 |
| 成员 | `PATCH/DELETE /admin/users/{user_id}` | 状态、套餐、bonus、软删除 |
| 成员 | `POST /admin/users/{user_id}/reset-password` | 管理员确认后生成临时密码 |
| 成员 | `DELETE /admin/users/{user_id}/sessions` | 撤销成员会话 |
| 额度 | `GET /admin/quota` | 查询系统池和额度统计 |
| 账本 | `GET /admin/quota/ledger` | 查询额度流水 |
| 本地套餐 | `GET /admin/plans`、`GET /admin/orders` | 套餐目录和套餐订单 |
| 本地套餐 | `POST /admin/orders/{order_id}/approve|reject` | 审批本地套餐订单 |
| 充值 | `POST /admin/quota/redeem-code` | 验签并兑换充值码 |
| 销售充值 | `POST /admin/quota/recharge-requests` | 提交系统级在线充值申请 |
| 销售充值 | `GET /admin/quota/recharge-requests` | 查看本地充值影子订单 |
| 销售充值 | `POST /admin/quota/recharge-requests/sync` | 轮询、兑换、ACK |
| 销售配置 | `GET/PATCH /admin/sales-hub/config` | 配置 `system_id`、URL、Token、公钥 |
| 销售配置 | `POST /admin/sales-hub/verify` | 双向连接验证 |
| 用量 | `POST /admin/sales-hub/usage-report` | 上报系统汇总 |
| 审计 | `GET /admin/audit/events` | 查看审计摘要 |
| 审计 | `POST /admin/audit/overview` | 以理由查看指定用户概要 |
| 资源 | `GET/POST /admin/resources` | 查询/发布技能和 MCP |
| 策略 | `GET /admin/policy` | 查看执行保护策略 |

当前代码中正式使用的是 `/admin/sales-hub/*`；旧资料中的 `/admin/integration/sales/*` 不应作为新客户端入口。

### 8.5 管理系统反向命令接口

基础路径：`/integration/sales/v1`。认证为销售系统持有的客户专属 Bearer Token。

| 方法 | 路径 | 关键请求字段 | 当前状态 |
| --- | --- | --- | --- |
| POST | `/ping` | 空对象 | 已实现；返回 `system_id`、应用/核心版本、健康和 `request_id` |
| POST | `/admin-password-resets` | `operation_id`、`username`、`new_password`、可选 `expires_at` | 已实现；含幂等、过期和审计 |
| POST | `/upgrades/app` | operation、版本、SHA-256、大小、下载 URL、时间窗口 | 已实现代码；存在摘要格式联调差异 |
| POST | `/upgrades/core` | 同上 | 已实现代码；存在摘要格式联调差异 |

### 8.6 错误与重试

| 状态码 | 语义 | 调用方处理 |
| --- | --- | --- |
| 400/422 | 参数、格式或业务前置条件错误 | 记录原因，不盲目重试 |
| 401 | Token/Cookie 无效 | 告警、暂停调用，不循环重试 |
| 403 | 权限不足或管理员确认失败 | 转人工处理 |
| 404 | 资源、订单或发布包不存在 | 校验 ID 和配置 |
| 409 | 状态冲突、金额/系统不匹配、幂等键复用 | 按业务状态查询，不重复扣款/入账 |
| 429 | 限流 | 按 `Retry-After` 或指数退避 |
| 502/503/504 | 对端、网络或依赖暂不可用 | 保留 `unknown`，使用相同幂等键重试 |

管理系统 `SalesHubClient` 当前连接超时默认 3 秒、总超时默认 8 秒、可重试请求最多 3 次；销售系统反向调用客户系统的单次 HTTP 超时为 120 秒，并支持配置的内部地址优先回退到客户配置地址。

## 9. 安全、幂等和数据一致性要求

### 9.1 充值码

充值码格式：`LXRC2.<payload>.<signature>`。Payload 至少包含：

```json
{
  "system_id": "customer-system-001",
  "amount": "1000.00",
  "tokens": 41841000,
  "version": "1",
  "order_id": "order-id",
  "nonce": "unique-nonce",
  "issued_at": "2026-09-17T08:30:00Z",
  "expires_at": "2026-09-17T09:30:00Z"
}
```

销售系统只保存 Ed25519 私钥并签名；管理系统保存公钥并验签。管理系统兑换时必须检查签名、版本、金额规范化、Token 正整数、`system_id`、订单号、nonce、签发/过期时间和订单金额一致性。

### 9.2 幂等

- 所有创建、审批、拒绝、兑换、ACK、密码重置、升级、配置更新和发布包写操作携带 `Idempotency-Key`；
- 幂等记录必须绑定调用身份、接口作用域和请求摘要；
- 同一幂等键重试应返回同一业务结果，不得重复签发、加额、扣额、重置密码或启动升级；
- 同一幂等键对应不同请求内容必须返回 `409 idempotency_key_reused`；
- 网络超时后不能用新幂等键直接创建同一充值申请，应使用原幂等键查询/重试。

### 9.3 审计

审计至少保留操作者/系统身份、目标资源、动作、原因、结果、`request_id`、来源 IP 或系统来源和 UTC 时间。敏感值只记录摘要、掩码或指纹。

### 9.4 升级包

上传/下载/执行均需校验：

- `manifest.json` 存在且类型、版本一致；
- tar 路径不含绝对路径、`..` 穿越、软链接、硬链接或设备文件；
- 包大小、文件数量、SHA-256 一致；
- 升级目标不在数据目录内；
- 升级前有可恢复备份；
- 重启或健康检查失败自动回滚；
- 升级完成后通过 Ping 返回目标版本。

## 10. 当前交付状态与已知差距

### 10.1 已具备能力

| 能力 | 销售系统 | 管理系统 | 说明 |
| --- | --- | --- | --- |
| 员工/管理员登录和权限 | 已实现 | 已实现 | 两边身份体系独立 |
| 客户/成员管理 | 已实现 | 已实现 | 管理系统成员支持软删除和会话撤销 |
| 套餐与本地额度 | 不适用 | 已实现 | 套餐价格仍是演示配置 |
| 在线系统充值 | 审核/签发已实现 | 提交/轮询/兑换/ACK 已实现 | 真实网络和订单待联调 |
| 离线充值码 | 签发已实现 | Ed25519 兑换已实现 | 公钥需安全配置 |
| 用量快照 | 接收/展示/对账已实现 | 生成/上报已实现 | 上报频率和生产告警需确认 |
| 双向连接验证 | 已实现 | 已实现 | 需现场网络和 Token |
| 发布包 | 上传/校验/下载已实现 | 下载/校验/备份/回滚已实现 | 目标目录、重启和健康检查需配置 |
| 审计 | 已实现摘要 | 已实现摘要和二次访问审计 | 细粒度销售角色仍缺失 |
| 资源发布 | 不适用 | 技能/MCP 按范围发布已实现 | 资源内容仍在客户系统内 |

### 10.2 P0/P1 待处理项

| 优先级 | 问题 | 影响 | 建议处理 |
| --- | --- | --- | --- |
| P0 | 销售系统发送升级字段 `sha256:...`，管理系统要求纯 64 位 hex | `/integration/sales/v1/upgrades/{type}` 当前会被管理系统 422 拒绝 | 统一合同：建议双方都使用纯 64 位 hex；若保留前缀则两侧统一解析并更新契约测试 |
| P0 | 真实销售中心 URL、客户 Token、正式 system_id、公钥和网络未交付 | 无法宣称双向联调完成 | 补齐测试环境资料并执行 API-CHECKLIST |
| P0 | 正式公钥轮换、Token 轮换/撤销流程未形成运维闭环 | 影响生产密钥安全和故障恢复 | 明确公钥版本、轮换窗口、Token scope、撤销和回滚 |
| P1 | 销售批量升级为同步 HTTP 编排，升级任务未持久化为可重试队列 | 批量目标多或销售 API 重启时难以恢复 | 引入持久化 operation、任务队列和分目标状态 |
| P1 | 销售 PostgreSQL 当前仍使用单表 JSONB 兼容存储 | 事务、约束、查询和并发能力不足 | 按客户、订单、报告、发布、升级、审计拆分正式表并加唯一/状态约束 |
| P1 | 销售员工 `operator` 角色尚未细分路由权限 | 运营和只读审计无法真正隔离 | 增加 capability/permission 级别和写操作授权策略 |
| P1 | 充值报告累计值回退、异常增量的处理策略未完全固化 | 对账和预警可能受异常快照影响 | 明确单调性、回退、补报和人工校正规则 |
| P1 | 管理系统公钥/发布包当前主要依赖手工配置/远程下载流程 | 初始化和密钥轮换易出错 | 增加公钥指纹确认、自动拉取和安全落盘流程 |
| P2 | 套餐支付、退款、定价、并发和不同 Token 类型计费未确定 | 无法进入正式商业化计费 | 由产品/销售确认价格、权益、账期和支付系统边界 |

## 11. 联调验收标准

### 11.1 基础连接

- [ ] 正确客户 Token 调用销售系统成功，错误 Token 返回 401；
- [ ] 销售系统可访问管理系统 Ping，返回匹配的 `system_id`；
- [ ] 管理系统可调用销售系统连接验证，`outbound`、`inbound` 和 `ping` 均正确；
- [ ] 两边均能透传并回显同一 `X-Request-ID`；
- [ ] HTTPS、内网 CA、防火墙和 DNS 在测试环境完成验证。

### 11.2 充值

- [ ] 客户管理员提交线上充值申请，销售系统生成唯一 `order_id`；
- [ ] 销售员工批准后，管理系统轮询获得完整充值码；
- [ ] 公钥验签、金额、Token、订单、`system_id`、nonce 和时间窗口全部通过；
- [ ] 兑换后系统池、累计充值、credits 和账本只增加一次；
- [ ] ACK 后销售系统标记 `delivered`；
- [ ] 重复 submit/poll/redeem/ACK 不重复创建订单或入账；
- [ ] 销售员工拒绝后管理系统拿不到充值码；
- [ ] 断网、超时、重启和 409 情况可恢复且不重复入账。

### 11.3 用量与预警

- [ ] 管理系统启动后按约定上报，周期和失败重试符合约定；
- [ ] 销售客户页能看到最新额度、版本、来源 IP 和上报时间；
- [ ] 累计消耗/赠送异常回退不会被错误当成正常增量；
- [ ] 至少两次有效报告后才进行消耗速率和余额预警估算；
- [ ] 对账结果能区分订单金额、系统快照和累计消耗。

### 11.4 升级和密码重置

- [ ] `app`、`core` 两种包均能上传、校验、下载和执行；
- [ ] 升级命令的 SHA-256 格式在双方一致；
- [ ] 管理系统能完成备份、替换、重启、健康检查和自动回滚；
- [ ] 升级成功后 Ping 返回目标版本；
- [ ] 重复 `operation_id` 不重复执行；
- [ ] 远程密码重置仅影响目标管理员，临时密码按有效期失效，已有会话被撤销；
- [ ] 失败升级、重置和回滚均有 operation/audit 记录。

## 12. 交付前配置清单

### 销售系统

```text
SALES_ADMIN_USERNAME=<销售员工账号>
SALES_ADMIN_PASSWORD=<生产密码>
DATABASE_URL=<生产 PostgreSQL>
SALES_DATA_DIR=<持久化数据目录>
WEB_ORIGIN=<销售 Web 地址>
SALES_PUBLIC_URL=<客户可访问的销售 API 地址>
```

需持久化：PostgreSQL、`SALES_DATA_DIR/releases`、`SALES_DATA_DIR/keys`。生产环境必须配置 HTTPS/反向代理、日志脱敏、备份和恢复演练。

### 管理系统

```text
LONGXIN_SYSTEM_NAME=<客户系统名称>
LONGXIN_SYSTEM_ID=<与销售系统绑定的稳定 ID>
LONGXIN_APP_VERSION=<龙信业务应用版本>
LONGXIN_CORE_VERSION=<AgentScope 平台版本>
SALES_CENTER_BASE_URL=<销售 API 根地址>
SALES_CENTER_API_TOKEN=<客户专属 Token 或 Secret 文件>
LONGXIN_CONFIG_ENCRYPTION_KEY=<配置加密密钥>
LONGXIN_DATA_DIR=<升级和备份数据目录>
LONGXIN_APP_TARGET_DIR=<应用升级目标目录>
LONGXIN_CORE_TARGET_DIR=<核心升级目标目录>
LONGXIN_UPGRADE_RESTART_COMMAND=<重启命令>
LONGXIN_UPGRADE_HEALTHCHECK_URL=<健康检查地址>
```

公钥需要写入管理系统销售中心配置并核对指纹；Token、密码和私钥不能写入代码仓库、前端构建产物或普通聊天记录。

## 13. 维护与变更规则

- 新客户端只能以 `sales-console/docs/API-CONTRACT.yaml` 的 `/api/v1` 作为销售接口入口；
- 任何接口字段、枚举、状态或签名规则变更必须同步更新 API Contract、服务端模型、前端类型和契约测试；
- 不删除或复用已有枚举表达新语义；新增字段默认可选；破坏性变更提升主版本或提供迁移期双读；
- 所有订单、报告、升级 operation 和审计事件应保留可追踪 ID；
- 旧资料与当前代码冲突时，以当前代码和本 Spec 为准，并在变更记录中注明迁移策略；
- 任何真实联调结论必须附测试环境、时间、请求 ID、响应码和验收结果，不以本地 mock 或代码存在替代。

## 14. 相关资料

- 销售 API 合同：`sales-console/docs/API-CONTRACT.yaml`
- 销售联调验收：`sales-console/docs/API-CHECKLIST.md`
- 销售业务规则：`sales-console/docs/BUSINESS-RULES.md`
- 销售数据模型：`sales-console/docs/DATA-MODEL.md`
- 管理系统当前销售对接实现：`docs/contracts/销售系统API对接-当前代码实现.md`
- 管理系统管理员 API：`examples/agent_service/admin_api.py`
- 管理系统销售 HTTP 适配器：`examples/agent_service/sales_hub_client.py`
- 管理系统升级模块：`examples/agent_service/longxin_admin/upgrade/`
