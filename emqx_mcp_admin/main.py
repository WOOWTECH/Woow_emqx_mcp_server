"""EMQX MCP Admin — GUI, Admin API and MCP proxy on a single port.

Mirrors `n8n_mcp_admin.main`: `mcp_admin_core` supplies the FastAPI factory,
JWT middleware, config store, subprocess manager and MCP reverse proxy; this
module only contributes the EMQX-specific routers.

`extra_routers` must be passed to the factory — routers added after
`create_app()` returns are shadowed by the SPA catch-all route.
"""

from __future__ import annotations

from mcp_admin_core.app import create_app

from .routers import config, health, logs, tokens, tools

app = create_app(
    title="EMQX MCP Admin",
    extra_routers=[
        config.router,
        tools.router,
        tokens.router,
        health.router,
        logs.router,
    ],
)
