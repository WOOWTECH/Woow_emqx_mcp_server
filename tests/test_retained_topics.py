"""Retained-message lookup for real topics, which always contain slashes.

Verified against EMQX 5.8.8 on 2026-08-03: the single-message endpoint
`/mqtt/retainer/message/{topic}` answers 200 for a flat topic but returns an
HTML 404 as soon as the topic contains a slash — percent-encoded or not.
Since every real topic is hierarchical, the tool has to cope.

Seam under test: the MCP tool surface, with EMQX replaced by a transport
that reproduces the observed behaviour.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from fastmcp import Client

import emqx_mcp_server.lifespan as lifespan_module
from emqx_mcp_server.gating import ToolGate
from emqx_mcp_server.server import build_server

SLASHED = "woow/mcp/e2e"
PAYLOAD = "e2e-ok"

HTML_404 = "<html><head><title>404 - NOT FOUND</title></head><body></body></html>"


_published: list[dict] = []


def _emqx_double() -> httpx.AsyncClient:
    """Stands in for EMQX 5.8.8, including its slash-in-path refusal."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if path.startswith("/api/v5/mqtt/retainer/message/"):
            return httpx.Response(404, text=HTML_404,
                                  headers={"content-type": "text/html"})

        if path == "/api/v5/publish":
            body = json.loads(request.content or b"{}")
            _published.append(body)
            return httpx.Response(
                202, json={"message": "no_matching_subscribers", "reason_code": 16}
            )

        if path == "/api/v5/mqtt/retainer/messages":
            return httpx.Response(200, json={
                "data": [{
                    "topic": SLASHED,
                    "qos": 0,
                    "payload": base64.b64encode(PAYLOAD.encode()).decode(),
                    "publish_at": "2026-08-03T18:34:36.913+08:00",
                }],
                "meta": {"count": 1, "limit": 100, "page": 1, "hasnext": False},
            })

        return httpx.Response(404, json={"code": "NOT_FOUND"})

    return httpx.AsyncClient(
        base_url="http://emqx.test/api/v5", transport=httpx.MockTransport(handler)
    )


@pytest.fixture(autouse=True)
def _use_double(monkeypatch):
    monkeypatch.setattr(lifespan_module, "make_client", _emqx_double)


async def test_retained_message_is_found_for_a_hierarchical_topic():
    """The direct path 404s, so the tool must fall back to the listing."""
    async with Client(build_server(gate=ToolGate())) as client:
        result = await client.call_tool("emqx_get_retained", {"topic": SLASHED})

    assert not result.is_error, result.content
    data = result.structured_content
    assert data["topic"] == SLASHED
    assert data["payload"] == PAYLOAD, "payload should be decoded, not base64"


async def test_missing_retained_message_says_so_plainly():
    """A topic with no retained message is a normal answer, not a crash."""
    async with Client(build_server(gate=ToolGate())) as client:
        result = await client.call_tool("emqx_get_retained", {"topic": "no/such/topic"})

    assert not result.is_error, result.content
    assert result.structured_content["found"] is False


async def test_clearing_a_hierarchical_topic_falls_back_to_an_empty_publish():
    """EMQX cannot DELETE a slashed topic, but MQTT can clear it natively.

    Publishing an empty retained payload is the protocol-level way to drop a
    retained message, so the tool uses it instead of reporting failure.
    """
    _published.clear()

    async with Client(build_server(gate=ToolGate())) as client:
        result = await client.call_tool("emqx_delete_retained", {"topic": SLASHED})

    assert not result.is_error, result.content
    assert result.structured_content["deleted"] is True

    assert len(_published) == 1, "expected exactly one clearing publish"
    assert _published[0]["topic"] == SLASHED
    assert _published[0]["payload"] == ""
    assert _published[0]["retain"] is True


async def test_publishing_with_nobody_listening_says_so():
    """EMQX answers 202 no_matching_subscribers rather than failing.

    Reporting a bare success hides the single most common reason a message
    "does nothing", so the tool passes the broker's verdict through.
    """
    async with Client(build_server(gate=ToolGate())) as client:
        result = await client.call_tool(
            "emqx_publish", {"topic": "woow/mcp/nolisteners", "payload": "hi"}
        )

    assert not result.is_error, result.content
    data = result.structured_content
    assert data["published"] is True
    assert data["delivered_to_subscribers"] is False
    assert "subscriber" in (data["broker_note"] or "").lower()
