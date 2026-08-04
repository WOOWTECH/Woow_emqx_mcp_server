# --- stage 1: shared Admin SPA, built from the vendored frontend/ ----------
FROM node:20-alpine AS frontend

# frontend/ and frontend-overrides/ both live in this repo now, so no network
# is needed at build time.
COPY frontend/ /src/frontend/
COPY frontend-overrides/ConnectionConfig.jsx /src/frontend/src/pages/ConnectionConfig.jsx

WORKDIR /src/frontend/src
RUN set -eux; \
    # Sidebar: give EMQX its own product name instead of the generic fallback.
    sed -i "s|: 'MCP Admin';|: appType === 'emqx' ? 'EMQX MCP Admin' : 'MCP Admin';|" \
        components/Sidebar.jsx; \
    # Dashboard: a broker has nodes and connections, not a database and modules.
    sed -i "s|title={isN8n ? 'n8n Instance' : 'Odoo Instance'}|title={appType === 'emqx' ? 'EMQX Broker' : isN8n ? 'n8n Instance' : 'Odoo Instance'}|" \
        pages/Dashboard.jsx; \
    sed -i "s|title={isN8n ? 'Database' : 'Database'}|title={appType === 'emqx' ? 'Broker Node' : 'Database'}|" \
        pages/Dashboard.jsx; \
    sed -i "s|subtitle={isOdoo ? 'Odoo database' : isN8n ? 'n8n database' : 'Target database'}|subtitle={appType === 'emqx' ? 'EMQX cluster node' : isOdoo ? 'Odoo database' : isN8n ? 'n8n database' : 'Target database'}|" \
        pages/Dashboard.jsx; \
    sed -i "s|title={isN8n ? 'Workflows' : 'Modules'}|title={appType === 'emqx' ? 'Connections' : isN8n ? 'Workflows' : 'Modules'}|" \
        pages/Dashboard.jsx; \
    sed -i "s|subtitle={isN8n ? 'Active workflows' : 'Installed modules'}|subtitle={appType === 'emqx' ? 'Connected MQTT clients' : isN8n ? 'Active workflows' : 'Installed modules'}|" \
        pages/Dashboard.jsx; \
    # Settings: the command is python3 here, and the bearer token is generic.
    sed -i 's|placeholder="odoo-mcp-server"|placeholder="python3"|' pages/SettingsPage.jsx; \
    sed -i 's|Bearer Token (optional, for n8n proxy)|Bearer Token (optional, sent upstream to the MCP server)|' \
        pages/SettingsPage.jsx; \
    # Fail the build rather than ship a half-patched UI.
    grep -q "EMQX MCP Admin" components/Sidebar.jsx; \
    grep -q "EMQX Broker" pages/Dashboard.jsx; \
    grep -q "Connected MQTT clients" pages/Dashboard.jsx; \
    grep -q "EMQX Connection" pages/ConnectionConfig.jsx

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

# mcp_admin_core is vendored, unchanged, from the n8n bundle. The pyproject
# for it lives at the repo root as mcp_admin_core.pyproject.toml so the
# vendored source tree stays byte-identical to the upstream package.
COPY mcp_admin_core/ /tmp/build/mcp_admin_core/
COPY mcp_admin_core.pyproject.toml /tmp/build/pyproject.toml
RUN pip install /tmp/build && rm -rf /tmp/build

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
