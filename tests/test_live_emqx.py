"""End-to-end checks against a real EMQX broker.

Seam under test: the contract between our tool code and the live EMQX v5
REST API — the one thing unit tests with fake payloads cannot prove.

Skipped unless credentials are present:

    EMQX_MCP_BASE_URL=http://192.168.2.189:18083 \
    EMQX_MCP_API_KEY=... EMQX_MCP_API_SECRET=... \
    python -m pytest tests/test_live_emqx.py -v

Only read-only tools run by default. Set EMQX_LIVE_ALLOW_WRITE=1 to also
exercise publish and retained-message deletion against a scratch topic.
"""

from __future__ import annotations

import os

import pytest
from fastmcp import Client

from emqx_mcp_server.gating import ToolGate
from emqx_mcp_server.server import build_server

pytestmark = pytest.mark.skipif(
    not (os.environ.get("EMQX_MCP_API_KEY") and os.environ.get("EMQX_MCP_API_SECRET")),
    reason="live EMQX credentials not configured",
)

ALLOW_WRITE = os.environ.get("EMQX_LIVE_ALLOW_WRITE") == "1"
SCRATCH_TOPIC = "woow/mcp/e2e"


@pytest.fixture
async def client():
    async with Client(build_server(gate=ToolGate())) as c:
        yield c


async def _data(client, tool: str, **args):
    result = await client.call_tool(tool, args)
    assert not result.is_error, f"{tool} failed: {result.content}"
    return result.structured_content


async def test_cluster_status_parses_a_live_response(client):
    """Proves /nodes exists and our field names match reality."""
    data = await _data(client, "emqx_cluster_status")

    assert data["node_count"] >= 1
    node = data["nodes"][0]
    assert node["version"], "EMQX did not report a version"
    assert node["node"], "EMQX did not report a node name"


async def test_broker_stats_returns_real_counters(client):
    """The dotted keys EMQX uses (connections.count) are easy to get wrong."""
    data = await _data(client, "emqx_broker_stats")

    for field in ("connections", "sessions", "subscriptions", "topics"):
        assert isinstance(data[field], int), f"{field} was not an integer"


async def test_listing_clients_matches_the_stats_counter(client):
    """Cross-check two endpoints against each other."""
    stats = await _data(client, "emqx_broker_stats")
    clients = await _data(client, "emqx_list_clients", limit=200)

    assert clients["total"] >= 0
    if stats["connections"] <= 200:
        connected = [c for c in clients["clients"] if c["connected"]]
        assert len(connected) == stats["connections"]


async def test_subscriptions_and_topics_are_readable(client):
    """Both paginated endpoints parse, and the shapes agree."""
    topics = await _data(client, "emqx_list_topics", limit=50)
    subs = await _data(client, "emqx_list_subscriptions", limit=50)

    assert isinstance(topics["topics"], list)
    assert isinstance(subs["subscriptions"], list)


async def test_retained_messages_are_readable(client):
    """Retainer lives under /mqtt/retainer, an easy path to get wrong."""
    data = await _data(client, "emqx_list_retained", limit=20)

    assert isinstance(data["messages"], list)


async def test_listeners_report_the_mqtt_port(client):
    """1883 must be present, otherwise nothing could connect at all."""
    data = await _data(client, "emqx_list_listeners")

    binds = " ".join(str(row) for row in data["listeners"])
    assert "1883" in binds


async def test_authenticators_are_readable(client):
    """The chain drives every device login, so its shape must parse."""
    data = await _data(client, "emqx_list_authn")

    assert isinstance(data["authenticators"], list)


async def test_alarms_endpoint_parses(client):
    data = await _data(client, "emqx_list_alarms", active_only=True)

    assert isinstance(data["alarms"], list)


async def test_unknown_client_produces_an_actionable_error(client):
    """Our 404 translation should reach the model as guidance, not a trace."""
    result = await client.call_tool(
        "emqx_get_client", {"clientid": "definitely-not-a-real-client-xyz"}
    )

    assert result.is_error
    assert "not" in str(result.content).lower()


@pytest.mark.skipif(not ALLOW_WRITE, reason="set EMQX_LIVE_ALLOW_WRITE=1 to run")
async def test_publish_then_read_back_the_retained_message(client):
    """Full round trip: publish retained, read it back, delete it."""
    published = await _data(
        client, "emqx_publish",
        topic=SCRATCH_TOPIC, payload="e2e-ok", qos=0, retain=True,
    )
    assert published["published"] is True

    stored = await _data(client, "emqx_get_retained", topic=SCRATCH_TOPIC)
    assert "e2e-ok" in str(stored)

    removed = await _data(client, "emqx_delete_retained", topic=SCRATCH_TOPIC)
    assert removed["deleted"] is True


@pytest.mark.skipif(not ALLOW_WRITE, reason="set EMQX_LIVE_ALLOW_WRITE=1 to run")
async def test_publishing_to_a_wildcard_is_refused_before_it_leaves(client):
    """Guard clause must fire locally rather than letting EMQX 400."""
    result = await client.call_tool(
        "emqx_publish", {"topic": "woow/mcp/#", "payload": "nope"}
    )

    assert result.is_error
    assert "wildcard" in str(result.content).lower()
