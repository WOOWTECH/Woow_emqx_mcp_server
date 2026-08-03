"""Cluster and monitoring tools (all read-only)."""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from pydantic import Field

from ..deps import emqx_client
from ..errors import emqx_request, json_body
from ..gating import ToolGate
from ..models import BrokerStats
from ._common import read_only


def register(mcp: FastMCP, gate: ToolGate) -> None:
    on = gate.is_tool_enabled

    if on("emqx_cluster_status"):

        @mcp.tool(name="emqx_cluster_status", tags={"emqx", "read", "cluster"},
                  annotations=read_only("EMQX Cluster Status"))
        async def cluster_status(
            emqx: httpx.AsyncClient = Depends(emqx_client),
        ) -> dict[str, Any]:
            """Nodes in the EMQX cluster with version, uptime and resource use.

            Start here when asked "is EMQX healthy", "what version is running",
            or before any node-specific call, to learn valid node names.
            """
            nodes = json_body(await emqx_request(emqx, "GET", "/nodes"))
            rows = nodes if isinstance(nodes, list) else []
            return {
                "node_count": len(rows),
                "nodes": [
                    {
                        "node": n.get("node"),
                        "version": n.get("version"),
                        "role": n.get("role"),
                        "status": n.get("node_status"),
                        "uptime_ms": n.get("uptime"),
                        "connections": n.get("connections"),
                        "load1": n.get("load1"),
                        "memory_used": n.get("memory_used"),
                    }
                    for n in rows
                ],
            }

    if on("emqx_node_detail"):

        @mcp.tool(name="emqx_node_detail", tags={"emqx", "read", "cluster"},
                  annotations=read_only("EMQX Node Detail"))
        async def node_detail(
            node: Annotated[str, Field(
                description="Node name exactly as emqx_cluster_status reports it, "
                            "e.g. 'emqx@127.0.0.1'.")],
            emqx: httpx.AsyncClient = Depends(emqx_client),
        ) -> dict[str, Any]:
            """Everything EMQX knows about one node."""
            return json_body(await emqx_request(emqx, "GET", f"/nodes/{node}"))

    if on("emqx_broker_stats"):

        @mcp.tool(name="emqx_broker_stats", tags={"emqx", "read", "cluster"},
                  annotations=read_only("EMQX Broker Stats"))
        async def broker_stats(
            emqx: httpx.AsyncClient = Depends(emqx_client),
        ) -> BrokerStats:
            """Live counters for the whole cluster.

            Answers "how many devices are online" and "how many subscriptions
            exist" in one call — cheaper than listing clients.
            """
            body = json_body(await emqx_request(emqx, "GET", "/stats"))
            row = body[0] if isinstance(body, list) and body else body
            row = row if isinstance(row, dict) else {}
            return BrokerStats(
                connections=row.get("connections.count", 0),
                sessions=row.get("sessions.count", 0),
                subscriptions=row.get("subscriptions.count", 0),
                topics=row.get("topics.count", 0),
                retained=row.get("retained.count", 0),
            )

    if on("emqx_metrics_current"):

        @mcp.tool(name="emqx_metrics_current", tags={"emqx", "read", "cluster"},
                  annotations=read_only("EMQX Current Metrics"))
        async def metrics_current(
            emqx: httpx.AsyncClient = Depends(emqx_client),
        ) -> dict[str, Any]:
            """Current throughput gauges (messages/s in and out, dropped, rate)."""
            return json_body(await emqx_request(emqx, "GET", "/monitor_current"))

    if on("emqx_metrics_history"):

        @mcp.tool(name="emqx_metrics_history", tags={"emqx", "read", "cluster"},
                  annotations=read_only("EMQX Metrics History"))
        async def metrics_history(
            latest_seconds: Annotated[int, Field(
                description="Length of the window to return, in seconds.",
                ge=60, le=86400)] = 3600,
            emqx: httpx.AsyncClient = Depends(emqx_client),
        ) -> dict[str, Any]:
            """Time-series metrics over a recent window.

            Use this to answer "was there a spike", "when did connections drop",
            or to correlate an incident with broker load.
            """
            body = json_body(
                await emqx_request(emqx, "GET", "/monitor",
                                   params={"latest": latest_seconds})
            )
            points = body if isinstance(body, list) else []
            return {"window_seconds": latest_seconds,
                    "point_count": len(points),
                    "points": points[-240:]}

    if on("emqx_list_alarms"):

        @mcp.tool(name="emqx_list_alarms", tags={"emqx", "read", "cluster"},
                  annotations=read_only("EMQX Alarms"))
        async def list_alarms(
            active_only: Annotated[bool, Field(
                description="True for currently firing alarms, False for history."
            )] = True,
            emqx: httpx.AsyncClient = Depends(emqx_client),
        ) -> dict[str, Any]:
            """Broker alarms — the first place to look when something feels wrong."""
            body = json_body(await emqx_request(
                emqx, "GET", "/alarms",
                params={"activated": "true" if active_only else "false"}))
            rows = body.get("data", []) if isinstance(body, dict) else body
            return {"active_only": active_only,
                    "count": len(rows or []),
                    "alarms": rows or []}

    if on("emqx_prometheus_stats"):

        @mcp.tool(name="emqx_prometheus_stats", tags={"emqx", "read", "cluster"},
                  annotations=read_only("EMQX Prometheus Stats"))
        async def prometheus_stats(
            max_bytes: Annotated[int, Field(
                description="Truncate the exposition text at this many bytes.",
                ge=1024, le=262144)] = 65536,
            emqx: httpx.AsyncClient = Depends(emqx_client),
        ) -> dict[str, Any]:
            """Raw Prometheus exposition text. Large — prefer emqx_broker_stats."""
            resp = await emqx_request(emqx, "GET", "/prometheus/stats")
            text = resp.text
            return {"truncated": len(text) > max_bytes, "text": text[:max_bytes]}
