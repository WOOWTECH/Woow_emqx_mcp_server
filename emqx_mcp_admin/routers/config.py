"""EMQX connection settings and the tool-permission policy.

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

from emqx_mcp_server.registry import TOOL_REGISTRY

router = APIRouter(prefix="/api/config", tags=["config"])

CONNECTION_KEYS = (
    "emqx_mcp_base_url",
    "emqx_mcp_api_key",
    "emqx_mcp_api_secret",
)

DEFAULT_PERMISSIONS: dict[str, Any] = {"allowed_tools": ["*"], "denied_tools": []}


class ConnectionSettings(BaseModel):
    emqx_mcp_base_url: str = Field(
        description="EMQX dashboard base URL, e.g. http://192.168.2.189:18083"
    )
    emqx_mcp_api_key: str = Field(description="API key from EMQX → API Keys.")
    emqx_mcp_api_secret: str = Field(description="Matching secret.")
    restart: bool = Field(True, description="Restart the MCP server after saving.")


class PermissionPolicy(BaseModel):
    """What the PermissionEditor page saves."""

    permissions: dict[str, Any]


async def _probe(base_url: str, key: str, secret: str) -> dict[str, Any]:
    """Ask EMQX who it is. Shape matches what ConnectionConfig.jsx reads."""
    base = (base_url or "").rstrip("/")
    if not base:
        return {"success": False, "ok": False, "message": "No EMQX URL configured yet."}
    try:
        async with httpx.AsyncClient(timeout=8.0, auth=(key, secret)) as client:
            response = await client.get(f"{base}/api/v5/nodes")
    except Exception as exc:  # noqa: BLE001 — shown to the operator, not raised
        return {"success": False, "ok": False,
                "message": f"Cannot reach EMQX at {base}: {exc}"}

    if response.status_code == 401:
        return {"success": False, "ok": False,
                "message": "EMQX rejected the API key or secret (401)."}
    if response.status_code >= 400:
        return {"success": False, "ok": False,
                "message": f"EMQX returned HTTP {response.status_code}."}

    rows = response.json()
    rows = rows if isinstance(rows, list) else []
    first = rows[0] if rows else {}
    version = first.get("version")
    edition = first.get("edition")
    # ConnectionConfig.jsx shows `message` on success, so make it readable.
    return {
        "success": True,
        "ok": True,
        "message": f"Connected to EMQX {version} {edition} · {len(rows)} node(s)",
        "version": version,
        "edition": edition,
        "node_count": len(rows),
    }


@router.get("")
async def get_config() -> dict[str, Any]:
    from mcp_admin_core.config import get_config_store

    store = get_config_store()
    connection = await store.get("connection", {}) or {}
    secret = connection.get("emqx_mcp_api_secret", "")
    tools = await store.get("tools", {}) or {}

    return {
        "app_type": "emqx",
        "emqx_mcp_base_url": connection.get("emqx_mcp_base_url", ""),
        "emqx_mcp_api_key": connection.get("emqx_mcp_api_key", ""),
        # Never echo the secret back to the browser.
        "emqx_mcp_api_secret_masked": "********" if secret else "",
        "permissions": tools.get("permissions") or DEFAULT_PERMISSIONS,
    }


@router.put("/connection")
async def put_connection(payload: ConnectionSettings) -> dict[str, Any]:
    from mcp_admin_core.config import get_config_store
    from mcp_admin_core.process import get_process_manager

    store = get_config_store()
    await store.reload()
    current = await store.get("connection", {}) or {}

    updates = {key: getattr(payload, key) for key in CONNECTION_KEYS}
    # An untouched secret field arrives as "", which must not wipe the stored one.
    if not updates["emqx_mcp_api_secret"]:
        updates["emqx_mcp_api_secret"] = current.get("emqx_mcp_api_secret", "")
    await store.patch("connection", updates)

    status = "ok"
    if payload.restart:
        manager = get_process_manager()
        if manager.is_running and not await manager.restart():
            # Saved but not yet live — say so rather than failing the request.
            status = "partial"
    return {"status": status, "success": True}


@router.post("/test")
async def test_connection(payload: ConnectionSettings | None = None) -> dict[str, Any]:
    """Verify credentials against EMQX — the posted ones, or the saved ones.

    ConnectionConfig.jsx calls this with no body, so saved values are the
    normal path.
    """
    if payload is not None and payload.emqx_mcp_base_url:
        secret = payload.emqx_mcp_api_secret
        if not secret:
            from mcp_admin_core.config import get_config_store

            saved = await get_config_store().get("connection", {}) or {}
            secret = saved.get("emqx_mcp_api_secret", "")
        return await _probe(payload.emqx_mcp_base_url, payload.emqx_mcp_api_key, secret)

    from mcp_admin_core.config import get_config_store

    connection = await get_config_store().get("connection", {}) or {}
    return await _probe(
        connection.get("emqx_mcp_base_url", ""),
        connection.get("emqx_mcp_api_key", ""),
        connection.get("emqx_mcp_api_secret", ""),
    )


@router.put("/permissions")
async def put_permissions(payload: PermissionPolicy) -> dict[str, Any]:
    """Save the tool-permission policy and translate it into switches.

    The editor speaks allow/deny lists; the MCP server speaks disabled sets.
    `allowed_tools: ["*"]` means "no allow-list restriction", otherwise every
    tool outside the list is switched off. `denied_tools` always wins.
    """
    from mcp_admin_core.config import get_config_store

    from .tools import apply_to_runtime

    policy = payload.permissions or {}
    allowed = policy.get("allowed_tools") or ["*"]
    denied = set(policy.get("denied_tools") or [])

    every = [spec.name for spec in TOOL_REGISTRY]
    unknown = sorted(
        {t for t in list(allowed) + list(denied) if t != "*" and t not in every}
    )

    disabled = set(denied)
    if "*" not in allowed:
        disabled |= {name for name in every if name not in set(allowed)}

    store = get_config_store()
    await store.reload()
    tools = await store.get("tools", {}) or {}
    merged = {**tools, "permissions": policy, "disabled_tools": sorted(disabled)}
    await store.patch("tools", merged)

    status = await apply_to_runtime(merged)
    return {
        "status": status,
        "success": True,
        "disabled_count": len(disabled),
        "unknown_tools": unknown,
    }
