# woow_emqx_mcp_server

MCP server, Admin console and reverse proxy for **EMQX**, packaged the same way
as [`woow_n8n_mcp_server`](https://github.com/WOOWTECH/woow_n8n_mcp_server):
one container, one port, a web GUI in front of it.

**39 tools across 7 categories, each individually switchable from the GUI.**

---

## Why this exists

The n8n bundle wraps a third-party MCP server (`n8n-mcp` from npm). EMQX has no
equivalent worth wrapping:

| Candidate | Verdict |
|---|---|
| `emqx/emqx-mcp-server` | Does not exist — the URL 404s |
| `Benniu/emqx-mcp-server` | 5 tools, stdio only, every parameter is an opaque `dict` so the model has to guess field names |
| EMQX official MCP repos | All "MCP **over** MQTT" — MQTT as a transport for MCP, the opposite problem |
| `rodrigo-abena/emqx-admin-mcp` | 22 tools but unmaintained and unverified; useful only as a tool-list reference |

So this repo also contains the MCP server itself, written against
**FastMCP 3.4.5** (`PrefectHQ/fastmcp` — note the project moved from
`jlowin/fastmcp`, and most tutorials online still describe 2.x).

---

## Architecture

```
AI client (Claude, Cursor, n8n agent)
      │  http://host:8080/private_{token}/mcp
      ▼
:8080  FastAPI  ─ React SPA (/)  ─ Admin API (/api/*, JWT)  ─ MCP proxy (token)
      │
      ▼  spawned child process, bound to loopback only
127.0.0.1:3000  emqx-mcp-server  (FastMCP 3.x, 39 tools)
      │
      ▼
:18083  EMQX REST API v5   (HTTP Basic: API key / secret)
```

The MCP server binds `127.0.0.1`, so the proxy's token check cannot be
bypassed — that is a property of the architecture, not of a config flag.

### Reused parts

`mcp_admin_core/` and `frontend/` are vendored unchanged from
`woow_n8n_mcp_server`. Copy them in before building:

```bash
git clone https://github.com/WOOWTECH/woow_n8n_mcp_server /tmp/n8n-bundle
cp -r /tmp/n8n-bundle/mcp_admin_core /tmp/n8n-bundle/frontend .
cp /tmp/n8n-bundle/pyproject.toml core_pyproject.toml
```

---

## Tool switches

Three levels of granularity — switching one thing off never switches
everything off:

| Level | Effect | Stored as |
|---|---|---|
| **Category** | All tools in a section disappear | `disabled_categories` |
| **Tool** | One tool disappears | `disabled_tools` |
| **Operation** | Tool stays, but e.g. loses `delete` | `disabled_operations` |
| **Read-only** | Every tool marked dangerous disappears | `readonly` |

Disabled tools are **not registered at all**, so they never reach the model's
tool list — it cannot waste a turn attempting something that is switched off.
Disabling every operation of a tool removes the tool entirely.

The GUI writes these through `PUT /api/tools`; `env_from_tool_settings()`
serialises them into the child process environment on restart.

> The n8n bundle stores the same switches but never forwards them to the
> subprocess, so its GUI toggles have no real effect. `tests/test_admin_tools_api.py`
> pins the fixed behaviour here.

### Tool inventory

| Category | Tools | Dangerous |
|---|---:|---:|
| Cluster & Monitoring | 7 | 0 |
| Client Management | 6 | 3 |
| Topics & Subscriptions | 2 | 0 |
| Messaging | 5 | 3 |
| Access Control | 8 | 4 |
| Diagnostics | 5 | 2 |
| Data Integration | 6 | 1 |
| **Total** | **39** | **13** |

`tests/test_mcp_surface.py` asserts the registry and the server advertise
exactly the same set, so a GUI switch can never point at a missing tool.

---

## Running it

### Standalone MCP server (stdio, for Claude Desktop / Claude Code)

```bash
pip install -e ".[dev]"
export EMQX_MCP_BASE_URL=http://192.168.2.189:18083
export EMQX_MCP_API_KEY=...  EMQX_MCP_API_SECRET=...
python -m emqx_mcp_server.server
```

```json
{ "mcpServers": { "emqx": {
    "command": "python", "args": ["-m", "emqx_mcp_server.server"],
    "env": { "EMQX_MCP_BASE_URL": "http://192.168.2.189:18083",
             "EMQX_MCP_API_KEY": "...", "EMQX_MCP_API_SECRET": "..." } } } }
```

### Full bundle

```bash
docker compose up -d          # http://localhost:8080, default login admin/admin
```

Remote clients then point at `http://host:8080/private_{token}/mcp`.

### Getting EMQX credentials

EMQX dashboard → **System Settings → API Keys → Create**. Use the key as the
username and the secret as the password (HTTP Basic).

---

## Safety

Four independent layers, because EMQX tools can disconnect every device:

1. **Architecture** — MCP server on loopback, proxy token required.
2. **Registration** — `EMQX_MCP_READONLY=true` means dangerous tools are never
   registered (not merely refused at call time).
3. **Authorization** — tools carry a `destructive` tag for
   `restrict_tag("destructive", require_scopes(...))`.
4. **Hints** — `ToolAnnotations(destructiveHint=True)` makes Claude prompt for
   confirmation; descriptions start with `[DESTRUCTIVE]`.

MCP annotations are hints from an untrusted server and are *not* a security
boundary — layers 1–3 are the real controls.

Four endpoints are deliberately **not** exposed as tools: clearing all retained
messages, clearing all ACL rules, forcing a node out of the cluster, and
stopping a listener.

---

## Verified against a live broker

Checked on 2026-08-03 against EMQX **5.8.8 Opensource** (`emqx@woowtechshowha.local`,
1 node, 69 subscriptions). Confirmed correct: the dotted stat keys
(`connections.count`, `retained.count`), the `{data, meta{count, hasnext}}`
pagination envelope, `/mqtt/retainer/messages`, `/listeners` reporting
1883/8883/8083/8084, the `password_based:built_in_database` authenticator id,
`:` needing percent-encoding in authenticator ids, and the `CLIENTID_NOT_FOUND`
404 shape.

Three things only a live broker revealed:

**Slashes cannot appear in a retained-message path.**
`GET /mqtt/retainer/message/{topic}` answers 200 for `woowe2eflat` but an HTML
404 for `woow/mcp/e2e` — percent-encoded or not. Since every real topic is
hierarchical, `emqx_get_retained` falls back to scanning the listing, and
`emqx_delete_retained` clears the topic the MQTT-native way, by publishing an
empty retained payload. Both paths are pinned by `tests/test_retained_topics.py`.

**Publishing answers 202, not 200.**
With nothing subscribed EMQX returns `{"message":"no_matching_subscribers",
"reason_code":16}`. Reporting a plain success would hide the most common reason
a message "does nothing", so `emqx_publish` surfaces
`delivered_to_subscribers` and `broker_note`.

**Retained payloads come back base64-encoded.**
`ZmxhdC1vaw==` rather than `flat-ok`. They are decoded before the model sees them.

## Tests

```bash
python -m pytest -q      # 17 tests, plus 11 live checks when credentials are set
```

Written test-first at three agreed seams:

- `test_gating.py` — the three-level switch logic.
- `test_mcp_surface.py` — what an AI client actually sees, through FastMCP's
  in-memory `Client`.
- `test_admin_tools_api.py` — the endpoints the React GUI calls.
- `test_retained_topics.py` — the EMQX quirks found during live verification.
- `test_live_emqx.py` — end-to-end against a real broker; skipped without credentials.

---

## Layout

```
emqx_mcp_server/          the MCP server
  registry.py             39 tool specs — shared with the GUI
  gating.py               category / tool / operation switches
  server.py               build_server(gate) assembles what survives
  lifespan.py deps.py errors.py models.py settings.py
  tools/                  cluster clients topics messaging security
                          diagnostics integration
emqx_mcp_admin/           the admin layer
  main.py                 create_app(extra_routers=[...])
  store.py                switch persistence + subprocess env
  routers/                config tools tokens health logs
mcp_admin_core/           vendored from woow_n8n_mcp_server
frontend/                 vendored from woow_n8n_mcp_server
tests/
```
