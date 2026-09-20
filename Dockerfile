FROM docker.io/library/node:20-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright

WORKDIR /app

# AgentScope's example service can launch the Playwright MCP through npx.
# Keep Node.js 20 in the image because current Playwright MCP releases
# require a modern Node runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        build-essential \
        ripgrep \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Bake the MCP server and a headless Chromium into the image. This avoids an
# interactive npx install and a first-request browser download at runtime.
RUN npm install --global @playwright/mcp@latest \
    && npx -y playwright-core install-deps chromium \
    && npx -y playwright-core install --no-shell chromium \
    && mkdir -p /root/.cache \
    && if [ ! -e /root/.cache/ms-playwright ]; then \
         ln -s /ms-playwright /root/.cache/ms-playwright; \
       fi

COPY . /app

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install uv \
    && /opt/venv/bin/pip install \
        ".[service,storage-redis,observability-postgres,channel,vdb-qdrant]"

# Runtime dependencies used by the installed office-document skills. Keeping
# them in the image avoids slow, ephemeral package installs during a chat.
RUN /opt/venv/bin/pip install \
        openpyxl \
        xlrd \
        pandas \
        pdfplumber \
        reportlab \
        python-docx \
        python-pptx \
        Pillow \
    && npm install --global pptxgenjs

ENV PATH="/opt/venv/bin:${PATH}"

RUN mkdir -p /app/examples/agent_service/workspaces /app/blobs

EXPOSE 8000

WORKDIR /app/examples/agent_service

CMD ["python", "main.py"]
