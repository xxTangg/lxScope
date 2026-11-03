# AgentScope 服务器部署与访问指南

本文适用于仓库中的 `docker-compose.yml`，用于在 Linux 服务器上部署
AgentScope 后端、Web UI 和 Redis。

## 先回答：部署成功后用户访问什么地址？

默认情况下，用户在浏览器打开 Web UI 地址：

```text
http://<服务器公网 IP 或内网 IP>:8000
```

其中 `8000` 是 `WEB_UI_PORT` 的默认值。如果 `.env` 中修改了
`WEB_UI_PORT`，用户访问修改后的端口，例如：

```text
http://192.168.1.20:8080
```

当前版本的 Web UI 和 API 是两个容器，浏览器还必须能够访问 API 端口：

```text
http://<服务器 IP 或域名>:8001
```

`8001` 是 `AGENTSCOPE_API_PORT` 的默认值。用户不需要直接在浏览器中打开
API 地址，但 Web UI 会从用户浏览器向该地址发起请求，所以只开放 `8000`
而不开放 `8001` 会导致页面能打开、聊天和其他功能却无法使用。

注意：`localhost` 或 `127.0.0.1` 指的是当前浏览器所在的电脑。服务器部署后，
用户不能访问 `http://localhost:8000`，除非浏览器就在服务器上；用户应使用服务器
的公网 IP、内网 IP 或域名。

## 1. 服务和端口

| 服务 | 容器端口 | 服务器端口 | 用途 |
| --- | ---: | ---: | --- |
| `web-ui` | 80 | `WEB_UI_PORT`，默认 `8000` | 用户访问的前端页面 |
| `agentscope` | 8000 | `AGENTSCOPE_API_PORT`，默认 `8001` | Web UI 调用的后端 API |
| `redis` | 6379 | 不映射到宿主机 | 后端存储，只允许 Compose 内部访问 |

默认访问链路如下：

```text
用户浏览器
   ├── http://服务器:8000  ──> web-ui
   └── http://服务器:8001  ──> agentscope API ──> redis
```

## 2. 服务器要求

- Linux 服务器，已安装 Docker Engine 和 Docker Compose v2
- 用户网络能够访问 Web UI 端口和 API 端口
- 服务器能够访问配置的模型 API；离线内网环境需要确保模型 API 在内网可达
- 至少准备一个可用的 OpenAI-compatible 模型 API Key

确认 Docker Compose 版本：

```bash
docker --version
docker compose version
```

## 3. 准备部署文件

### 3.1 在线构建部署

如果服务器可以访问代码仓库和镜像构建所需的网络资源：

```bash
git clone <项目仓库地址> agentscope
cd agentscope
cp .env.example .env
```

### 3.2 离线部署

在可联网且安装了 Docker 的机器上构建离线包：

```powershell
.\scripts\package_offline.ps1
```

脚本会在 `dist/agentscope-offline-时间戳/` 生成：

- `agentscope-images.tar`：AgentScope 后端、Web UI 和 Redis 镜像
- `docker-compose.yml`
- `.env.example`
- `OPS_DEPLOYMENT.md`
- `manifest.json`：镜像包 SHA256

将整个目录复制到服务器后执行：

```bash
docker load -i agentscope-images.tar
cp .env.example .env
```

离线启动时使用 `--no-build`，否则 Compose 可能尝试重新构建镜像：

```bash
docker compose up -d --no-build
```

## 4. 配置 `.env`

服务器上至少检查和修改以下配置：

```env
# 用户浏览器访问的 Web UI 端口
WEB_UI_PORT=8000

# 浏览器调用 AgentScope API 的端口
AGENTSCOPE_API_PORT=8001

# 默认登录账号。user_id 是后端数据隔离使用的稳定标识
AGENTSCOPE_USERNAME=admin
AGENTSCOPE_USER_ID=local-user
AGENTSCOPE_PASSWORD=请设置登录密码

# JWT 签名密钥，至少 32 字节；非本地部署必须替换
AGENTSCOPE_JWT_SECRET=请替换为至少32字节的随机长字符串
AGENTSCOPE_JWT_EXPIRE_MINUTES=1440

# 非本地部署必须替换为随机长字符串
AGENTSCOPE_DOWNLOAD_SECRET=请替换为随机长字符串

# 可选，但生产环境建议设置
REDIS_PASSWORD=请设置 Redis 密码

# 示例：SiliconFlow OpenAI-compatible API
SILICONFLOW_API_KEY=你的 API Key
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_CHAT_MODEL=deepseek-ai/DeepSeek-V3.2
SILICONFLOW_EMBEDDING_MODEL=BAAI/bge-m3
SILICONFLOW_EMBEDDING_DIMENSIONS=1024
```

`AGENTSCOPE_API_PORT` 会在构建 Web UI 时写入前端。如果修改了这个端口，
必须重新构建 `web-ui` 镜像：

```bash
docker compose up -d --build
```

只修改 `.env` 后执行 `docker compose restart` 不会更新已经构建好的前端配置。
离线部署时，必须在打包前使用目标 API 端口构建镜像；`--no-build` 不会重新写入
前端配置。

默认配置创建一个账号。若需要多个用户，可在 `.env` 中设置
`AGENTSCOPE_AUTH_USERS`，每个账号必须使用不同的 `user_id`：

```env
AGENTSCOPE_AUTH_USERS={"alice":{"user_id":"user-alice","password":"alice-password"},"bob":{"user_id":"user-bob","password":"bob-password"}}
```

登录成功后，Web UI 使用 JWT 访问 API；API 从已验证的 token 中取得 `user_id`，
并沿用现有的用户维度存储隔离。修改账号配置或 JWT 密钥后需要重启
`agentscope` 服务；修改 JWT 密钥会使之前签发的 token 失效。

用户也可以在登录页自助注册。注册账号及其密码校验数据保存在 Redis 数据卷中，
每个注册账号会自动生成独立的 `user_id`；重建容器不会丢失，删除 Redis 数据卷则
会同时删除这些注册账号。登录后的用户入口提供账号信息、切换账号、退出登录和
当前账号的 Token 用量统计。

## 5. 启动服务

### 在线构建并启动

```bash
docker compose up -d --build
docker compose ps
```

### 使用已加载的离线镜像启动

```bash
docker compose up -d --no-build
docker compose ps
```

三个服务状态均应为 `running`，并且 `agentscope`、`web-ui` 的健康检查应通过。

## 6. 部署后检查

先在服务器本机检查：

```bash
# Web UI 应返回 HTML
curl -I http://127.0.0.1:8000
# 如果修改过 WEB_UI_PORT，请把上面的 8000 换成实际端口。

# API 公开存活探针应返回 204
curl -i http://127.0.0.1:8001/auth/health
# 如果修改过 AGENTSCOPE_API_PORT，请把上面的 8001 换成实际端口。
```

如果本机正常、用户电脑访问失败，通常是云服务器安全组或系统防火墙没有放行
端口。以内网用户网段为例：

```bash
sudo ufw allow from <用户网段> to any port 8000 proto tcp
sudo ufw allow from <用户网段> to any port 8001 proto tcp
```

如果确实需要公网访问，请根据实际安全策略限制来源 IP；不要对公网开放 Redis
的 `6379` 端口。

## 7. 用户首次访问

1. 运维人员确认服务器 IP 或 DNS 已指向部署服务器。
2. 在浏览器打开 `http://<服务器 IP 或域名>:8000`。
3. 使用 `.env` 中配置的用户名和密码登录。
4. 如果页面出现服务设置页，在“服务器地址”中填写
   `http://<服务器 IP 或域名>:8001` 并提交。
5. 如果页面能打开但提示无法连接服务，检查用户电脑到 `8001` 的网络连通性，
   以及浏览器保存的服务地址是否仍为 `localhost`。

用户访问的核心地址可以按下面的规则计算：

```text
Web UI = http(s)://<服务器地址>:<WEB_UI_PORT>
API    = http(s)://<服务器地址>:<AGENTSCOPE_API_PORT>
```

如果使用 HTTPS，Web UI 和 API 都应使用 HTTPS；否则浏览器可能阻止从 HTTPS
页面请求 HTTP API（混合内容）。

## 8. 使用域名和反向代理

当前 Compose 默认是双端口部署。生产环境可以用 Nginx 或 Traefik 提供域名和
HTTPS。最简单的方式是为 UI 和 API 配置两个域名：

```text
https://agent.example.com      -> 127.0.0.1:8000
https://api.agent.example.com  -> 127.0.0.1:8001
```

Nginx 示例：

```nginx
server {
    listen 443 ssl;
    server_name agent.example.com;

    # 此处配置证书
    # ssl_certificate ...;
    # ssl_certificate_key ...;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 443 ssl;
    server_name api.agent.example.com;

    # 此处配置证书
    # ssl_certificate ...;
    # ssl_certificate_key ...;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

此时用户主要打开 `https://agent.example.com`。首次使用时访问
`https://agent.example.com/setup`，将 API 地址设置为
`https://api.agent.example.com`。当前前端默认通过“同一主机名 + API 端口”推导
API 地址，因此使用两个域名时需要在设置页保存 API 地址。

如果要求用户只能使用一个域名和一个端口，需要额外实现 `/api` 路径代理，
并让前端所有 API 请求使用 `/api` 前缀；当前默认 `nginx.conf` 只负责静态 Web UI
和 SPA 路由，不包含该反向代理配置。不能只开放 Web UI 端口就认为单地址部署已经
完成。

## 9. 常用运维命令

```bash
# 查看状态
docker compose ps

# 查看最近日志
docker compose logs --tail=200 agentscope web-ui redis

# 持续查看后端日志
docker compose logs -f agentscope

# 重启服务
docker compose restart

# 停止服务，但保留数据卷
docker compose down
```

不要执行 `docker compose down -v`，否则会删除 Redis、工作区、文件和 Qdrant
数据卷。

## 10. 常见问题

| 现象 | 处理方式 |
| --- | --- |
| 浏览器打不开 `:8000` | 检查 `web-ui` 状态、服务器端口映射、安全组和防火墙 |
| 页面能打开，但聊天失败 | 检查用户电脑是否能访问 `:8001`，以及 API 地址是否正确 |
| 改了 API 端口但前端仍访问旧端口 | 执行 `docker compose up -d --build` 重新构建 Web UI |
| API 返回 503 或服务未就绪 | 查看 `docker compose logs agentscope redis`，确认 Redis 和模型配置正常 |
| 用户被分到错误的数据空间 | 检查 `AGENTSCOPE_AUTH_USERS`，确保每个账号使用唯一且稳定的 `user_id` |
| 服务器本机正常，外部访问失败 | 检查云安全组、UFW/iptables、公司网络和 DNS |

## 11. 生产环境安全检查

- 使用 HTTPS，并为 Web UI 和 API 配置一致的安全策略。
- 替换默认登录密码和 `AGENTSCOPE_JWT_SECRET`，不要把真实值提交到 Git。
- 当前 JWT 是轻量账号隔离方案；如需管理员后台、密码找回、撤销 token、权限角色或抵御恶意攻击，再接入完整账号体系或公司 SSO。
- 将 `AGENTSCOPE_DOWNLOAD_SECRET` 替换成随机长字符串。
- 设置 Redis 密码，并确认 Redis 端口没有映射到公网。
- API 端口只允许用户网段或反向代理访问。
- 定期备份 Docker 数据卷：`redis-data`、`agentscope-workspaces`、
  `agentscope-blobs` 和 `qdrant-data`。
- 模型 API Key 只放在服务器 `.env` 中，不要提交到 Git，也不要写入前端代码。

## 9. 本工作区部署取值（2026-09-11 更新）

本机 8000 / 8080 端口已被另一套 AgentScope 部署占用，lxScope 使用以下端口：

```text
Web UI：http://localhost:8002   （WEB_UI_PORT=8002）
API：   http://localhost:8003   （AGENTSCOPE_API_PORT=8003）
```

登录账号由 `.env` 中的 `AGENTSCOPE_USERNAME` / `AGENTSCOPE_PASSWORD` 决定，
默认管理员账号的 `AGENTSCOPE_USER_ID=local-user`，用于保留开启 JWT 登录前创建的数据。

### 9.1 DeepSeek 官方 API

DeepSeek 官方 API（`https://api.deepseek.com/v1`）不提供 Embedding 接口，只能用于聊天。
知识库向量化请继续使用 `SILICONFLOW_*` 配置；需要同时使用 DeepSeek 官方 API 时，
把 Key 放在独立的 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_CHAT_MODEL`
变量中，`docker-compose.yml` 已透传给后端容器。

### 9.2 基础镜像源

`docker.1panel.live` 镜像源已返回 403，两个 Dockerfile 的基础镜像改用
`docker.m.daocloud.io/library/`；如该源也不可用，需替换为其他可用镜像源后再构建。
