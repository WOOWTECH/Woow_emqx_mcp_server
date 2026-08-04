# Contributing

Thanks for considering a contribution. This project is small and opinionated, so a little context
up front will save you time.

## Development setup

```bash
git clone https://github.com/WOOWTECH/Woow_emqx_mcp_server.git
cd Woow_emqx_mcp_server

pip install -e ".[dev]"
cd frontend && npm install && cd ..

pytest -v
```

The suite runs without a broker: 26 tests use `httpx.MockTransport`, and 11 live-broker tests skip
themselves unless `EMQX_MCP_BASE_URL`, `EMQX_MCP_API_KEY` and `EMQX_MCP_API_SECRET` are set. If you
have a spare EMQX, set them — the live tests catch things mocks cannot, and every real bug this
project has had was found that way.

## How the codebase is organised

| Package | Responsibility |
|---------|----------------|
| `emqx_mcp_server` | The MCP server: tools, gating, EMQX HTTP access |
| `emqx_mcp_admin` | EMQX-specific admin API on top of the shared core |
| `mcp_admin_core` | Shared with the n8n bundle — app factory, proxy, process manager, auth, config |
| `frontend` | React SPA, shared with the n8n bundle |
| `frontend-overrides` | The pages that differ for EMQX, applied at image build time |
| `cloudflare` | Edge Workers |

`mcp_admin_core` and `frontend` are shared across the WOOWTECH MCP fleet. Fix a bug there and it
should go upstream to [woow_n8n_mcp_server](https://github.com/WOOWTECH/woow_n8n_mcp_server) too,
otherwise the next image build will overwrite it.

## Adding a tool

1. Add a `ToolSpec` to `emqx_mcp_server/registry.py`. This is the single source of truth — the GUI
   reads the same list.
2. Write the test first, in the relevant `tests/test_*.py`, using `httpx.MockTransport` with a
   response shaped like the real EMQX one.
3. Implement it in the matching `emqx_mcp_server/tools/*.py` module, guarded by
   `if on("emqx_your_tool"):`.
4. Use `emqx_request()` rather than calling the client directly — it converts EMQX failures into
   messages a model can act on.
5. Annotate honestly: `read_only(...)` or `destructive(...)`. A destructive tool that claims to be
   read-only will be called without confirmation.

`tests/test_mcp_surface.py` asserts registry ↔ server parity, so a spec without an implementation
(or vice versa) fails the suite.

## Writing tool descriptions

Descriptions are read by a model, not a person. What works:

- Say what the tool answers, not what endpoint it calls.
- Name the tool to reach for next when the answer is empty. `emqx_list_topics` says that a missing
  topic means nobody is subscribed, which is the usual reason a published message vanishes.
- Say when an empty result is normal — open-source EMQX has no enterprise connectors, and a model
  that does not know this will report it as a fault.
- Prefix destructive docstrings with `[DESTRUCTIVE]` and state what cannot be undone.

## Style

- Python targets 3.12. Type hints on public functions.
- Comments explain *why*, not *what*. If a line of code needs a comment to say what it does,
  rename something instead.
- No speculative abstraction. Two call sites is not a pattern.
- Error messages are instructions: "Publish to a concrete topic; wildcards are only valid in
  subscriptions" beats "invalid topic".

## Pull requests

- One concern per PR.
- Include the test that failed before your change.
- If you fixed something found against a live broker, say so in the PR body and quote the real
  response. Those notes are how the next person understands the code.
