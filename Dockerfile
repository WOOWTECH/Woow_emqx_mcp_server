# --- stage 1: shared Admin SPA, built from the n8n bundle -------------------
FROM node:20-alpine AS frontend
RUN apk add --no-cache git
RUN git clone --depth 1 https://github.com/WOOWTECH/woow_n8n_mcp_server /src
WORKDIR /src/frontend
# npm ci skips devDependencies when NODE_ENV=production, and vite is a devDep.
RUN npm install --include=dev && npx vite build

# --- stage 2: runtime -------------------------------------------------------
# No Node here: unlike the n8n bundle the MCP server is Python, so the runtime
# image stays small.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_ADMIN_CONFIG=/data/config.json

# mcp_admin_core is vendored, unchanged, from the n8n bundle.
RUN apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/WOOWTECH/woow_n8n_mcp_server /tmp/bundle \
 && pip install /tmp/bundle && rm -rf /tmp/bundle

COPY pyproject.toml README.md ./
COPY emqx_mcp_server/ ./emqx_mcp_server/
COPY emqx_mcp_admin/ ./emqx_mcp_admin/
RUN pip install ".[admin]"

COPY --from=frontend /src/frontend/dist ./static/

VOLUME /data
EXPOSE 8080

# GUI, Admin API and the MCP proxy share 8080. The MCP server itself is
# spawned as a child on 127.0.0.1:3000 and is unreachable from outside.
CMD ["uvicorn", "emqx_mcp_admin.main:app", "--host", "0.0.0.0", "--port", "8080"]
