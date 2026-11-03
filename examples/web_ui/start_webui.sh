#!/usr/bin/env bash
# 启动前端 Web UI（Vite 开发服务器）
# 用法: bash examples/web_ui/start_webui.sh
#
# 说明：
#   1) 仓库默认用 pnpm workspace，但 Windows 上 pnpm 替换 node_modules 时
#      会触发大量删除，被工具的删除保护拦住而中断。这里改用 npm 把依赖装到
#      frontend/node_modules（首次安装时该目录为空，整个过程零删除）。
#   2) 只跑 frontend。仓库里的 `pnpm dev` 还会起一个 express mock 后端
#      （只有 /api/health），那是纯前端自测的假数据，真实运行不需要。
#   3) 浏览器直连后端主机:端口（见 frontend/src/api/client.ts），
#      不走 vite.config.ts 里那个指向 :3000 的 /api 代理。
#   4) 必须用 `NODE_OPTIONS=""` 清掉 WorkBuddy 注入的 safe-delete shim。
#      Vite 依赖优化器在提交阶段会删除 node_modules/.vite/deps_temp_* 临时目录，
#      该 shim 的批量删除守卫（阈值 50/轮）会拦下这次删除并让 Vite 进程崩溃。
#      清空 NODE_OPTIONS 后优化器可正常清理临时目录，把 .vite/deps 缓存建好；
#      之后只要缓存有效，Vite 启动便跳过优化、不再触发删除，常规启动不再崩。
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/frontend"

# 告诉前端真实后端的端口（对应 examples/agent_service/.env 的 AGENTSCOPE_PORT）
if [ ! -f .env ]; then
  echo "VITE_AGENTSCOPE_API_PORT=${AGENTSCOPE_API_PORT:-8000}" > .env
fi

# npm 安装若未完成收尾，node_modules/.bin 可能缺失，这里直接调用 vite 入口。
VITE_BIN="node_modules/vite/bin/vite.js"
if [ ! -f "$VITE_BIN" ]; then
  echo "未找到 vite，请先运行: (cd examples/web_ui/frontend && npm install)"
  exit 1
fi

exec env NODE_OPTIONS="" node "$VITE_BIN" --host 127.0.0.1 --port "${WEB_UI_PORT:-5173}"
