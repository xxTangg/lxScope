# AgentScope 内网离线部署指南

## 1. 部署包内容

部署包应包含：

- `agentscope-images.tar`：后端、Web UI、Redis 三个镜像
- `docker-compose.yml`
- `.env.example`
- `manifest.json`

构建离线包（需要联网且已安装 Docker Desktop/Docker Compose）：

```powershell
.\scripts\package_offline.ps1
```

脚本会在 `dist/agentscope-offline-时间戳/` 生成部署包，并输出镜像包 SHA256。

如果镜像已经构建完成，只需要重新导出：

```powershell
.\scripts\package_offline.ps1 -SkipBuild
```

## 2. 运维服务器要求

- Docker Engine 或 Docker Desktop
- Docker Compose v2
- 服务器可访问内网 OpenAI-compatible API
- 用户终端可访问 Web UI 地址

## 3. 运维启动

将部署包复制到服务器并执行：

```bash
docker load -i agentscope-images.tar
cp .env.example .env
```

编辑 `.env`，至少设置：

```env
AGENTSCOPE_DOWNLOAD_SECRET=请替换为随机长字符串
REDIS_PASSWORD=请设置Redis密码
```

启动服务：

```bash
docker compose up -d --no-build
docker compose ps
```

默认访问地址：

```text
Web UI：http://服务器IP:8000
API：  http://服务器IP:8001
```

用户浏览器必须能够访问配置的 `WEB_UI_PORT`（默认 `8000`）和 `8001`。停止服务：

本工作区当前宿主机的 `8000` 已被其他本地服务占用，实际 `.env` 使用
`WEB_UI_PORT=8002`，因此本机 Web UI 地址为 `http://localhost:8002`。

```bash
docker compose down
```

不要执行 `docker compose down -v`，否则会删除 Redis、工作区和文件数据。

## 4. 配置 SiliconFlow 模型

`.env` 中配置 SiliconFlow API Key 后，后端会在启动时自动创建一个名为
`SiliconFlow` 的 OpenAI-compatible 凭据：

```text
SILICONFLOW_API_KEY=你的 API Key
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_CHAT_MODEL=deepseek-ai/DeepSeek-V3.2
SILICONFLOW_EMBEDDING_MODEL=BAAI/bge-m3
SILICONFLOW_EMBEDDING_DIMENSIONS=1024
```

聊天模型走 `openai_chat`，知识库 Embedding 使用 `BAAI/bge-m3`（1024 维）。
API Key 只由后端读取并保存到 Redis，不会写入前端镜像。

当前服务已注册文本、PDF、Word、Excel、PowerPoint 和图片解析器，支持 `.txt`、`.md`、`.csv`、`.json`、`.xml`、`.yaml`、`.pdf`、`.docx`、`.xls`、`.xlsx`、`.pptx`、`.png`、`.jpg` 等格式。

聊天输入框可直接选择 `.txt`、`.md`、`.csv`、`.json`、`.xml`、`.yaml`、`.pdf`、`.docx`、`.xls`、`.xlsx`、`.pptx` 等文档。对于当前模型不原生支持的格式，后端会先提取文本再发送给模型，不需要配置 Embedding；默认单文件上限为 20 MiB，最多向聊天上下文写入 200,000 个字符。图片仍要求所选模型具备对应的多模态输入能力。

## 5. 单地址访问

当前默认需要 `8000` 和 `8001` 两个端口。若只允许用户访问一个地址（例如 `https://agent.company.local`），需在入口 Nginx/Traefik 配置：

- `/` → `web-ui:80`
- 后端 API 路径 → `agentscope:8000`

同时将前端的 API 地址设置为该统一域名。

## 6. 安全要求

当前 `X-User-ID` 是临时身份标识，不是登录认证。正式使用时应在入口增加公司 SSO、JWT、LDAP 或 Basic Auth，并限制 API 端口只允许内网访问。
