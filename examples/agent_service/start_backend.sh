#!/usr/bin/env bash
# 启动 AgentScope 后端服务（SQLite 存储，无需 Redis/Docker）
# 用法: bash examples/agent_service/start_backend.sh
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$SCRIPT_DIR"

# 使用项目内的虚拟环境
VENV_PY="$PROJECT_ROOT/.venv/Scripts/python.exe"
if [ ! -x "$VENV_PY" ]; then
  echo "未找到虚拟环境，请先运行: python -m venv .venv && .venv/Scripts/pip install -e \".[service,storage-sql,vdb-qdrant,channel]\" aiosqlite python-dotenv"
  exit 1
fi

exec "$VENV_PY" main.py
