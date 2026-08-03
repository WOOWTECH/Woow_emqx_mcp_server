"""EMQX connection settings.

Keys written into the `connection` section are upper-cased by
`mcp_admin_core.process` and injected into the MCP subprocess environment, so
they must match what `emqx_mcp_server.settings.Settings` reads —
`emqx_mcp_base_url` becomes `EMQX_MCP_BASE_URL`. `tests/test_connection_wiring.py`
keeps the two ends in step.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/config", tags=["config"])

CONNECTION_KEYS = (
    "emqx_mcp_base_url",
    "emqx_mcp_api_key",
    "emqx_mcp_api_secret",
)


class ConnectionSettings(BaseModel):
    emqx_mcp_base_url: str = Field(
        description="EMQX dashboard base URL, e.g. http://a0d7b954-emqx:18083"
    )
    emqx_mcp_api_key: str = Field(description="API key from EMQX → API Keys.")
    emqx_mcp_api_secret: str = Field(description="Matching secret.")
    restart: bool = Field(True, description="Restart the MCP server after saving.")


async def _probe(base_url: str, key: str, secret: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=8.0, auth=(key, secret)) as client:
            response = await client.get(f"{base}/api/v5/nodes")
    except Exception as exc:  # noqa: BLE001 — shown to the operator, not raised
        return {"ok": False, "error": f"Cannot reach EMQX at {base}: {exc}"}

    if response.status_code == 401:
        return {"ok": False, "error": "EMQX rejected the API key or secret (401)."}
    if response.status_code >= 400:
        return {"ok": False, "error": f"EMQX returned HTTP {response.status_code}."}

    nodes = response.json()
    rows = nodes if isinstance(nodes, list) else []
    return {
        "ok": True,
        "version": rows[0].get("version") if rows else None,
        "node_count": len(rows),
        "edition": rows[0].get("edition") if rows else None,
    }


@router.get("")
async def get_config() -> dict[str, Any]:
    from mcp_admin_core.config import get_config_store

    connection = await get_config_store().get("connection", {})
    secret = connection.get("emqx_mcp_api_secret", "")
    return {
        "app_type": "emqx",
        "emqx_mcp_base_url": connection.get("emqx_mcp_base_url", ""),
        "emqx_mcp_api_key": connection.get("emqx_mcp_api_key", ""),
        # Never echo the secret back to the browser.
        "emqx_mcp_api_secret_masked": "********" if secret else "",
    }


@router.put("/connection")
async def put_connection(payload: ConnectionSettings) -> dict[str, Any]:
    from mcp_admin_core.config import get_config_store
    from mcp_admin_core.process import get_process_manager

    await get_config_store().patch(
        "connection",
        {key: getattr(payload, key) for key in CONNECTION_KEYS},
    )

    status = "ok"
    if payload.restart:
        manager = get_process_manager()
        if manager.is_running and not await manager.restart():
            # Saved but not yet live — say so rather than failing the request.
            status = "partial"
    return {"status": status}


@router.post("/test")
async def test_connection(payload: ConnectionSettings | None = None) -> dict[str, Any]:
    """Verify credentials against EMQX — the posted ones, or the saved ones."""
    if payload is not None:
        return await _probe(
            payload.emqx_mcp_base_url,
            payload.emqx_mcp_api_key,
            payload.emqx_mcp_api_secret,
        )

    from mcp_admin_core.config import get_config_store

    connection = await get_config_store().get("connection", {})
    return await _probe(
        connection.get("emqx_mcp_base_url", ""),
        connection.get("emqx_mcp_api_key", ""),
        connection.get("emqx_mcp_api_secret", ""),
    )
