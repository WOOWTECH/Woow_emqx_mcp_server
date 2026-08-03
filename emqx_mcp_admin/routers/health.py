"""Dashboard health payload.

The shape below is a hard contract with the shared React SPA — keep the keys
even when a value is unknown.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter

router = APIRouter(prefix="/api/health", tags=["health"])


async def _probe_emqx() -> dict[str, Any]:
    # Read the same `connection` section the GUI writes, so the dashboard
    # reflects the configured broker rather than a stale environment variable.
    try:
        from mcp_admin_core.config import get_config_store

        connection = await get_config_store().get("connection", {})
    except Exception:  # noqa: BLE001 — core absent in unit-test contexts
        connection = {}

    base = (connection.get("emqx_mcp_base_url") or "http://localhost:18083").rstrip("/")
    key = connection.get("emqx_mcp_api_key", "")
    secret = connection.get("emqx_mcp_api_secret", "")
    try:
        async with httpx.AsyncClient(timeout=5.0, auth=(key, secret)) as client:
            response = await client.get(f"{base}/api/v5/nodes")
            response.raise_for_status()
            nodes = response.json()
    except Exception as exc:  # noqa: BLE001 — surfaced to the dashboard, not raised
        return {"healthy": False, "url": base, "error": str(exc)[:200],
                "version": None, "connections": 0}

    rows = nodes if isinstance(nodes, list) else []
    return {
        "healthy": True,
        "url": base,
        "error": None,
        "version": rows[0].get("version") if rows else None,
        "connections": sum(n.get("connections", 0) or 0 for n in rows),
        "node_count": len(rows),
    }


@router.get("")
async def health() -> dict[str, Any]:
    """Aggregate status for the dashboard: broker, MCP subprocess and proxy."""
    target = await _probe_emqx()

    try:
        from mcp_admin_core.process import get_process_manager

        # McpProcessManager.status() is a coroutine in mcp_admin_core.
        status = await get_process_manager().status()
    except Exception:  # noqa: BLE001 — core absent in unit-test contexts
        status = {}

    running = bool(status.get("running"))
    return {
        "app_type": "emqx",
        "overall_status": "ok" if target["healthy"] and running else "degraded",
        "mcp_server": {
            "healthy": running,
            "pod_name": f"pid={status.get('pid')}",
            "restart_count": status.get("restart_count", 0),
        },
        "target_app": target,
        "proxy": {"healthy": True, "pod_name": "built-in reverse proxy"},
        "version": target.get("version"),
        "db_name": "emqx",
        "item_count": target.get("connections", 0),
        "namespace": os.environ.get("NAMESPACE", "podman"),
    }
