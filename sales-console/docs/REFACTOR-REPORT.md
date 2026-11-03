# sales-console 结构性重构基线与阶段性交付

日期：2026-10-28
范围：仅 `sales-console/`。本报告覆盖重构前基线、已完成的低风险拆分、兼容边界、迁移设计与回滚方案。

## 1. 范围与现状

`sales-console` 使用 React + TypeScript + Vite、Express + TypeScript、PostgreSQL JSONB 存储；本次没有更换技术栈、修改其他项目、删除业务功能或改变客户 API、Cookie/Bearer 认证、签名密钥、充值码及升级包协议。现有 JSONB 数据和密钥文件均未触碰。

开始时 `sales-console/` 没有未提交改动。工作区其他目录已有未提交文件，本次没有改动这些文件。

### 1.1 源码规模与接口分布

| 模块                                                | 重构前行数 | 当前行数 | 当前职责                                             |
| --------------------------------------------------- | ---------: | -------: | ---------------------------------------------------- |
| `apps/admin-web/src/App.tsx`                        |      2,019 |      131 | 应用初始化、会话、导航、路由装配                     |
| `services/sales-api/src/app.ts`                     |      2,331 |      864 | Express 初始化、共享中间件、鉴权和路由装配           |
| `services/sales-api/src/business.ts`                |         99 |       99 | 充值签名、金额、客户连接和汇总规则                   |
| `services/sales-api/src/contract.ts`                |         83 |       83 | snake_case/camelCase、金额/时间和 artifact type 转换 |
| `services/sales-api/src/store.ts`                   |        294 |      419 | JSON/PostgreSQL 兼容存储、事务读改写和密钥文件       |
| `services/sales-api/src/types.ts`                   |         96 |       96 | 持久化业务模型                                       |
| `services/sales-api/src/contracts/legacy-compat.ts` |          — |      331 | `/api/v1` 与旧路由的映射及响应转换                   |
| `services/sales-api/src/routes/`                    |          — |      902 | 客户、充值、报表、设置和升级 HTTP 路由               |
| `services/sales-api/src/services/`                  |          — |      993 | 客户、充值、报表、设置和升级业务操作                 |
| `services/sales-api/src/repositories/`              |          — |      112 | 业务集合与升级包元数据存取                           |
| `services/sales-api/src/infrastructure/`            |          — |       56 | 升级包归档校验                                       |

后端当前有 39 个 Express 路由声明，包含健康检查、canonical 直连路由和旧路由实现。前端有约 25 个 API 调用点，现由统一 API client 承担请求头、错误和响应适配。

### 1.2 重构前主要问题

1. `App.tsx` 同时包含登录、经营总览、客户、充值审核、对账、预警、升级、公钥、设置、共享组件、接口请求与数据转换，页面职责难以独立验证。
2. `app.ts` 同时处理 Express 启动、身份验证、请求追踪、幂等记录、canonical 路由重写、业务规则、客户系统 HTTP 调用、存储读写和上传升级流程。多数路由直接组合 `db.*`、状态转换和响应格式。
3. 前后端都有 snake_case/camelCase 和业务对象映射。后端 `fromCanonical`、`canonicalTarget`、`canonicalResponseFor` 与前端 `fromCanonical`、`normalizeCanonicalResponse` 共同完成兼容转换；部分字段又有路径专用映射。
4. 前端使用的类型多为宽泛页面模型，响应字段映射包含 `any`，转换责任不易定位。
5. 数据仓库使用一个 `sales_console_json_store` 表按业务集合保存整体 JSONB（staff、customers、orders、reports、settings、audit、idempotency），不是逐客户/订单的关系表。
6. `serial()` 只协调单个 Node 进程内的读改写；多 API 实例不能依靠它避免覆盖更新。
7. 销售人员 Session 保存在进程内 `Map`；进程重启或切换实例会使原 Cookie 对应的会话失效。
8. 升级包上传用 Multer `memoryStorage`，文件大小上限为 300 MiB；并发上传会同时占用进程内存。
9. 批量升级在请求处理中逐客户执行，HTTP 调用最长 120 秒，升级确认最多轮询 30 次、每次间隔 2 秒；任务状态没有持久化。
10. 原有 API 测试只有两项签名密钥测试，未覆盖 canonical 响应、客户 Token、充值幂等和交付确认。

## 2. 关键业务链路与风险基线

| 链路                  | 当前实现检查结果                                                                                                                               | 需要保留的行为 / 风险                                                                                                                                                                   |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 登录与鉴权            | 密码通过 `scrypt` 校验；员工使用 `HttpOnly; SameSite=Strict` Cookie；客户系统使用独立 Bearer Token；客户机器接口不接受员工 Cookie              | 认证协议和角色语义必须保持。Session 存在进程内，重启/多实例会丢失会话；登录限流计数也在进程内                                                                                           |
| 客户创建与 Token 轮换 | Token 为 24 字节随机值的十六进制表示；创建和轮换通过 `api_token_once` 返回一次性明文                                                           | canonical 响应不得增加明文 Token 查询。现有管理页面仍使用旧 `GET /api/customers/{id}/api-token` 读取已存 Token，调用方未核查前需保留该旧接口                                            |
| 充值申请与审批        | canonical 状态变更要求 `Idempotency-Key`；相同请求返回缓存结果，不同请求体复用 key 返回 409；审批会签发 Ed25519 充值码并更新订单及客户累计充值 | PostgreSQL 下订单和客户聚合通过 `writeJsonBatch` 同一事务写入；JSON 文件模式逐文件写入，进程内串行不能覆盖崩溃中断或多实例并发。幂等索引当前保存在单个整体 JSONB 项中，多实例争用不安全 |
| 充值轮询与 ACK        | 轮询检查签名/有效期，返回充值码并记递送次数；ACK 更新交付状态及 redemption/ledger 标识                                                         | 金额字符串、状态、签名格式、nonce/订单关联、Request ID 和幂等响应必须不变。幂等记录和业务更新目前不是同一数据库事务                                                                     |
| 升级包上传与批量升级  | 上传先在内存接收，随后落盘并校验 tar 路径、manifest、版本和必要文件；批量升级同步调用客户系统并轮询版本                                        | `app`/`core` 类型、下载路径、签名/摘要字段和客户系统命令均需保持。内存峰值与长请求风险需另行通过流式落盘和持久任务设计解决                                                              |

## 3. 已完成的低风险模块拆分

### 3.1 前端

`App.tsx` 现在仅负责身份状态检查、导航、登出、全局刷新和页面装配。业务界面保留原页面、字段、操作及样式；没有隐藏或删除导航功能。

```text
apps/admin-web/src/
├─ App.tsx
├─ api/client.ts
├─ components/ui.tsx
├─ features/
│  ├─ auth/Login.tsx
│  ├─ dashboard/Overview.tsx
│  ├─ customers/Customers.tsx
│  ├─ recharge/Requests.tsx
│  ├─ reconciliation/{Reconciliation,Alerts}.tsx
│  ├─ upgrades/Upgrades.tsx
│  └─ settings/{PublicKey,Settings}.tsx
├─ lib/format.ts
├─ types.ts
├─ main.tsx
└─ styles.css
```

拆分后的依赖关系：页面组件 → `api/client.ts`、`components/ui.tsx`、`lib/format.ts` 和 `types.ts`；`App.tsx` → 登录及各业务页面。响应转换仍集中在一个客户端边界，业务组件继续使用当前 camelCase 视图模型，避免在此阶段扩大数据模型变更。

### 3.2 后端

路由已按客户、充值、报表、设置和升级领域移入 `src/routes/`；对应写操作与领域计算位于 `src/services/`。Service 依赖 `SalesRepository` 接口而不读取 Express Request/Response；Repository 适配现有 JSON/JSONB store。发布包元数据另有 repository，tar 校验位于 infrastructure。

`app.ts` 现在是组合根，保留 Express 初始化、统一认证和 canonical 中间件、错误响应以及登录/会话等尚未拆分的通用端点。`legacy-compat.ts` 继续负责旧路径和 canonical 响应转换。本次保持全部旧 API 路径，不改变身份认证、签名或升级包协议。

### 3.3 API 错误响应修正

契约测试发现：缺少 `Idempotency-Key` 时，服务器返回 HTTP 400，但响应转换先命中了客户创建分支，错误体被错误地序列化成空客户结构。已将含 `detail` 的错误响应优先于成功响应映射处理，错误仍返回原有 `detail.code/detail.message` 形状及 `request_id`。这修复的是已有契约实现，不改变路径或请求协议。

## 4. API 兼容性矩阵

| 类型         | 当前接口 / 形式                                                                           | 当前用途                                                    | 处理意见                                                                                   |
| ------------ | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| 必须长期保持 | `/api/v1/auth/*`                                                                          | 员工登录、当前会话、注销；Cookie 名和属性保持               | 保留路径、认证和响应语义                                                                   |
| 必须长期保持 | `/api/v1/customers/*`                                                                     | 客户档案、Token 轮换、用量、订单、充值码、连接验证          | 保留字段、状态码、幂等和一次性 Token 语义                                                  |
| 必须长期保持 | `/api/v1/integration/*`                                                                   | 客户 Bearer 认证、充值申请/轮询/ACK、报告、公钥、发布包下载 | 属于客户系统集成协议；不得改 Bearer 边界、签名、充值码、Request ID 或 ACK 字段             |
| 必须长期保持 | `/api/v1/recharge-requests/*`、`/reconciliation`、`/alerts`、`/audit/events`、`/settings` | 销售人员充值审核、财务、预警、审计、系统设置                | 保留 canonical snake_case、ISO 时间和金额字符串                                            |
| 必须长期保持 | `/api/v1/releases/*`、`/api/v1/upgrade-all/{app\|core}`                                   | 发布包上传、目标客户升级及客户系统升级命令                  | 保留 `app`/`core`、manifest、SHA-256 和现有下载/升级路径                                   |
| 暂时保留     | `/api/*` 旧路由及 canonical middleware 的重写目标                                         | 现有 `/api/v1` 目前复用大量旧 handler；未核查客户调用方     | 先加契约测试、迁移调用方，再逐路由迁移；本次不删除                                         |
| 暂时保留     | `GET /api/customers/{id}/api-token`                                                       | 当前管理页面仍用它读取已有 Token                            | 新页面设计替代方案并审计调用方之前不删除；它是旧员工接口，不加入 canonical 明文 Token 查询 |
| 可确认删除   | 无                                                                                        | 当前仓库和交付资料不足以证明某个兼容入口无人使用            | 本阶段没有可安全删除的接口                                                                 |

`canonicalTarget` 映射的显式路径包括：设置 PATCH → 旧设置 PUT；认证 me → 旧 auth status；audit events → operation audit；public key → 旧 public key；客户 usage reports → 旧 usage；releases 和 integration releases → 旧发布 handler；integration usage reports、verify connection、legacy recharge codes、recharge requests、poll 和 ACK → 对应旧机器接口。其他 `/api/v1` 路径按后缀映射到 `/api` handler。客户 Token 查询的 canonical 路径没有直连 handler，前端当前使用旧路径。

本次没有删除旧接口，也没有改变任何响应字段。API 合同基准以 [API-CONTRACT.yaml](./API-CONTRACT.yaml) 和 [API-CHECKLIST.md](./API-CHECKLIST.md) 为准。

## 5. 一级导航方案（仅设计，未改导航）

当前 8 项入口仍原样保留。拟议的 5 项一级导航与功能映射如下：

| 新一级导航 | 当前页面/功能映射            | 页面方案                                                                         |
| ---------- | ---------------------------- | -------------------------------------------------------------------------------- |
| 工作总览   | 经营总览                     | 保留经营指标、充值趋势、待办、客户额度分布和最近动态                             |
| 客户管理   | 客户系统                     | 保留客户档案、连接、Token、远程支持、充值记录、上报记录                          |
| 充值与财务 | 充值审核、对账中心、余额预警 | 以页内二级标签或子导航切换“充值审核 / 对账中心 / 余额预警”，每项保留原操作和数据 |
| 版本与升级 | 升级管理                     | 保留 app/core 包上传、校验、选择客户、升级和结果                                 |
| 系统管理   | 总部公钥、系统设置           | 页内二级入口呈现“总部公钥 / 汇率与密码 / 审计记录”                               |

导航实施前应做页面走查并验证每个旧功能可达；现有导航与链接在此阶段没有变更。

## 6. 数据库与异步任务设计

当前 PostgreSQL 单表存储按集合保存 JSONB，既保留 JSON 文件开发模式，也通过进程内队列串行读改写。拟议关系表见 [001_sales_normalized_schema.sql](./migrations/001_sales_normalized_schema.sql)，历史数据增量回填见 [002_backfill_sales_json_store.sql](./migrations/002_backfill_sales_json_store.sql)。脚本未连接当前销售数据库执行。

设计要点：

1. 将员工、客户、充值订单、用量报告、设置、审计、幂等记录分别落表；发布包本体和签名密钥仍保留在现有数据卷，元数据可单独建表。
2. 充值审批继续在一个 PostgreSQL 事务中更新订单和客户累计值。订单状态使用 CHECK/受控迁移；客户 system_id 在重复值预检通过后设唯一索引。
3. 幂等记录以 `(scope, idempotency_key)` 唯一约束防止多实例重复受理；request hash 不匹配返回现有 409 语义。充值码兑换可进一步为 `(customer_id, redemption_operation_id)` 增唯一约束，实施前需和客户系统账本语义核对。
4. 多实例读改写用行锁或事务级更新，不能以进程内 Promise 队列作为并发保证。升级 worker 可通过 `FOR UPDATE SKIP LOCKED`、租约和过期回收领取持久任务。
5. 现有 Session Map 无可迁移记录；新 Session 表从切换时开始写入，Cookie 协议不变。切换时要求员工重新登录是可预期的会话生命周期边界。
6. 升级任务持久化后，旧同步 endpoint 的请求/响应协议仍需保持。可以由 handler 等待任务结果维持兼容，后续如需任务状态轮询，应另行评审 additive API 契约。
7. 回填采用新增表、`ON CONFLICT DO NOTHING`，并保留原 JSONB 行。迁移前需备份并检查：客户 system_id 重复、订单/报告孤儿、幂等键重复、异常金额/状态、各集合计数和内容摘要。冲突未清理前不切换读写源。
8. 建议先影子读取并比较规范化对象，再按域切换读写；观察期内保留可回退写入路径与 JSONB 快照。完成恢复演练及回滚窗口后，另行批准移除旧仓库代码。

### 6.1 迁移与回滚验证

本次使用隔离的 PostgreSQL 16 临时容器和 7 个 JSONB 源集合 fixture 执行两份脚本两次：新表创建成功、七类数据各一条完成回填，幂等记录的 `operationID` 回填到 `operation_id`，第二次执行没有重复插入；原存储 7 行的 JSONB 摘要在迁移前后完全一致。容器无端口映射、无持久卷，执行后已移除。

这是脚本语法、增量性、幂等回填和“切回旧 JSONB 读取”的回滚路径验证，不代表已在客户数据上执行迁移。未对真实销售容器做读写或重启；新表若被弃用可留存，不需要 DROP 表即可回退应用读取源。

## 7. 测试与验证

| 验收项                         | 结果                                                                                                                                                                |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 前端构建与 TypeScript 类型检查 | 通过：Node 20 / pnpm 9 下运行 admin-web `tsc -b` 与 Vite build                                                                                                      |
| 后端构建与类型检查             | 通过：Node 20 / pnpm 9 下运行 sales-api `tsc -p tsconfig.json`                                                                                                      |
| ESLint                         | 通过：`admin-web` ESLint；`sales-api` 当前没有声明 ESLint 脚本或配置                                                                                                |
| 现有签名密钥测试               | 通过：跨重启签发/验签、缺失/不匹配密钥 fail-closed，共 2 项                                                                                                         |
| 新增 API 契约测试              | 通过：客户创建/更新/Token、Cookie/Bearer 边界、在线/离线充值、重复审批、幂等键冲突、超时后恢复、签名码轮询/ACK、消费上报、对账、设置及发布包查询，共 1 个端到端场景 |
| PostgreSQL 并发集成测试        | 通过：隔离 PostgreSQL 16 容器中由独立 Node 进程并发提交、重复审批、重复离线充值、事务失败回滚后重试和已提交未缓存响应重试，共 1 个集成场景                          |
| SQL 迁移脚本                   | 通过：临时 PostgreSQL 上建表、回填、重复执行和源数据摘要保留验证                                                                                                    |
| 升级上传/远端批量升级          | 未做端到端客户 VM 联调；已检查上传内存上限和同步任务行为，重构实施留待单独方案及模拟客户测试                                                                        |

验证期间通过 Node 20.20.2 与 pnpm 9.15.9 直接运行两个 workspace package 的 build、测试和 lint 命令。根目录聚合 `pnpm build` 的子命令解析到了机器上的 pnpm 11 / Node 24，因此 Node 20 验收结果以分别执行 `--filter admin-web build` 和 `--filter sales-api build` 为准。后端没有单独的 ESLint 配置，未以格式检查替代 ESLint。

验证中的临时失败分类：Node 20 的 `node --test` 自动发现了测试 fixture 和独立 PostgreSQL 集成入口，因此常规测试脚本改为显式列出两个现有 `.test.mjs` 文件，集成测试仍由单独的 `test:postgres` 命令执行；对账场景最初把发生在查询时间窗之后的离线订单计入预期，随后按接口时间窗语义修正测试数据预期。两项签名测试、API 契约测试和 PostgreSQL 并发测试最终全部通过，没有删除、跳过测试或放宽业务断言。

## 8. 分阶段修改清单

| 阶段                                   | 已完成/待做                                                                                                         | 主要文件                                                                                                           |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| 基线、契约和风险检查                   | 已完成；新增临时目录隔离的契约测试                                                                                  | `services/sales-api/test/api-contract.test.mjs`、本报告                                                            |
| 前端按业务拆分                         | 已完成；页面、共享组件、格式化、API client、类型分别归位                                                            | `apps/admin-web/src/App.tsx`、`api/`、`components/`、`features/`、`lib/`、`types.ts`                               |
| 后端 routes/services/repositories 拆分 | 已完成客户、充值、报表、设置、升级领域拆分；认证会话及组合根仍在 `app.ts`                                           | `services/sales-api/src/routes/`、`src/services/`、`src/repositories/`、`src/infrastructure/`                      |
| 数据结构迁移设计                       | 已完成设计及未执行脚本；真实数据源保持不变                                                                          | `docs/migrations/001_sales_normalized_schema.sql`、`002_backfill_sales_json_store.sql`                             |
| 导航调整                               | 已提交映射和页面方案；未隐藏/删除原入口                                                                             | 本报告第 5 节                                                                                                      |
| 交易一致性与并发测试                   | 已完成 JSONB 集合行锁事务、充值操作原子提交、可恢复幂等记录和独立 PostgreSQL 多进程测试；关系表唯一约束仍待独立迁移 | `services/sales-api/src/store.ts`、`src/services/recharge-service.ts`、`test/postgres-concurrency.integration.mjs` |

## 9. 剩余技术债务

- `app.ts` 仍包含登录、会话、公钥、远程连接验证、dashboard 和员工密码端点；认证 Session Map 仍为进程内状态。
- 前后端都需要一次 snake/camel 和字段别名转换；当前已各自集中，但仍存在重复转换。要移除转换，必须先对每个 canonical 响应建立字段级契约测试并确认内部视图模型。
- PostgreSQL JSONB 集合行锁已保护应用当前的多实例充值/报告读改写，但一个集合仍是一个锁粒度；数据库尚无独立订单状态条件更新、幂等唯一索引或消费 ACK 唯一约束。
- JSON 文件开发模式的事务只保证单进程串行，不适用于多 API 进程共享同一文件目录；涉及多个集合的文件写入仍无法抵御写入中断造成的部分提交。
- 上传仍占用内存；批量升级仍同步等待客户 VM 响应且没有持久任务队列。
- 密钥托管/轮换、细粒度员工权限、TLS、备份恢复演练、上传资源限额策略仍待生产化评审。
- `/api/*` 旧接口没有调用方清单，不能在本次判断为可删除。

## 10. 回滚方案

1. **前端拆分：** 回滚 `App.tsx` 与新增 `api/`、`components/`、`features/`、`lib/`、`types.ts` 文件作为同一代码变更；页面功能和 API 未改，回滚不会涉及数据。
2. **后端模块化：** 路由和 service/repository 变更可按领域一起回滚到 `app.ts` 原实现；兼容转换、字段和 API 路径未删除。数据库事务只使用现有 JSONB 行，不要求任何生产 DDL。
3. **SQL：** 本次没有在客户数据库执行迁移。已验证脚本只新增结构和复制数据，不包含 `DROP`、`TRUNCATE`、删除源行或密钥重置。若未来切换后需回滚，先把读取/写入开关切回 `sales_console_json_store`，保留新表和源快照；回滚窗口结束且备份恢复演练通过后再单独评审清理。
4. **生产部署：** 本次没有构建/部署销售容器、重启 API、清空数据库、删除发布文件或替换签名密钥。

## 11. 第二阶段后端模块化与交易一致性

### 11.1 当前目录与依赖方向

```text
src/
├─ app.ts                         # Express 组合根、共享中间件、登录/会话
├─ contracts/legacy-compat.ts     # canonical ↔ 旧 API 适配
├─ routes/                        # customers / recharge / reports / settings / upgrades
├─ services/                      # 各领域业务操作，不接收 Express Request/Response
├─ repositories/                  # SalesRepository、发布包元数据存取
├─ infrastructure/                # tar 包路径、manifest 与文件校验
├─ business.ts / contract.ts      # 共享业务与字段转换
└─ store.ts                       # JSON 与 PostgreSQL JSONB 持久化、事务边界
```

依赖由 `app.ts` 组装 routes；routes 调用 services；services 面向 repository 接口；repository 再适配现有 store。充值码签名由业务/密钥设施提供，升级 tar 校验与领域流程分开。鉴权、请求 ID、参数适配和错误格式仍在共享应用边界执行，页面/领域路由不重复实现。

本阶段新增或调整的后端文件包括 `routes/{customers,recharge,reports,settings,upgrades}.ts`、`services/{customer-service,recharge-service,report-service,settings-service,upgrade-service}.ts`、`repositories/{sales-repository,release-repository}.ts`、`infrastructure/release-archive.ts`，以及 `store.ts`、`app.ts` 和 API/并发测试。目录拆分对应领域操作、持久化边界和归档校验，不改变各模块外部路径。

### 11.2 交易事务与并发处理

`transactJson()` 在 PostgreSQL 下先按存储键固定排序，再对声明的集合行执行 `SELECT ... FOR UPDATE`；事务提交前写回变更集合。此处仍使用现有 `sales_console_json_store`，没有创建或迁移生产表。JSON 文件路径使用进程内 `serial()` 回退。

充值签发、在线充值提交、审批、拒绝、重签、轮询交付状态和 ACK 通过领域 service 访问 repository。审批在同一事务中读取最新订单/客户/汇率，并仅在订单仍为 `pending` 时转为 `approved`，同时写充值码、累计充值和幂等操作 ID。相同订单并发审批会在锁释放后读取已结算状态，不再重复增加客户累计额。离线充值根据 request ID 查重并在同一事务中创建订单、增加累计额。消费报告和客户最近上报状态也在同一事务写入。

canonical 幂等中间件先保留原 `statusCode=0` 记录。充值 service 在业务提交事务中绑定稳定订单 ID；如果进程在业务提交后、缓存 HTTP 响应前退出，重试可在集合锁下读回原订单并重新生成响应。允许自动恢复的路径限定为在线提交、离线签发/重签、审批/拒绝及 ACK；其他接口仍使用原 `idempotency_in_progress` 行为。测试覆盖本地 JSON API 并发以及 PostgreSQL 中多个独立 Node 进程。

关系表切换后的交易事务方案是：先按 `(scope, idempotency_key)` 插入幂等记录，冲突时比较 request hash；相同请求读取已完成响应或等待状态，内容不同返回 409。审批在单个事务中 `SELECT ... FOR UPDATE` 锁定订单，使用 `UPDATE ... WHERE order_id = $1 AND status = 'pending' RETURNING ...` 做状态条件转换，成功后写客户累计额、签发码和幂等 `operation_id` 并提交；更新行数为 0 时读取当前状态并按现有语义返回同状态结果或 409。数据库唯一冲突映射到既有幂等冲突响应；死锁/序列化失败应做有界重试并保留 request ID。拒绝也使用相同状态条件，ACK 的 redemption/ledger 唯一约束需和客户侧账本语义确认后启用。

该方案是现有 JSONB 架构上的过渡并发保护，不是规范化交易表的最终形态。当前没有数据库级 `(scope, key)` 唯一约束、订单独立行的 `UPDATE ... WHERE status='pending'`、或 redemption/ledger 唯一约束；所有同集合写操作会争用同一 JSONB 行。下一步应基于第 6 节迁移草案在隔离 schema 实现条件更新和唯一索引，跑冲突/回滚验证，观察后再安排独立迁移评审。

### 11.3 API、测试与行为边界

本阶段没有删除或新增对外 API，也没有更改 `/api/v1` 兼容映射、旧 `/api/*` handler URL、Cookie/Bearer 认证、签名密钥、公钥格式、充值码字段、升级 manifest/SHA-256 或客户升级消息。旧 API 兼容矩阵仍按第 4 节执行。

新增 `services/sales-api/test/postgres-concurrency.integration.mjs` 与 fixture worker，使用专门的 `SALES_TEST_DATABASE_URL`；本次用临时 PostgreSQL 16 容器执行，成功后容器移除。没有连接 `sales-console-postgres-1`，没有改动生产/开发共享容器、数据库或密钥。常规 API 契约测试还模拟已提交充值但 HTTP 回包未缓存的重试。

端到端客户虚拟机没有参与升级包上传/升级测试；因为其外部重启时序、上传资源峰值与 60 秒轮询仍需在模拟客户上验证，升级服务虽已模块化，但未改变同步执行和 300 MiB 内存上传行为。
