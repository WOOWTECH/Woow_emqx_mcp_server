/**
 * EMQX MCP public endpoint — Cloudflare Worker
 *
 * Shape copied from the n8n bundle: one hostname that serves the MCP proxy and
 * nothing else, so every other path — including /.well-known/* — comes back as
 * a clean JSON 404.
 *
 * That 404 is the load-bearing part. Claude's connector runs OAuth discovery
 * before it connects; a 404 means "no sign-in service here" and it falls back
 * to an anonymous connection. The admin console answers /.well-known/* with
 * 200 text/html from its SPA catch-all, so Claude concludes a sign-in service
 * exists, tries to register against a web page, and fails with "Couldn't
 * register with … sign-in service". Sharing a hostname with the GUI is the
 * whole bug; giving the proxy its own hostname is the whole fix.
 *
 * Two routes are served:
 *
 *   /private_{token}/mcp   the bundle's own scheme, exactly as the n8n README
 *                          documents it. The token is passed through untouched
 *                          and validated by mcp_admin_core's proxy, so a wrong
 *                          token still gets the origin's 403.
 *   /mcp                   convenience alias; the private path is added here,
 *                          so this form keeps the token out of the URL.
 *
 * The trailing slash is normalised before the request leaves the edge: the
 * origin 307-redirects /private_{token}/mcp to https://localhost/mcp/, and a
 * cross-host redirect loses the request (and would drop an Authorization
 * header). Both slash forms therefore work here.
 *
 * Neither route requires authentication beyond the path token, which matches
 * the n8n deployments. Use the OAuth gateway where real auth matters.
 *
 * Bindings: UPSTREAM_BASE, UPSTREAM_TOKEN.
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Accept, mcp-session-id, mcp-protocol-version, last-event-id",
  "Access-Control-Expose-Headers": "mcp-session-id",
  "Access-Control-Max-Age": "86400",
};

const FORWARD_HEADERS = ["content-type", "accept", "mcp-session-id",
  "mcp-protocol-version", "last-event-id"];

const PRIVATE_MCP = /^\/private_([^/]+)\/mcp$/;

const notFound = (path) =>
  new Response(
    JSON.stringify({
      error: "Not found",
      message: `Cannot resolve ${path}. This host serves the MCP proxy only.`,
    }),
    {
      status: 404,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        ...CORS,
      },
    }
  );

async function forward(request, upstream) {
  const headers = new Headers();
  for (const name of FORWARD_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const response = await fetch(upstream, {
    method: request.method,
    headers,
    body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
  });

  const out = new Headers(CORS);
  for (const name of ["content-type", "mcp-session-id"]) {
    const value = response.headers.get(name);
    if (value) out.set(name, value);
  }
  out.set("cache-control", "no-store");
  return new Response(response.body, { status: response.status, headers: out });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

    const priv = path.match(PRIVATE_MCP);
    if (priv) {
      return forward(request, `${env.UPSTREAM_BASE}/private_${priv[1]}/mcp/${url.search}`);
    }
    if (path === "/mcp") {
      return forward(request, `${env.UPSTREAM_BASE}/private_${env.UPSTREAM_TOKEN}/mcp/${url.search}`);
    }
    return notFound(url.pathname);
  },
};
