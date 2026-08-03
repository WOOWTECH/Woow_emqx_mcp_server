"""The endpoints the Web GUI calls to render and flip the tool switches.

Seam under test: the Admin REST API, exercised over HTTP with FastAPI's
TestClient — the same boundary the React SPA talks to.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from emqx_mcp_admin.routers import tools as tools_router
from emqx_mcp_admin.store import ToolConfigStore, env_from_tool_settings


def test_switches_are_handed_to_the_mcp_subprocess_as_environment():
    """The n8n bundle stored the switches but never passed them down.

    Without this the GUI appears to work while the server keeps every tool
    loaded, so the setting is verified end to end here.
    """
    env = env_from_tool_settings(
        {
            "disabled_categories": ["Diagnostics"],
            "disabled_tools": ["emqx_kick_client"],
            "disabled_operations": {"emqx_manage_authn_users": ["delete"]},
            "readonly": True,
        }
    )

    assert env["EMQX_MCP_DISABLED_CATEGORIES"] == "Diagnostics"
    assert env["EMQX_MCP_DISABLED_TOOLS"] == "emqx_kick_client"
    assert json.loads(env["EMQX_MCP_DISABLED_OPERATIONS"]) == {
        "emqx_manage_authn_users": ["delete"]
    }
    assert env["EMQX_MCP_READONLY"] == "true"


@pytest.fixture
def client(tmp_path):
    store = ToolConfigStore(tmp_path / "config.json")
    app = FastAPI()
    app.include_router(tools_router.router)
    app.dependency_overrides[tools_router.get_store] = lambda: store
    return TestClient(app)


def test_get_tools_serves_the_flat_list_the_react_gui_reads(client):
    """ToolManager.jsx does `toolsData?.tools` and groups by `tool.category`.

    Returning only a grouped structure renders an empty page, so the flat
    list is part of the contract.
    """
    body = client.get("/api/tools").json()

    flat = {tool["name"]: tool for tool in body["tools"]}
    assert "emqx_kick_client" in flat

    tool = flat["emqx_kick_client"]
    assert tool["category"] == "Client Management"
    assert tool["enabled"] is True
    assert tool["description"]


def test_put_accepts_the_full_tool_array_the_gui_sends(client):
    """The GUI sends every tool back with its `enabled` flag flipped."""
    tools = client.get("/api/tools").json()["tools"]
    payload = [
        {**t, "enabled": False} if t["name"] == "emqx_kick_client" else t
        for t in tools
    ]

    client.put("/api/tools", json={"tools": payload})

    flat = {t["name"]: t for t in client.get("/api/tools").json()["tools"]}
    assert flat["emqx_kick_client"]["enabled"] is False
    assert flat["emqx_list_clients"]["enabled"] is True


def test_get_tools_lists_every_tool_grouped_by_category(client):
    """The GUI renders one switch per tool, grouped into sections."""
    body = client.get("/api/tools").json()

    categories = {group["category"]: group for group in body["categories"]}
    assert "Client Management" in categories

    names = {t["name"] for t in categories["Client Management"]["tools"]}
    assert {"emqx_list_clients", "emqx_kick_client"} <= names

    kick = next(
        t for t in categories["Client Management"]["tools"] if t["name"] == "emqx_kick_client"
    )
    assert kick["dangerous"] is True
    assert kick["enabled"] is True


def _find_tool(body, name):
    for group in body["categories"]:
        for tool in group["tools"]:
            if tool["name"] == name:
                return tool
    raise AssertionError(f"{name} missing from the response")


def test_switching_a_tool_off_persists_and_takes_effect(client):
    """Flipping one switch must survive a reload and leave others alone."""
    client.put("/api/tools", json={"disabled_tools": ["emqx_kick_client"]})

    body = client.get("/api/tools").json()

    assert _find_tool(body, "emqx_kick_client")["enabled"] is False
    assert _find_tool(body, "emqx_list_clients")["enabled"] is True


def test_switching_off_one_operation_keeps_the_tool_available(client):
    """Operation-level switches narrow a tool instead of removing it."""
    client.put(
        "/api/tools",
        json={"disabled_operations": {"emqx_manage_authn_users": ["delete"]}},
    )

    tool = _find_tool(client.get("/api/tools").json(), "emqx_manage_authn_users")

    assert tool["enabled"] is True
    assert {op["name"]: op["enabled"] for op in tool["operations"]} == {
        "read": True,
        "create": True,
        "delete": False,
    }
