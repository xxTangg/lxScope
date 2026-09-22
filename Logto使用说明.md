# lxScope Logto 使用说明

本文说明如何让其他人拿到 lxScope 代码后，使用自己的 Logto 实例完成部署。

## 最少需要手动提供什么

正常情况下只需要准备下面 2 类信息：

| 需要提供的内容 | 在哪里获得 |
| --- | --- |
| M2M App ID | Logto `Applications` 中创建的 `Machine-to-machine` 应用详情页 |
| M2M App Secret | 同一个 M2M 应用详情页，点击 Secret 的复制按钮 |

M2M 应用还必须在 Logto 中绑定 `Logto Management API access` 角色。这个绑定需要在 Logto 控制台完成一次，因为脚本必须先拥有一个管理身份，才能调用 Logto API。

其他配置都有默认值：

- Logto 地址：`http://localhost:3001`；
- lxScope API Resource：`https://api.lxscope.local`；
- 前端地址：`http://localhost:8000`；
- SPA 应用：脚本可以自动创建 `lxScope Web`，也可以使用已有应用。

Windows 可以直接执行一键入口：

```powershell
.\scripts\setup-logto.ps1
```

脚本会询问或读取 M2M App ID 和 Secret，自动执行迁移，并把最终的 SPA App ID 写入：

```text
.env
examples/web_ui/frontend/.env.local
```

如果要同时创建演示数据，执行：

```powershell
.\scripts\setup-logto.ps1 -SeedDemo
```

此时额外输入两个演示用户密码即可。演示数据包括 1 个 `demo` 租户、1 个管理员和 1 个普通成员。

## 一、先理解两个 Logto 应用

部署时需要区分两个应用：

| 应用 | 用途 | 配置位置 |
| --- | --- | --- |
| `lxScope Web` / Single Page App | 普通用户在浏览器中登录 lxScope | `VITE_LOGTO_APP_ID` |
| `lxScope Logto Migration` / Machine-to-machine | 迁移脚本调用 Logto Management API | `LOGTO_M2M_APP_ID`、`LOGTO_M2M_APP_SECRET` |

SPA 应用用于“用户登录”；M2M 应用用于“脚本管理 Logto”。两者不能混用。

## 二、准备 Logto

### 1. 启动或准备 Logto

可以使用项目自带的 Logto，也可以使用已经运行的 Logto 实例。确认浏览器能够打开：

```text
http://localhost:3001
```

如果 Logto 在服务器上，后续配置中的地址替换成服务器实际地址。

### 2. 创建 M2M 应用

在 Logto Admin Console 中进入：

```text
Applications
→ Create application
→ Machine-to-machine
```

应用名称可以填写：

```text
lxScope Logto Migration
```

创建后记录：

- `App ID`；
- `App Secret`。

`App Secret` 只用于服务端脚本，不能放进前端，也不能提交 Git。

### 3. 给 M2M 应用分配管理权限

进入：

```text
Roles
→ Logto Management API access
→ Machine-to-machine apps
→ Assign machine-to-machine apps
```

选择刚刚创建的 `lxScope Logto Migration`。

在 `Permissions` 页面确认存在：

```text
all
```

如果没有绑定这个角色，迁移脚本会出现：

```text
Logto API GET /resources failed (403): Forbidden
```

### 4. 查看 Management API Resource

进入：

```text
API resources
→ Logto Management API
```

复制它的 `Indicator`。常见值类似：

```text
https://default.logto.app/api
```

这里要填写 `Indicator`，不是角色 ID，也不是 M2M App ID。

## 三、准备浏览器登录应用

### 方式 A：已有 `lxScope Web` 应用

如果 Logto 中已经有：

```text
lxScope Web
Single Page App
```

进入详情页，复制它的 `App ID`，这个值用于：

```env
VITE_LOGTO_APP_ID=你的SPA应用ID
```

### 方式 B：没有 SPA 应用

可以先手动创建一个 `Single Page App`，也可以让迁移脚本根据 `migration.yaml` 自动创建。

### 配置回调地址

回调地址必须与实际访问地址完全一致。

热重载开发，例如前端运行在 `18200` 端口：

```text
http://localhost:18200/callback
http://localhost:18200
```

Docker 或其他方式运行在 `8000` 端口：

```text
http://localhost:8000/callback
http://localhost:8000
```

如果通过服务器 IP 或域名访问，还要添加对应地址，例如：

```text
https://lxscope.example.com/callback
https://lxscope.example.com
```

不要把 `localhost` 回调地址用于其他机器访问的正式环境。

## 四、配置项目环境变量

在项目根目录创建 `.env`。可以从 `.env.example` 复制：

```powershell
Copy-Item .env.example .env
```

至少填写以下内容：

```env
# Logto 地址
LOGTO_ENDPOINT=http://localhost:3001

# M2M 应用，仅迁移脚本使用
LOGTO_M2M_APP_ID=你的M2M应用ID
LOGTO_M2M_APP_SECRET=你的M2M应用Secret
LOGTO_MANAGEMENT_API_RESOURCE=https://default.logto.app/api

# lxScope API Resource
LOGTO_API_RESOURCE=https://api.lxscope.local

# 前端公开地址，用于登录页品牌资源和回调地址生成
LXSCOPE_PUBLIC_URL=http://localhost:8000
```

其中：

| 变量 | 来源 |
| --- | --- |
| `LOGTO_M2M_APP_ID` | M2M 应用详情页的 App ID |
| `LOGTO_M2M_APP_SECRET` | M2M 应用详情页的 App Secret |
| `LOGTO_MANAGEMENT_API_RESOURCE` | `Logto Management API` 的 Indicator |
| `LOGTO_API_RESOURCE` | lxScope API Resource 的 indicator |
| `LXSCOPE_PUBLIC_URL` | 用户实际打开 lxScope 的地址 |

不要把 `LOGTO_M2M_APP_SECRET` 提交到 Git。

## 五、热重载前端配置

本项目的热重载前端目录是：

```text
examples/web_ui/frontend
```

创建：

```text
examples/web_ui/frontend/.env.local
```

填写：

```env
VITE_AUTH_PROVIDER=logto
VITE_LOGTO_ENDPOINT=http://localhost:3001
VITE_LOGTO_APP_ID=你的SPA应用ID
VITE_LOGTO_API_RESOURCE=https://api.lxscope.local
```

`.env.local` 不应该提交到 Git。修改后需要重启 Vite 开发服务器，环境变量才会生效。

启动前端：

```powershell
cd examples/web_ui
pnpm dev:frontend
```

如果前端运行在 `18200` 端口，Logto SPA 应用中必须存在：

```text
http://localhost:18200/callback
```

## 六、执行 Logto 迁移

### Windows PowerShell

先确认项目依赖中有 PyYAML：

```powershell
python -m pip install PyYAML
```

检查配置：

```powershell
.\scripts\migrate-logto.ps1 validate
```

执行迁移：

```powershell
.\scripts\migrate-logto.ps1 apply
```

### Linux、macOS、Git Bash 或 WSL

```bash
bash scripts/migrate-logto.sh validate
bash scripts/migrate-logto.sh apply
```

`validate` 只检查本地清单和环境变量；`apply` 才会访问 Logto Management API。

成功后会生成：

```text
deploy/logto/generated/
├── migration-result.json
├── migration.env
└── tenant-bindings.sql
```

其中：

- `migration-result.json`：本次匹配或创建的 Logto ID；
- `migration.env`：浏览器应用 ID 等非秘密变量；
- `tenant-bindings.sql`：Logto Organization 到 lxScope 租户的绑定 SQL。

## 七、迁移 Organization、用户和角色

普通执行 `apply` 时，配置中的业务组织列表是空的：

```yaml
organizations: []
```

因此普通迁移只会迁移静态权限模型和浏览器应用，不会创建业务租户、用户。

如果需要快速创建一套演示数据，可以使用内置的 demo seed：

- 1 个租户：`demo`；
- 1 个 `admin` 用户；
- 1 个 `member` 用户。

先设置两个用户的初始密码：

```powershell
$env:LOGTO_DEMO_ADMIN_PASSWORD="管理员初始密码"
$env:LOGTO_DEMO_MEMBER_PASSWORD="成员初始密码"
```

然后执行：

```powershell
.\scripts\migrate-logto.ps1 apply --seed-demo
```

Linux、macOS、Git Bash 或 WSL：

```bash
export LOGTO_DEMO_ADMIN_PASSWORD='管理员初始密码'
export LOGTO_DEMO_MEMBER_PASSWORD='成员初始密码'
bash scripts/migrate-logto.sh apply --seed-demo
```

密码不会写入代码或迁移清单。`--seed-demo` 只会创建或匹配这套演示租户和用户，重复执行不会重复创建。

需要创建自己的业务租户时，编辑：

```text
deploy/logto/migration.yaml
```

示例：

```yaml
organizations:
  - code: demo
    name: Demo tenant
    description: Demo organization
    users:
      - username: admin
        primary_email: admin@example.com
        name: Demo administrator
        password_env: LOGTO_DEMO_ADMIN_PASSWORD
        role: admin
```

执行前设置新用户密码：

```powershell
$env:LOGTO_DEMO_ADMIN_PASSWORD="请设置一个初始密码"
.\scripts\migrate-logto.ps1 apply
```

脚本会自动：

1. 创建或匹配 Organization；
2. 创建或匹配用户；
3. 将用户加入 Organization；
4. 分配 `admin` 或 `member` 角色；
5. 生成 lxScope 租户绑定 SQL。

密码不会写入 YAML，也不会被 `export` 导出。

## 八、应用租户绑定 SQL

如果创建了 Organization，需要先确保应用数据库已经执行：

```text
examples/agent_service/migrations/0004_logto_tenant_binding.sql
```

然后应用生成的：

```text
deploy/logto/generated/tenant-bindings.sql
```

使用 Docker PostgreSQL 时可以执行：

```powershell
Get-Content .\deploy\logto\generated\tenant-bindings.sql |
  docker compose exec -T postgres psql -U lxscope -d lxscope
```

如果 `organizations` 为空，生成的 SQL 不包含业务租户绑定，不需要执行。

## 九、从旧 Logto 导出

在旧 Logto 实例对应的项目环境中执行：

```powershell
.\scripts\migrate-logto.ps1 export --output-dir deploy/logto/generated-old
```

或：

```bash
bash scripts/migrate-logto.sh export --output-dir deploy/logto/generated-old
```

导出结果中的：

```text
deploy/logto/generated-old/migration-manifest.json
```

可以作为新实例的迁移清单参考。

出于安全原因，导出不会包含用户密码或密码摘要。目标 Logto 中不存在的用户，需要补充 `password_env` 后再执行导入。

## 十、常见问题

### 1. `GET /resources failed (403)`

说明 M2M 应用没有得到 Management API 权限。检查：

```text
Roles
→ Logto Management API access
→ Machine-to-machine apps
```

确认当前 M2M 应用已经在列表中，并且角色中有 `all` 权限。

### 2. `PATCH /sign-in-exp failed (400)`

Logto OSS 不支持隐藏 Logto 品牌的配置。当前迁移脚本会自动跳过不兼容的 `hideLogtoBranding` 字段，保留其他品牌配置。

### 3. `Bash/Service/0x8007274c`

这是 Windows 的 Bash/WSL 启动问题，不是 Logto 问题。改用：

```powershell
.\scripts\migrate-logto.ps1 validate
```

### 4. 登录提示 redirect URI 不匹配

Logto 中配置的回调地址必须和浏览器地址栏完全一致，包括：

- `http` / `https`；
- 域名或 IP；
- 端口；
- `/callback` 路径。

### 5. 登录后提示租户未配置

说明 Logto Organization 已存在，但 lxScope 数据库中还没有对应的 `external_org_id` 绑定。重新执行 Organization 迁移，并应用生成的 `tenant-bindings.sql`。

## 十一、安全要求

- 不要提交项目根目录 `.env`；
- 不要提交 `examples/web_ui/frontend/.env.local`；
- 不要把 `LOGTO_M2M_APP_SECRET` 填进前端变量；
- 每个部署环境使用自己的 Logto、M2M 应用和 Secret；
- M2M 应用只用于迁移或管理任务，不用于普通用户登录。
