# lxScope Logto 配置说明（当前代码）

本文对应仓库当前的 AgentScope / lxScope 主应用：Docker Compose 启动，浏览器前端使用 Logto SPA 登录，后端校验带组织上下文的 API Access Token。

## 先看结论

当前代码会读取 Logto 登录令牌、检查组织身份和权限，并按组织隔离用户数据。应用启动时不会自动创建 Logto 配置；仓库现已提供 `scripts/setup-logto.ps1`，可通过 Logto Management API 一次性创建/补齐应用所需配置、写入 `.env` 并启动 Docker Compose。

至少需要配置：

1. 一个 Logto API Resource 和两个权限：`agent:use`、`tenant:manage`。
2. 一个 Logto SPA 应用，以及与实际访问地址匹配的回调地址。
3. 一个 Organization，且登录用户已加入该组织并获得对应角色。
4. 根目录 `.env` 中的 Logto 地址、SPA App ID 和 API Resource。

## 一键配置并启动（推荐）

交给其他人部署时，对方不需要逐个创建 API 权限、组织角色、SPA 应用或手动给初始用户分配角色。首次仍需在自己的 Logto 管理台做一次安全引导：创建一个 **M2M 应用**，并给它分配 Logto 内置的 **Logto Management API access** 角色；Management API 使用该 M2M 应用取得授权令牌，不能在没有任何管理员授权凭据的情况下替租户创建自己的管理凭据。[Logto Management API 文档](https://docs.logto.io/integrate-logto/interact-with-management-api)

此外，先在 Logto 中创建准备作为 lxScope 初始管理员的用户。脚本会把这个现有用户加入 lxScope Organization 并分配管理员角色，不会替对方创建或设置登录密码。

之后在项目根目录打开 PowerShell，运行：

```powershell
.\scripts\setup-logto.ps1
```

按提示提供 Logto 地址、M2M App ID/Secret、初始管理员邮箱和浏览器访问地址。Secret 在输入时隐藏，只用于本次初始化，不会写进 `.env`。脚本会自动创建或补齐 API Resource 与 `agent:use`、`tenant:manage` 权限，普通成员/管理员组织角色，SPA 登录回调与退出地址、lxScope Organization、初始管理员成员和角色；然后写入项目 `.env`，执行 `docker compose up -d --build` 并打开网页。角色和配置已存在时脚本会复用并补齐，适合重新运行。

常用选项：

```powershell
# 只配置 Logto 和 .env，稍后自己启动 Docker Compose
.\scripts\setup-logto.ps1 -ConfigureOnly
```

使用 Logto Cloud 自定义域名时，脚本会额外询问 Management API 地址；应填该租户默认的 `*.logto.app` 地址。Logto OSS 若跑在同一台 Docker Desktop 主机上，脚本会询问容器可访问的 Logto 地址，并默认建议 `host.docker.internal`。Linux 或自定义网络部署时，请在该提示中填写容器实际可访问的地址。若其他电脑会通过局域网访问，在浏览器访问地址提示中填写服务器 IP，例如 `http://192.168.1.20:8000`。

脚本自动设置的是**第一个初始管理员**。新用户需要先在 Logto 注册/创建；如果组织启用了适用的 JIT 自动加入，新用户会获得普通成员角色，否则仍需由 Logto 管理员把用户加入 Organization。脚本不迁移 Logto 用户库或 lxScope 业务数据，也不创建模型服务 API Key；使用 AI 功能前仍需在 `.env` 填好相应模型提供商的密钥，例如 `SILICONFLOW_API_KEY`。

## 一、Logto 中配置 API Resource 和权限

在 Logto Console 打开 **API resources**，创建 lxScope API。Indicator 是应用自定义的资源标识，可例如：

```text
https://api.lxscope.local
```

在该 Resource 下添加两个 API 权限（Scopes）：

| Scope | 用途 |
| --- | --- |
| `agent:use` | 允许组织成员使用 lxScope |
| `tenant:manage` | 将该组织成员识别为 lxScope 管理员 |

Indicator 可按部署自行命名，但 Logto Console、前端和后端中的值必须完全一致。

## 二、创建组织角色并分配权限

在 **Organization template → Organization roles** 创建两个 **User** 类型角色：

| 建议角色名 | 分配给角色的 lxScope API 权限 | 在 lxScope 中的效果 |
| --- | --- | --- |
| `lxscope_member` | `agent:use` | 普通成员，可使用 lxScope |
| `lxscope_admin` | `agent:use`、`tenant:manage` | 组织管理员，可使用管理员功能 |

角色名只是便于识别；当前后端依据 Access Token 的 Scope 判断权限：包含 `agent:use` 才能访问，包含 `tenant:manage` 才会被识别为管理员。不要把 `tenant:manage` 分配给普通成员。

之后创建一个 Organization，并把用户加入其中，再为每位用户分配 `lxscope_member` 或 `lxscope_admin`。lxScope 的登录页会显示该用户加入的组织供其选择；没有加入任何 Organization 的账号无法进入系统。

## 三、创建前端 SPA 应用

在 **Applications → Create application → Single-page application** 创建 lxScope Web 应用。把下列 URI 加到应用的 Redirect URIs 和 Post sign-out redirect URIs：

本机使用默认端口时：

```text
Redirect URI:              http://localhost:8000/auth/callback
Post sign-out redirect URI: http://localhost:8000
```

如果用户通过局域网 IP 或域名访问，还要登记实际地址，例如：

```text
http://192.168.1.20:8000/auth/callback
http://192.168.1.20:8000
```

回调地址必须和浏览器地址栏的协议、主机名、端口完全一致。`localhost` 只适用于运行浏览器的本机；其他电脑访问时应登记服务器 IP 或域名。公网部署建议使用 HTTPS。

记录 SPA 应用的 **App ID**。前端会请求组织信息，以及 `agent:use` 和 `tenant:manage` 权限。

## 四、配置项目 `.env`

从仓库根目录的 `.env.example` 复制配置：

```powershell
Copy-Item .env.example .env
```

至少填写以下 Logto 项：

```env
LXSCOPE_AUTH_PROVIDER=logto

# 后端验证令牌使用的 Logto 地址
LOGTO_ENDPOINT=https://<你的-logto-tenant>.logto.app

# API Resource Indicator，需与 Logto 中创建的值完全相同
LOGTO_API_RESOURCE=https://api.lxscope.local
LOGTO_ACCESS_SCOPE=agent:use
LOGTO_ADMIN_SCOPE=tenant:manage

# 浏览器使用的 Logto 地址和 SPA 应用
VITE_LOGTO_ENDPOINT=https://<你的-logto-tenant>.logto.app
VITE_LOGTO_APP_ID=<SPA 应用的 App ID>
VITE_LOGTO_API_RESOURCE=https://api.lxscope.local
```

`LOGTO_ENDPOINT` 与 `VITE_LOGTO_ENDPOINT` 通常填写同一个公开地址。`LOGTO_ENDPOINT` 决定后端校验的令牌发行者（issuer），不要随意改成另一个主机名；地址填写基础 URL，不要附加 `/oidc`。

如果 Logto OSS 也运行在同一台 Docker Desktop 主机上，浏览器可访问的 `http://localhost:3001` 对后端容器来说不是宿主机。Windows/macOS Docker Desktop 可保留 `LOGTO_ENDPOINT` 和 `VITE_LOGTO_ENDPOINT` 为 `http://localhost:3001`，再将后端 JWKS 和可选 M2M 管理 API 地址指向宿主机：

```env
LOGTO_JWKS_URI=http://host.docker.internal:3001/oidc/jwks
LOGTO_INTERNAL_ENDPOINT=http://host.docker.internal:3001
```

Linux Docker Engine 部署时，将 `host.docker.internal` 换成容器实际可访问的宿主机地址，或使用同一 Compose 网络中的 Logto 服务名。无论如何配置，浏览器拿到的令牌 issuer 必须与 `LOGTO_ENDPOINT` 一致。Logto Cloud 通常不需要上述内部地址设置。

保存 `.env` 后，在仓库根目录启动：

```powershell
docker compose up -d --build
```

如果之后修改了 `VITE_LOGTO_ENDPOINT`、`VITE_LOGTO_APP_ID` 或 `VITE_LOGTO_API_RESOURCE`，需要重新构建前端镜像：

```powershell
docker compose up -d --build web-ui agentscope
```

默认前端地址为 `http://localhost:8000`，API 地址为 `http://localhost:8001`。其他电脑通过局域网访问时，需要放通这两个端口，并在 Logto SPA 应用中登记实际访问地址。端口变化时，Logto Redirect URI 和 `.env` 中 `WEB_UI_PORT`、`AGENTSCOPE_API_PORT` 也要对应调整。

## 五、首次登录

1. 打开前端地址并选择 Logto 登录。
2. 使用已加入 Organization 的 Logto 用户登录。
3. 选择 Organization。
4. 确认账号在该组织中已分配角色；普通成员至少需要 `agent:use`，管理员还需要 `tenant:manage`。

可通过以下地址检查后端认证路由是否启动：

```text
http://localhost:8001/auth/health
```

正常情况下返回 HTTP `204`。这个检查只确认后端认证路由可用，不代表 Logto 组织、角色和用户已配置正确。

## 六、应用运行时 M2M 配置（可选）

下面这组运行时 M2M 环境变量与一键脚本启动时临时输入的管理凭据是两回事。普通用户登录和角色鉴权**不需要**在 lxScope `.env` 中配置运行时 M2M；只有后端需要通过 Logto Management API 查询用户资料、补充显示名称时才配置：

```env
LOGTO_M2M_APP_ID=<M2M App ID>
LOGTO_M2M_APP_SECRET=<M2M App Secret>
LOGTO_MANAGEMENT_API_RESOURCE=https://<你的-logto-tenant>.logto.app/api
```

M2M App 需要在 Logto 中获准读取用户资料。Secret 只放在服务端 `.env`，不要填写到任何 `VITE_` 变量，也不要提交到 Git。Logto Cloud 使用自定义域名时，Management API 的 M2M 令牌请求需使用该租户默认的 `*.logto.app` 地址；此时可将默认地址填入 `LOGTO_INTERNAL_ENDPOINT`。

## 七、当前权限判定方式

前端请求包含组织上下文的 Access Token；后端会校验签名、发行者、API Resource、`organization_id` 和权限 Scope：

- 缺少 `agent:use`：拒绝访问 lxScope API。
- 有 `agent:use`、没有 `tenant:manage`：按普通成员处理。
- 同时有 `agent:use` 和 `tenant:manage`：按该组织管理员处理。

因此，“在 Logto 中有个叫 admin 的角色”本身不够；该角色必须实际授予 `tenant:manage`，并且用户取得包含该 Scope 的组织令牌。更改角色后，用户需要重新获取令牌（退出再登录或刷新授权），旧令牌中的权限不会自动变化。

## 八、常见问题

### 登录后提示用户未加入组织

在 Logto Console 确认用户已加入至少一个 Organization。仅创建用户或创建角色不会自动把用户加入组织。

### 后端返回 403，提示无 lxScope 访问权限

确认 API Resource 下存在 `agent:use`，并确认该用户在当前 Organization 内的角色已被授予它。退出登录后重新登录，再选择该 Organization。

### 能使用系统，但看不到管理员功能

确认用户在当前组织中有 `tenant:manage`，并重新获取组织 Access Token。

### 出现回调地址不匹配

核对 Logto SPA 应用中的 Redirect URI 是否逐字等于浏览器实际使用的地址加 `/auth/callback`，包括协议、主机名和端口。

### 没有 Logto 时只想本机验证

可将 `.env` 中 `LXSCOPE_AUTH_PROVIDER` 设为 `local`，并清空三个 `VITE_LOGTO_*` 配置，使用 `AGENTSCOPE_USERNAME` 和 `AGENTSCOPE_PASSWORD` 的本地账号模式。正式接入 Logto 时再恢复 Logto 配置并重新构建容器。

## 参考

- [Logto：组织级 API Resource 授权](https://docs.logto.io/authorization/organization-level-api-resources)
- [Logto：RBAC 角色与权限](https://docs.logto.io/authorization/role-based-access-control)
- [Logto：Machine-to-machine 与 Management API](https://docs.logto.io/integrate-logto/interact-with-management-api)
