"""The hand-off from the Admin GUI to the MCP subprocess.

`mcp_admin_core.process` upper-cases every key of the `connection` section
and injects it into the child environment. If those names do not line up with
what `Settings` reads, the GUI saves credentials that the server never sees —
and the only symptom is a puzzling 401.

Seam under test: the connection section's key names.
"""

from __future__ import annotations

from emqx_mcp_admin.routers.config import CONNECTION_KEYS
from emqx_mcp_server.settings import Settings


def test_connection_keys_upper_case_into_the_variables_settings_reads():
    injected = {key.upper() for key in CONNECTION_KEYS}

    prefix = Settings.model_config["env_prefix"]
    expected = {f"{prefix}{field}".upper() for field in ("base_url", "api_key", "api_secret")}

    assert expected <= injected, (
        f"Settings reads {sorted(expected)} but the GUI would inject "
        f"{sorted(injected)}"
    )


def test_settings_actually_pick_up_the_injected_values(monkeypatch):
    """Prove it end to end rather than trusting the naming convention."""
    for key in CONNECTION_KEYS:
        monkeypatch.setenv(key.upper(), f"value-for-{key}")

    settings = Settings()

    assert settings.base_url == "value-for-emqx_mcp_base_url"
    assert settings.api_key == "value-for-emqx_mcp_api_key"
    assert settings.api_secret == "value-for-emqx_mcp_api_secret"
