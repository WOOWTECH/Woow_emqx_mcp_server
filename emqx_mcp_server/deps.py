"""Dependency-injection helpers.

Parameters resolved through `Depends` are stripped from the JSON Schema, so
the model never sees (or can override) the HTTP client.
"""

from __future__ import annotations

import httpx
from fastmcp.server.dependencies import get_context

from .lifespan import make_client

_fallback: httpx.AsyncClient | None = None


def emqx_client() -> httpx.AsyncClient:
    """The pooled EMQX client for the current request."""
    try:
        return get_context().lifespan_context["emqx"]
    except (RuntimeError, KeyError, TypeError):
        # No lifespan context (e.g. a direct unit-test call) — lazily make one.
        global _fallback
        if _fallback is None or _fallback.is_closed:
            _fallback = make_client()
        return _fallback
