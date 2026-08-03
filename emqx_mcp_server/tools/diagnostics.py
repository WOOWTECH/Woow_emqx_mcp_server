"""Packet tracing and listener inspection."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import quote

import httpx
from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from pydantic import Field

from ..deps import emqx_client
from ..errors import emqx_request, json_body
from ..gating import ToolGate
from ._common import destructive, page_of, read_only, writing

TraceTarget = Literal["clientid", "topic", "ip_address"]


def register(mcp: FastMCP, gate: ToolGate) -> None:
    on = gate.is_tool_enabled

    if on("emqx_list_traces"):

        @mcp.tool(name="emqx_list_traces", tags={"emqx", "read", "diagnostics"},
                  annotations=read_only("List EMQX Traces"))
        async def list_traces(
            emqx: httpx.AsyncClient = Depends(emqx_client),
        ) -> dict[str, Any]:
            """Packet traces defined on the broker, with status and expiry."""
            body = json_body(await emqx_request(emqx, "GET", "/trace"))
            rows = body if isinstance(body, list) else body.get("data", [])
            return {"count": len(rows or []), "traces": rows or []}

    if on("emqx_create_trace"):

        @mcp.tool(name="emqx_create_trace", tags={"emqx", "write", "diagnostics"},
                  annotations=writing("Create EMQX Trace"))
        async def create_trace(
            name: Annotated[str, Field(
                description="Unique trace name; you will reuse it to read the log.")],
            target_type: Annotated[TraceTarget, Field(
                description="What to trace on.")],
            target: Annotated[str, Field(
                description="The client id, topic filter or IP to capture.")],
            duration_seconds: Annotated[int, Field(
                description="How long to capture for.", ge=10, le=3600)] = 300,
            emqx: httpx.AsyncClient = Depends(emqx_client),
        ) -> dict[str, Any]:
            """Start capturing MQTT packets for one client, topic or IP.

            Traces are the tool for "the device connects but nothing arrives".
            Note the capture buffers in memory and only flushes to disk when it
            expires — let it run to completion before reading the log, and do
            not delete it early or the buffered events are lost.
            """
            payload = {"name": name, "type": target_type, target_type: target,
                       "duration": duration_seconds}
            body = json_body(await emqx_request(emqx, "POST", "/trace", json=payload))
            return {"name": name, "type": target_type, "target": target,
                    "duration_seconds": duration_seconds, "created": True,
                    "result": body}

    if on("emqx_get_trace_log"):

        @mcp.tool(name="emqx_get_trace_log", tags={"emqx", "read", "diagnostics"},
                  annotations=read_only("Read EMQX Trace Log"))
        async def get_trace_log(
            name: Annotated[str, Field(description="Trace name to read.")],
            max_bytes: Annotated[int, Field(
                description="Truncate the log at this many bytes.",
                ge=1024, le=1048576)] = 65536,
            emqx: httpx.AsyncClient = Depends(emqx_client),
        ) -> dict[str, Any]:
            """Read what a trace captured.

            A trace that is still running usually returns little or nothing —
            its events sit in an in-memory buffer until the trace expires.
            """
            detail = json_body(await emqx_request(
                emqx, "GET", f"/trace/{quote(name, safe='')}/log_detail"))
            return {"name": name, "detail": detail, "max_bytes": max_bytes}

    if on("emqx_delete_trace"):

        @mcp.tool(name="emqx_delete_trace",
                  tags={"emqx", "write", "destructive", "diagnostics"},
                  annotations=destructive("Delete EMQX Trace"))
        async def delete_trace(
            name: Annotated[str, Field(description="Trace name to delete.")],
            emqx: httpx.AsyncClient = Depends(emqx_client),
        ) -> dict[str, Any]:
            """[DESTRUCTIVE] Delete a trace and its captured log.

            If the trace is still running, events buffered in memory are lost.
            Call emqx_get_trace_log first, or wait for it to expire naturally.
            """
            await emqx_request(emqx, "DELETE", f"/trace/{quote(name, safe='')}")
            return {"name": name, "deleted": True}

    if on("emqx_list_listeners"):

        @mcp.tool(name="emqx_list_listeners", tags={"emqx", "read", "diagnostics"},
                  annotations=read_only("List EMQX Listeners"))
        async def list_listeners(
            emqx: httpx.AsyncClient = Depends(emqx_client),
        ) -> dict[str, Any]:
            """Listeners, their bind addresses and running state.

            This is where you confirm that 1883 (MQTT), 8883 (TLS), 8083/8084
            (WebSocket) are actually up before blaming a device.
            """
            body = json_body(await emqx_request(emqx, "GET", "/listeners"))
            rows, _ = page_of(body)
            return {"count": len(rows), "listeners": rows}
