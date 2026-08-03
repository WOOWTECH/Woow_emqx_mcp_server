"""What the LogViewer page actually receives.

Seam under test: the log capture that feeds `/api/logs/stream`. Verified
against the deployment on 2026-08-03: the page connected (200,
text/event-stream) and then received nothing at all, because the core logs
subprocess output at INFO and an unconfigured logger inherits root's WARNING
— the records were dropped before any handler saw them.
"""

from __future__ import annotations

import json
import logging

import pytest

from emqx_mcp_admin.routers import logs

CORE_LOGGER = "mcp_admin_core.process"


@pytest.fixture(autouse=True)
def _clean():
    logs.clear_buffer()
    yield
    logs.clear_buffer()


async def test_subprocess_output_reaches_the_buffer():
    """An INFO record from the core must survive to the stream."""
    logging.getLogger(CORE_LOGGER).info("[mcp-server] EMQX MCP is running now!")

    messages = [json.loads(line)["message"] for line in logs.recent()]

    assert any("EMQX MCP is running now!" in m for m in messages)


async def test_lifecycle_messages_are_kept_too():
    """Start/stop/exit lines are what an operator actually looks for."""
    logging.getLogger(CORE_LOGGER).warning("MCP server exited with code 1")

    messages = [json.loads(line)["message"] for line in logs.recent()]

    assert any("exited with code 1" in m for m in messages)


async def test_lines_carry_the_fields_the_page_renders():
    """LogViewer.jsx reads timestamp, level, message and source per event."""
    logging.getLogger(CORE_LOGGER).error("[mcp-server] boom")

    entry = json.loads(logs.recent()[-1])

    assert set(entry) >= {"timestamp", "level", "message", "source"}
    assert entry["level"] == "error", "the page colours by level"
    assert entry["source"] == "mcp-server"
    assert entry["message"] == "boom", "the prefix is a tag, not part of the message"
