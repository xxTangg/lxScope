# 龙信销售运营中心（新一代）

这是独立建设的新销售运营中心。项目自身负责客户管理、在线充值申请、离线充值码、额度上报、连接验证和集中升级；不依赖历史销售总台的代码、数据库、进程或部署目录。前端采用 AgentScope 项目当前使用的 React + TypeScript + Vite 工具链，后端采用 TypeScript + Express 分层实现。

## 项目地址

| 内容                          | 地址                                |
| ----------------------------- | ----------------------------------- |
| 新项目                        | `sales-console/`                    |
| lxScope / AgentScope 参考实现 | `src/`、`examples/web_ui/frontend/` |
| 客户系统接入边界              | 通过新项目定义的 Bearer API 接入    |

## 技术基线

- Node.js：20.x（`>=20.0.0 <21`）
- 包管理器：pnpm 9.15.9，已写入根目录 `package.json` 的 `packageManager`（AgentScope 当前采用 pnpm，不采用 npm 作为安装入口）
- 前端：React 19、TypeScript 6、Vite 8、ESLint 10、Prettier 3
- 后端：TypeScript、Express、Multer、node-tar
- 数据存储：Docker 部署默认使用 PostgreSQL；发布包和签名密钥继续使用独立数据卷保存

## 本地运行

```powershell
cd sales-console
corepack enable
pnpm install
pnpm dev:api
```

另开一个终端：

```powershell
cd sales-console
pnpm dev
```

Docker 部署前先复制 `.env.example` 为 `.env`，替换管理员密码和 PostgreSQL 密码（并同步更新 `DATABASE_URL`），再执行 `docker compose up -d --build`。生产环境不要把 `.env` 提交到 Git。

- API：`http://127.0.0.1:44100`
- Web：`http://127.0.0.1:44101`
- 健康检查：`http://127.0.0.1:44100/health`

首次启动会自动创建 PostgreSQL 数据表、数据卷、签名密钥和管理员账号。开发环境默认账号为 `admin`，默认密码为 `change-me`；生产环境必须设置 `SALES_ADMIN_PASSWORD`、`POSTGRES_PASSWORD` 和 `DATABASE_URL`，并通过环境变量提供稳定的 `SALES_DATA_DIR`。

## 目录

```text
sales-console/
├─ apps/admin-web/       # AgentScope 风格的销售运营前端
├─ services/sales-api/   # 销售总台 API、鉴权、业务规则和升级编排
└─ docs/                 # 地址、业务规则、数据模型、接口清单、迁移计划
```

## 文档入口

- `docs/PROJECT-ADDRESS.md`：项目、端口、运行入口和部署边界
- `docs/新手讲解手册.md`：面向非技术人员的系统说明和讲解稿
- `docs/BUSINESS-RULES.md`：销售运营中心业务规则和普通用户/管理员影响链路
- `docs/API-CHECKLIST.md`：新项目需要联调和验收的接口清单
- `docs/API-CONTRACT.yaml`：接口契约草案，可作为 OpenAPI 进一步生成的输入
- `docs/管理系统对接资料与开发清单.md`：发送给管理系统开发人员的资料和接口对接清单
- `docs/FUNCTION-COVERAGE.md`：新销售运营中心功能清单
- `docs/DATA-MODEL.md`：客户、订单、报告、发布包和审计数据模型
- `docs/MIGRATION-PLAN.md`：从当前存储方案升级到生产级数据库和异步任务的步骤

## 当前实现边界

当前 Docker 部署已经接入 PostgreSQL，销售 API 的客户、订单、报表、配置、审计和幂等记录写入数据库；升级压缩包和签名密钥仍保存在 `sales_data` 数据卷。新对接方统一使用 `/api/v1` canonical 接口：JSON 为 `snake_case`，时间为 UTC ISO 8601，金额为定点字符串，状态变更必须带 `Idempotency-Key`。直接本地开发且未设置 `DATABASE_URL` 时，仍可使用 JSON 文件仓库。正式生产前还需要按迁移计划补齐队列、密钥托管、反向代理 TLS、操作员细粒度权限和升级任务持久化。
