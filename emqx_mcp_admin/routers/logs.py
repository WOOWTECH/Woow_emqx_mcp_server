"""MCP subprocess logs: ring buffer, search and SSE stream."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from typing import Any

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

router = APIRouter(prefix="/api/logs", tags=["logs"])

_BUFFER: deque[str] = deque(maxlen=5000)
_SUBSCRIBERS: set[asyncio.Queue] = set()
_MCP_PREFIX = "[mcp-server] "


async def append_log_line(line: str) -> None:
    _BUFFER.append(line)
    for queue in list(_SUBSCRIBERS):
        queue.put_nowait(line)


class _BufferLogHandler(logging.Handler):
    """Pull subprocess output out of the core process logger."""

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        if _MCP_PREFIX not in message:
            return
        line = message.split(_MCP_PREFIX, 1)[-1]
        try:
            asyncio.get_running_loop().create_task(append_log_line(line))
        except RuntimeError:
            _BUFFER.append(line)


def install_log_capture() -> None:
    logging.getLogger("mcp_admin_core.process").addHandler(_BufferLogHandler())


install_log_capture()


@router.get("/search")
async def search_logs(q: str = "", limit: int = 200) -> dict[str, Any]:
    """Filter the buffered log lines with a regular expression."""
    lines = list(_BUFFER)
    if q:
        try:
            pattern = re.compile(q, re.IGNORECASE)
        except re.error as exc:
            return {"error": f"Invalid regular expression: {exc}", "lines": []}
        lines = [line for line in lines if pattern.search(line)]
    return {"count": len(lines), "lines": lines[-limit:]}


@router.get("/stream")
async def stream_logs() -> EventSourceResponse:
    """Live tail of the MCP subprocess output."""
    queue: asyncio.Queue = asyncio.Queue()
    _SUBSCRIBERS.add(queue)

    async def publisher():
        try:
            for line in list(_BUFFER)[-100:]:
                yield {"data": line}
            while True:
                yield {"data": await queue.get()}
        finally:
            _SUBSCRIBERS.discard(queue)

    return EventSourceResponse(publisher())
