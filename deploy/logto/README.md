# lxScope Logto Bootstrap

这个目录把 lxScope 当前使用的 Logto 授权模型固化成可重复执行的配置：

- API Resource：`lxScope API` / `https://api.lxscope.local`
- API Resource permissions：`agent:use`、`resource:manage`、`member:manage`、`tenant:manage`、`resource:read`、`platform:manage`、`platform:upgrade`、`platform:integration`、`platform:observe`
- Organization roles：`admin`、`platform_admin`、`member`
- 角色与 API Resource permissions 的绑定
- Logto 官方 Sign-in Experience 品牌配置（Logo、品牌色、中文、应用名称和自定义 CSS）

`config.yaml` 对应现有 Console 配置。API Resource、permission、organization role 及其绑定仍然只会补齐缺失项；`sign_in_experience` 则会通过 Logto 官方 Management API 更新托管登录页品牌配置。脚本不会删除用户、租户、角色或权限。

Logto 的 Organization Template 是全局共享模板，当前 Management API 通过 `organization-roles` 管理其中的角色；因此这里没有额外创建一个名为 “Organization Template” 的实体。

## 一次性准备

先在 Logto 中创建一个 Machine-to-machine application，并给它分配内置的 `Logto Management API access` 角色（包含 Management API 的 `all` permission）。然后把下面的值写入项目根目录 `.env`：

```dotenv
LOGTO_ENDPOINT=http://localhost:3001
LOGTO_M2M_APP_ID=your-m2m-app-id
LOGTO_M2M_APP_SECRET=your-m2m-app-secret
LOGTO_MANAGEMENT_API_RESOURCE=https://default.logto.app/api
```

对于 Logto OSS，Management API resource 通常使用 `https://default.logto.app/api`；以当前 Logto 实例 API Resource 列表中显示的 indicator 为准。

Bootstrap 不会创建或修改浏览器登录应用。继续使用现有登录配置，并将后端与前端的 API resource 指向同一个 identifier：

```dotenv
LXSCOPE_AUTH_PROVIDER=logto
LOGTO_API_RESOURCE=https://api.lxscope.local
VITE_AUTH_PROVIDER=logto
VITE_LOGTO_API_RESOURCE=https://api.lxscope.local
LXSCOPE_PUBLIC_URL=https://lxscope.example.com
```

`VITE_LOGTO_APP_ID` 仍然填写现有浏览器应用的 App ID；`LXSCOPE_PUBLIC_URL` 必须是浏览器可访问的 lxScope 前端地址，Logto 会从这里加载 `/lxscope-logo.svg`。`LOGTO_M2M_APP_ID` / `LOGTO_M2M_APP_SECRET` 只给初始化脚本使用。

Python 运行环境需要包含项目依赖（其中包括 `PyYAML`）。例如：

```bash
python -m pip install -e ".[service]"
```

## 使用

```bash
docker compose up
bash scripts/init-logto.sh
```

脚本会先通过 M2M client credentials 获取 Management API token，然后依次确保 API Resource、permissions、organization roles 和 role-permission bindings 存在，并更新 Sign-in Experience 品牌配置。成功时会输出：

```text
=====================
lxScope Logto Bootstrap

[OK] API Resource
[OK] Permission agent:use
[OK] Permission resource:manage
[OK] Permission member:manage
[OK] Permission tenant:manage
[OK] Permission resource:read
[OK] Organization Role admin
[OK] Organization Role member

Completed.

=====================
```

本次初始化不修改 lxScope 的认证流程：浏览器仍然从 Logto 获取带 organization context 的 JWT，后端仍然使用现有 JWKS 校验、`organization_id` / `scope` / `organization_roles` 解析、tenant identity binding 和 permission checks。

## 完整迁移脚本

如果目标是迁移一套已有的 Logto 配置，使用新的迁移入口：

```bash
bash scripts/migrate-logto.sh validate
bash scripts/migrate-logto.sh apply
```

Windows PowerShell 使用：

```powershell
.\scripts\migrate-logto.ps1 validate
.\scripts\migrate-logto.ps1 apply
```

`apply` 在现有 bootstrap 的基础上，还会按 `migration.yaml` 创建或匹配浏览器 SPA、Organizations、用户和成员角色，并生成：

- `deploy/logto/generated/migration-result.json`：本次匹配到的 Logto ID；
- `deploy/logto/generated/migration.env`：需要回填到 `.env` 的非秘密变量；
- `deploy/logto/generated/tenant-bindings.sql`：Logto Organization 到 lxScope 租户的绑定 SQL。

从旧 Logto 导出迁移清单：

```bash
bash scripts/migrate-logto.sh export --output-dir deploy/logto/generated-old
```

导出不会包含用户密码或密码摘要。目标实例不存在的用户需要在清单中添加 `password_env`，并在执行时设置对应环境变量。默认 `migration.yaml` 不创建业务 Organization；需要迁移租户时再填写 `organizations`。
