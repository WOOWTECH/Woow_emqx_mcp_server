/**
 * EMQX MCP OAuth Gateway — Cloudflare Worker
 *
 * Claude's custom-connector flow refuses to talk to a remote MCP server that
 * does not speak OAuth 2.1: it always runs discovery, always tries Dynamic
 * Client Registration, and fails the connector if either is missing. Our MCP
 * proxy authenticates with a secret in the URL path instead, so the connector
 * never gets past discovery.
 *
 * This Worker sits in front of that proxy and supplies exactly what the
 * connector needs:
 *
 *   /.well-known/oauth-protected-resource[/mcp]   RFC 9728 resource metadata
 *   /.well-known/oauth-authorization-server[/mcp] RFC 8414 AS metadata
 *   /.well-known/openid-configuration             same, for clients that ask
 *   POST /oauth/register                          RFC 7591 dynamic registration
 *   GET|POST /oauth/authorize                     password gate, PKCE S256
 *   POST /oauth/token                             code + refresh grants
 *   ALL  /mcp                                     bearer-checked reverse proxy
 *
 * The upstream secret never leaves the edge: the Worker strips the client's
 * Authorization header and rewrites the request onto the private path.
 *
 * Two origin defects are also absorbed here, so the connector's canonical URL
 * form works: the upstream 307-redirects `/…/mcp` to `https://localhost/mcp/`
 * (a cross-host redirect drops the Authorization header), and its SPA
 * catch-all answers every `.well-known` path with 200 text/html, which
 * discovery cannot parse. This Worker answers both itself and always calls the
 * upstream with the trailing slash it wants.
 *
 * Bindings: KV (KV namespace), UPSTREAM_BASE, UPSTREAM_TOKEN, ACCESS_PASSWORD.
 */

const CODE_TTL = 600;            // seconds an authorization code stays valid
const ACCESS_TTL = 8 * 3600;     // access token lifetime
const REFRESH_TTL = 30 * 86400;  // refresh token lifetime

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers":
    "Authorization, Content-Type, Accept, mcp-session-id, mcp-protocol-version, last-event-id",
  "Access-Control-Expose-Headers": "mcp-session-id, www-authenticate",
  "Access-Control-Max-Age": "86400",
};

const json = (body, status = 200, extra = {}) =>
  new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...CORS,
      ...extra,
    },
  });

const oauthError = (error, description, status = 400) =>
  json({ error, error_description: description }, status);

function b64url(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const randomToken = (n = 32) => b64url(crypto.getRandomValues(new Uint8Array(n)));

async function s256(verifier) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return b64url(digest);
}

/** Constant-time-ish comparison, so a wrong password cannot be timed out. */
function sameSecret(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function readParams(request) {
  const url = new URL(request.url);
  const params = Object.fromEntries(url.searchParams);
  if (request.method !== "POST") return params;
  const type = request.headers.get("content-type") || "";
  if (type.includes("application/json")) {
    try {
      return { ...params, ...(await request.json()) };
    } catch {
      return params;
    }
  }
  const form = await request.formData();
  return { ...params, ...Object.fromEntries(form) };
}

// --------------------------------------------------------------------------
// Discovery documents
// --------------------------------------------------------------------------

const protectedResource = (origin) => ({
  resource: `${origin}/mcp`,
  authorization_servers: [origin],
  bearer_methods_supported: ["header"],
  scopes_supported: ["mcp"],
  resource_name: "Woow EMQX MCP",
});

const authorizationServer = (origin) => ({
  issuer: origin,
  authorization_endpoint: `${origin}/oauth/authorize`,
  token_endpoint: `${origin}/oauth/token`,
  registration_endpoint: `${origin}/oauth/register`,
  response_types_supported: ["code"],
  response_modes_supported: ["query"],
  grant_types_supported: ["authorization_code", "refresh_token"],
  token_endpoint_auth_methods_supported: ["none", "client_secret_post"],
  code_challenge_methods_supported: ["S256"],
  scopes_supported: ["mcp"],
});

// --------------------------------------------------------------------------
// OAuth endpoints
// --------------------------------------------------------------------------

async function register(request, env, origin) {
  let meta;
  try {
    meta = await request.json();
  } catch {
    return oauthError("invalid_client_metadata", "Registration body must be JSON.");
  }
  const redirectUris = Array.isArray(meta.redirect_uris) ? meta.redirect_uris : [];
  if (redirectUris.length === 0) {
    return oauthError("invalid_redirect_uri", "At least one redirect_uri is required.");
  }
  const clientId = `mcp_${randomToken(18)}`;
  const record = {
    client_id: clientId,
    client_name: meta.client_name || "MCP client",
    redirect_uris: redirectUris,
    grant_types: meta.grant_types || ["authorization_code", "refresh_token"],
    response_types: meta.response_types || ["code"],
    token_endpoint_auth_method: "none",
    scope: meta.scope || "mcp",
    client_id_issued_at: Math.floor(Date.now() / 1000),
  };
  await env.KV.put(`client:${clientId}`, JSON.stringify(record));
  return json({ ...record, registration_client_uri: `${origin}/oauth/register/${clientId}` }, 201);
}

function consentPage(origin, params, message) {
  const hidden = ["client_id", "redirect_uri", "state", "code_challenge",
    "code_challenge_method", "scope", "resource", "response_type"]
    .map((k) => (params[k] == null ? "" :
      `<input type="hidden" name="${k}" value="${escapeHtml(params[k])}">`))
    .join("");
  return new Response(`<!doctype html>
<html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>連接 Woow EMQX MCP</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&family=Noto+Sans+TC:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root { --brand:#6183FC; }
  * { box-sizing:border-box }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         background:#F5F6F8; font-family:Poppins,"Noto Sans TC",sans-serif; color:#1B1D21 }
  .card { width:min(420px,92vw); background:#fff; border-radius:20px; padding:40px 36px;
          box-shadow:0 12px 40px rgba(27,29,33,.08) }
  h1 { font-size:22px; margin:0 0 6px; font-weight:600 }
  p  { margin:0 0 24px; font-size:14px; line-height:1.6; color:#6B7280 }
  label { display:block; font-size:13px; font-weight:500; margin-bottom:8px }
  input[type=password] { width:100%; padding:13px 16px; font-size:15px; border-radius:20px;
          border:1px solid #E3E6EB; outline:none; font-family:inherit }
  input[type=password]:focus { border-color:var(--brand); box-shadow:0 0 0 3px rgba(97,131,252,.16) }
  button { width:100%; margin-top:20px; padding:13px 16px; font-size:15px; font-weight:600;
          color:#fff; background:var(--brand); border:0; border-radius:20px; cursor:pointer;
          font-family:inherit }
  button:hover { filter:brightness(.95) }
  .err { margin:0 0 16px; padding:11px 14px; border-radius:14px; background:#FDECEC;
         color:#B4231F; font-size:13px }
  .who { margin-top:22px; font-size:12px; color:#9AA0AA; text-align:center }
</style></head>
<body><form class="card" method="POST" action="${origin}/oauth/authorize">
  <h1>連接 Woow EMQX MCP</h1>
  <p>${escapeHtml(params.client_name || "用戶端")} 想要存取這台 EMQX broker 的 MCP 工具。輸入存取密碼以授權。</p>
  ${message ? `<div class="err">${escapeHtml(message)}</div>` : ""}
  <label for="pw">存取密碼</label>
  <input id="pw" type="password" name="password" autocomplete="current-password" autofocus required>
  ${hidden}
  <button type="submit">授權連接</button>
  <div class="who">woowtech · EMQX MCP Gateway</div>
</form></body></html>`, {
    status: message ? 401 : 200,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
}

function escapeHtml(v) {
  return String(v).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function authorize(request, env, origin) {
  const params = await readParams(request);
  const client = await env.KV.get(`client:${params.client_id}`, "json");

  if (!client) return oauthError("invalid_client", "Unknown client_id. Register first.", 401);
  if (params.response_type !== "code") {
    return oauthError("unsupported_response_type", "Only the authorization code flow is supported.");
  }
  if (!client.redirect_uris.includes(params.redirect_uri)) {
    return oauthError("invalid_request", "redirect_uri does not match this client's registration.");
  }
  if (!params.code_challenge || params.code_challenge_method !== "S256") {
    return oauthError("invalid_request", "PKCE with code_challenge_method=S256 is required.");
  }

  if (request.method === "GET") {
    return consentPage(origin, { ...params, client_name: client.client_name }, null);
  }
  if (!sameSecret(params.password || "", env.ACCESS_PASSWORD)) {
    return consentPage(origin, { ...params, client_name: client.client_name }, "密碼不正確，請再試一次。");
  }

  const code = randomToken(24);
  await env.KV.put(`code:${code}`, JSON.stringify({
    client_id: params.client_id,
    redirect_uri: params.redirect_uri,
    code_challenge: params.code_challenge,
    scope: params.scope || "mcp",
    resource: params.resource || `${origin}/mcp`,
  }), { expirationTtl: CODE_TTL });

  const target = new URL(params.redirect_uri);
  target.searchParams.set("code", code);
  if (params.state) target.searchParams.set("state", params.state);
  return Response.redirect(target.toString(), 302);
}

async function issueTokens(env, clientId, scope) {
  const access = randomToken();
  const refresh = randomToken();
  const now = Math.floor(Date.now() / 1000);
  await env.KV.put(`tok:${access}`, JSON.stringify({ client_id: clientId, scope, exp: now + ACCESS_TTL }),
    { expirationTtl: ACCESS_TTL });
  await env.KV.put(`rt:${refresh}`, JSON.stringify({ client_id: clientId, scope }),
    { expirationTtl: REFRESH_TTL });
  return {
    access_token: access,
    token_type: "Bearer",
    expires_in: ACCESS_TTL,
    refresh_token: refresh,
    scope,
  };
}

async function token(request, env) {
  const params = await readParams(request);

  if (params.grant_type === "refresh_token") {
    const stored = await env.KV.get(`rt:${params.refresh_token}`, "json");
    if (!stored) return oauthError("invalid_grant", "Refresh token is unknown or expired.");
    await env.KV.delete(`rt:${params.refresh_token}`);   // rotate
    return json(await issueTokens(env, stored.client_id, stored.scope));
  }

  if (params.grant_type !== "authorization_code") {
    return oauthError("unsupported_grant_type", "Use authorization_code or refresh_token.");
  }

  const grant = await env.KV.get(`code:${params.code}`, "json");
  if (!grant) return oauthError("invalid_grant", "Authorization code is unknown, used or expired.");
  await env.KV.delete(`code:${params.code}`);            // single use

  if (params.client_id && params.client_id !== grant.client_id) {
    return oauthError("invalid_grant", "Authorization code was issued to a different client.");
  }
  if (params.redirect_uri && params.redirect_uri !== grant.redirect_uri) {
    return oauthError("invalid_grant", "redirect_uri does not match the authorization request.");
  }
  if (!params.code_verifier || (await s256(params.code_verifier)) !== grant.code_challenge) {
    return oauthError("invalid_grant", "PKCE verification failed.");
  }
  return json(await issueTokens(env, grant.client_id, grant.scope));
}

// --------------------------------------------------------------------------
// The MCP endpoint itself
// --------------------------------------------------------------------------

const FORWARD_HEADERS = ["content-type", "accept", "mcp-session-id",
  "mcp-protocol-version", "last-event-id"];

async function proxyMcp(request, env, origin) {
  const auth = request.headers.get("authorization") || "";
  const presented = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  const session = presented ? await env.KV.get(`tok:${presented}`, "json") : null;

  if (!session) {
    return json({ error: "invalid_token", error_description: "A valid bearer token is required." }, 401, {
      "www-authenticate":
        `Bearer resource_metadata="${origin}/.well-known/oauth-protected-resource", error="invalid_token"`,
    });
  }

  // Always call upstream with the trailing slash: without it the origin
  // 307-redirects to https://localhost/mcp/ and the request is lost.
  const incoming = new URL(request.url);
  const upstream = new URL(
    `${env.UPSTREAM_BASE}/private_${env.UPSTREAM_TOKEN}/mcp/${incoming.search}`
  );

  const headers = new Headers();
  for (const name of FORWARD_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const response = await fetch(upstream.toString(), {
    method: request.method,
    headers,
    body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
  });

  const out = new Headers(CORS);
  for (const name of ["content-type", "mcp-session-id", "cache-control"]) {
    const value = response.headers.get(name);
    if (value) out.set(name, value);
  }
  out.set("cache-control", "no-store");
  return new Response(response.body, { status: response.status, headers: out });
}

// --------------------------------------------------------------------------

const LANDING = `<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Woow EMQX MCP Gateway</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&family=Noto+Sans+TC&display=swap" rel="stylesheet">
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#F5F6F8;
font-family:Poppins,"Noto Sans TC",sans-serif;color:#1B1D21}
.card{width:min(560px,92vw);background:#fff;border-radius:20px;padding:40px 36px;
box-shadow:0 12px 40px rgba(27,29,33,.08)}h1{font-size:22px;margin:0 0 10px;font-weight:600}
p{font-size:14px;line-height:1.7;color:#6B7280;margin:0 0 18px}
code{display:block;background:#F5F6F8;border-radius:14px;padding:14px 16px;font-size:13px;
word-break:break-all;color:#6183FC}</style></head><body><div class="card">
<h1>Woow EMQX MCP Gateway</h1>
<p>這是 EMQX MCP server 的 OAuth 2.1 閘道。把下面這個網址加成 Claude 的自訂連接器，授權時輸入存取密碼即可。</p>
<code>https://your-host/mcp</code></div></body></html>`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = `https://${url.host}`;
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

    if (path === "/.well-known/oauth-protected-resource" ||
        path === "/.well-known/oauth-protected-resource/mcp") {
      return json(protectedResource(origin));
    }
    if (path === "/.well-known/oauth-authorization-server" ||
        path === "/.well-known/oauth-authorization-server/mcp" ||
        path === "/.well-known/openid-configuration") {
      return json(authorizationServer(origin));
    }
    if (path === "/oauth/register" && request.method === "POST") return register(request, env, origin);
    if (path === "/oauth/authorize") return authorize(request, env, origin);
    if (path === "/oauth/token" && request.method === "POST") return token(request, env);
    if (path === "/mcp") return proxyMcp(request, env, origin);
    if (path === "/healthz") return json({ ok: true, upstream: env.UPSTREAM_BASE });
    if (path === "/") {
      return new Response(LANDING, { headers: { "content-type": "text/html; charset=utf-8" } });
    }
    return json({ error: "not_found", error_description: `No route for ${url.pathname}.` }, 404);
  },
};
