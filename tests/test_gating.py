"""Behaviour of the three-level tool gate: category / tool / operation.

Seam under test: `emqx_mcp_server.gating.ToolGate` — the public decision
surface that answers "may this tool be registered, and which of its
operations are allowed".
"""

from emqx_mcp_server.gating import ToolGate


def test_disabling_one_tool_leaves_the_others_enabled():
    """A single disabled tool must not switch off the whole server."""
    gate = ToolGate(disabled_tools=["emqx_kick_client"])

    assert gate.is_tool_enabled("emqx_kick_client") is False
    assert gate.is_tool_enabled("emqx_list_clients") is True


def test_disabling_a_category_switches_off_every_tool_in_it():
    """Operators switch off a whole capability area in one click."""
    gate = ToolGate(disabled_categories=["Client Management"])

    assert gate.is_tool_enabled("emqx_kick_client") is False
    assert gate.is_tool_enabled("emqx_list_clients") is False
    assert gate.is_tool_enabled("emqx_broker_stats") is True


def test_disabling_an_operation_narrows_a_tool_without_removing_it():
    """Keep the ability to read users, drop the ability to delete them."""
    gate = ToolGate(disabled_operations={"emqx_manage_authn_users": ["delete"]})

    assert gate.is_tool_enabled("emqx_manage_authn_users") is True
    assert gate.allowed_operations("emqx_manage_authn_users") == {"read", "create"}


def test_disabling_every_operation_switches_the_tool_off():
    """A tool with nothing left to do must not be offered to the model."""
    gate = ToolGate(
        disabled_operations={"emqx_manage_authn_users": ["read", "create", "delete"]}
    )

    assert gate.is_tool_enabled("emqx_manage_authn_users") is False


def test_enabled_tools_reports_the_surviving_set():
    """The server registers exactly what this returns."""
    gate = ToolGate(disabled_categories=["Client Management"])

    names = {spec.name for spec in gate.enabled_tools()}

    assert "emqx_kick_client" not in names
    assert "emqx_list_clients" not in names
    assert "emqx_broker_stats" in names


def test_readonly_mode_removes_every_dangerous_tool():
    """One switch that guarantees the server cannot change the broker."""
    gate = ToolGate(readonly=True)

    assert gate.is_tool_enabled("emqx_broker_stats") is True
    assert gate.is_tool_enabled("emqx_list_clients") is True
    assert gate.is_tool_enabled("emqx_kick_client") is False
    assert gate.is_tool_enabled("emqx_manage_authn_users") is False
