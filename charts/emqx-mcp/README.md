# emqx-mcp Helm chart

Deploys the EMQX MCP admin bundle — the 39-tool EMQX v5 MCP server, the FastAPI
admin GUI and the MCP reverse proxy, all on port 8080 in one container.

This chart is a faithful conversion of [`k8s-deploy.yaml`](../../k8s-deploy.yaml)
at the repository root. It renders the same three objects, field for field:

| Object | Name |
|--------|------|
| Namespace | `emqx-mcp` (skipped when it equals the release namespace — see `namespace.name` below) |
| Deployment | `emqx-mcp-admin` — init container `seed-config` + container `admin` |
| Service | `emqx-mcp-admin` — ClusterIP `:8080` |

The only deliberate difference is the `helm.sh/resource-policy: keep`
annotation that `keepOnUninstall` adds. `scripts/check-drift.sh` proves it, and
CI runs that script on every push.

What the chart deliberately does **not** contain:

- **The Cloudflare Tunnel.** `cloudflared` lives in its own `cloudflare`
  namespace and serves several hostnames from one tunnel; a second connector
  for the same token would split traffic. `emqx-mcp.woowtech.io` is routed
  there, to `http://emqx-mcp-admin.emqx-mcp.svc.cluster.local:8080`.
- **The Cloudflare Workers** in [`cloudflare/`](../../cloudflare). Deploy those
  with `wrangler`, not Helm.
- **A broker.** The console talks to an external EMQX over its REST API. For a
  broker of your own, see
  [WOOWTECH/Woow_k3s_emqx](https://github.com/WOOWTECH/Woow_k3s_emqx).

## The three Secrets

`k8s-deploy.yaml` referenced three Secrets and created none of them; the README
even claimed it created one. They were made by hand on the cluster, so nothing
in git described them. This chart fixes both halves:

| Secret | Key | Used for |
|--------|-----|----------|
| `emqx-mcp-config` | `config.json` | Seeded into `/data/config.json` by the init container: admin password, MCP path token, EMQX API credentials, tool gating |
| `emqx-mcp-jwt` | `secret` | `JWT_SECRET`, signs admin sessions |
| `ghcr-pull` | `.dockerconfigjson` | Pull credential for the private GHCR package |

`secrets.create` is `false` by default: the chart only *references* them, so
`helm upgrade` can never overwrite a live credential with an empty value. Their
exact shape, with placeholders, is in
[`examples/secrets.example.yaml`](examples/secrets.example.yaml) — copy it
outside the repository, fill it in, apply it.

Set `secrets.create=true` to have the chart render all three from values
instead (fresh installs and tests). Every value is then guarded by `required()`,
so a typo fails the render rather than producing a half-configured Secret.

## Install

From a clone:

```bash
git clone https://github.com/WOOWTECH/Woow_emqx_mcp_server.git
cd Woow_emqx_mcp_server

# The Secrets first (see examples/secrets.example.yaml)
kubectl create namespace emqx-mcp
kubectl -n emqx-mcp apply -f /secure/path/emqx-mcp-secrets.yaml

helm install emqx-mcp charts/emqx-mcp -n emqx-mcp --create-namespace
```

From the GitHub tarball, without cloning:

```bash
curl -sSL https://github.com/WOOWTECH/Woow_emqx_mcp_server/archive/refs/heads/main.tar.gz | tar -xz
helm install emqx-mcp Woow_emqx_mcp_server-main/charts/emqx-mcp \
  -n emqx-mcp --create-namespace
```

Or, letting the chart create the Secrets on a fresh install:

```bash
helm install emqx-mcp charts/emqx-mcp -n emqx-mcp --create-namespace \
  --set secrets.create=true \
  --set secrets.adminPassword="$(openssl rand -base64 24)" \
  --set secrets.mcpAuthToken="$(openssl rand -hex 16)" \
  --set secrets.jwtSecret="$(openssl rand -base64 48)" \
  --set secrets.emqx.baseUrl=http://emqx.emqx.svc.cluster.local:18083 \
  --set secrets.emqx.apiKey=... --set secrets.emqx.apiSecret=... \
  --set-file secrets.dockerConfigJson=/secure/path/dockerconfig.json
```

## Key values

| Value | Default | Notes |
|-------|---------|-------|
| `namespace.create` / `namespace.name` | `true` / `""` (falls back to `-n`) | Every namespaced object renders into `namespace.name`, or `-n` when it is left empty — so a plain `-n <ns>` install always stays inside `<ns>`. Set it explicitly only to reproduce `k8s-deploy.yaml`'s hardcoded `emqx-mcp`, or for a takeover into a namespace that differs from `-n`. A Namespace equal to the effective namespace is never rendered, so `helm uninstall` cannot delete it |
| `keepOnUninstall` | `true` | Adds `helm.sh/resource-policy: keep` to the Namespace and to chart-created Secrets |
| `image.repository` / `.tag` | `ghcr.io/woowtech/woow-emqx-mcp-admin` / `v1.0.0` | Built by hand; see the header of `k8s-deploy.yaml` |
| `imagePullSecret` | `ghcr-pull` | Set to `""` once the GHCR package is public; `imagePullSecrets` then disappears from the pod spec |
| `config.secretName` | `emqx-mcp-config` | Must hold key `config.json` |
| `jwt.secretName` / `.secretKey` / `.expiryHours` | `emqx-mcp-jwt` / `secret` / `24` | |
| `probes.readiness` / `probes.liveness` | `10`/`10`, `30`/`30` | Rendered verbatim under `httpGet: /healthz:8080`; add `timeoutSeconds` here |
| `resources` | 100m/192Mi → 1000m/768Mi | |
| `service.type` / `.port` | `ClusterIP` / `8080` | |
| `service.labels` | `{}` | Extra labels on the Service object only (not the selector, not the pod template). Empty keeps the render exactly `k8s-deploy.yaml`; the live instance sets `app.kubernetes.io/name` because the running Service carries it — see "Taking over" |
| `hardening.enabled` | `false` | Opt-in `securityContext` + `automountServiceAccountToken: false`. **Changes the pod template, so it restarts the pod** |
| `tools.*` | permissive | Written into `config.json` only when `secrets.create=true` |
| `secrets.create` | `false` | See above |
| `tests.enabled` | `true` | `helm test` smoke pod |
| `tests.requireBrokerHealthy` | `false` | Make the smoke test fail when the broker itself is unreachable |

## Verify

```bash
kubectl -n emqx-mcp rollout status deploy/emqx-mcp-admin --timeout=5m
helm test emqx-mcp -n emqx-mcp --logs
```

The smoke pod is read-only. It checks, in order: `/healthz` is 200; `/` serves
the built SPA; `/api/health` without a token is 401; the admin password from
the Secret logs in; `/api/health` with that JWT reports `app_type=emqx` and a
running MCP child process; `initialize` on `/private_<mcp_auth_token>/mcp/`
answers 200; and a wrong path token is rejected with 403.

By hand, without an Ingress or NodePort:

```bash
kubectl -n emqx-mcp port-forward svc/emqx-mcp-admin 8080:8080
curl -s localhost:8080/healthz          # {"status":"ok"}
open http://localhost:8080              # GUI; log in with admin_password
```

Drift check against the manifest, and optionally against a cluster:

```bash
charts/emqx-mcp/scripts/check-drift.sh
CONTEXT=default NAMESPACE=emqx-mcp charts/emqx-mcp/scripts/check-drift.sh
```

### Reproducing the CI checks locally

`.github/workflows/chart.yml` pins its tools, so quote the version alongside any
result you report — a different local binary can give a different count.

```bash
helm version                 # CI pins v3.19.5 (azure/setup-helm@v4)
kubeconform -v               # CI pins v0.6.7
```

Get the same kubeconform CI uses instead of whatever is on `$PATH`:

```bash
curl -sSL https://github.com/yannh/kubeconform/releases/download/v0.6.7/kubeconform-linux-amd64.tar.gz \
  | tar -xz -C /usr/local/bin kubeconform
helm lint charts/emqx-mcp
kubeconform -strict -summary <rendered values combinations>
# 7 combinations (default-ns, explicit-ns, instance, production,
# secrets.create=true, hardening, minimal)
#   -> 24 resources found in 7 files - Valid: 24, Invalid: 0, Errors: 0
```

v0.6.7 and v0.8.0 both report 24/24 on this chart today, but only v0.6.7 is the
number CI will reproduce.

No template may hardcode a namespace. CI asserts it, and so can you — every
rendered `namespace:` must be the one passed with `-n`:

```bash
helm template t charts/emqx-mcp -n SENTINEL --skip-tests | grep '^  namespace:'
#   namespace: SENTINEL
#   namespace: SENTINEL
```

## Uninstall — data is kept

```bash
helm uninstall emqx-mcp -n emqx-mcp
```

With `keepOnUninstall: true` (the default) the Namespace and every Secret the
chart created survive, so the admin password, the MCP token and the EMQX API
credentials are not destroyed. There are no PVCs: `/data` is an `emptyDir` and
the Secret is the source of truth. Secrets you created out of band are never
owned by the release and are untouched either way.

To remove the credentials too, delete them explicitly afterwards.

## Taking over the existing deployment

The live objects were created with `kubectl apply -f k8s-deploy.yaml`, so they
carry no Helm ownership metadata. `--take-ownership` adopts them in place:

```bash
helm --kube-context default upgrade --install emqx-mcp charts/emqx-mcp \
  -n emqx-mcp --take-ownership \
  -f charts/emqx-mcp/deploy/local-k3s/emqx-mcp.yaml
```

`deploy/local-k3s/emqx-mcp.yaml` holds the instance values (no secrets). With
them, `helm template` renders a Deployment **field-for-field identical** to
`k8s-deploy.yaml` — the whole pod template included — so the adoption changes no
field of the pod template and nothing restarts. Keep `hardening.enabled=false`
for the takeover; it is the one switch that would.

Two objects on the running cluster carry labels that `k8s-deploy.yaml` never
declared (no revision in git history has them — they were added out of band):

| live object | extra label | what the takeover does |
| --- | --- | --- |
| `Service/emqx-mcp-admin` | `app.kubernetes.io/name: emqx-mcp-admin` | kept: `service.labels` in the instance values reproduces it. Without that value an upgrade would strip it — harmless (Service labels are not part of the selector, nothing restarts) but a silent change. |
| `Namespace/emqx-mcp` | `purpose: e2e` | untouched: the Namespace equals the release namespace, so the chart never renders that object at all. |

So "identical to `k8s-deploy.yaml`" and "identical to what is running" are the
same statement for the Deployment and the Service, and the chart deliberately
does not reproduce the live Namespace.

Helm itself then stamps every adopted object with the label
`app.kubernetes.io/managed-by: Helm` and the annotations
`meta.helm.sh/release-name` / `meta.helm.sh/release-namespace`. That is how
ownership is recorded, no template renders it, and it lands on the object's own
metadata — never on the pod template — so it restarts nothing either.

As of 2026-09-12 that deployment is down — the node its pod was scheduled on is
`NotReady` and `cloudflared` is `0/1` — so the takeover needs a healthy cluster
first.

## Follow-ups this chart does not fix

These need a pod restart, a code change or a decision, so they are out of scope
for a like-for-like chart conversion:

1. **`hardening.enabled=true` is off.** The live container runs as root with no
   `securityContext` and an auto-mounted ServiceAccount token it never uses.
   The switch was verified in a throwaway namespace - the pod comes up as
   uid 1000 with `automountServiceAccountToken: false` and all capabilities
   dropped, and the full smoke test still passes - but turning it on changes the
   pod template and therefore restarts the pod, so do it in a maintenance
   window, not as part of the takeover.
2. **`/data` is an `emptyDir`.** Every restart re-seeds `config.json` from the
   Secret, so a token rotated in the GUI silently reverts. A fix means writing
   GUI changes back to the Secret, or a PVC plus `setdefault`-style seeding as
   in `Woow_k3s_litellm`.
3. **The image tag is mutable and `IfNotPresent`.** `v1.0.0` has been rebuilt
   repeatedly with no digest pin, so nodes can run different code under the same
   tag. Pin a digest, or publish immutable tags from CI.
4. **The frontend build is not reproducible.** There is no
   `frontend/package-lock.json`, so `npm install --include=dev` resolves
   whatever is latest on the day: the same `Dockerfile` builds cleanly today
   (`vite v6.4.3`, 1642 modules) but resolution can drift and has broken at
   least once (a `react-router` that failed with `Rollup failed to resolve
   import "cookie"`). Committing a lockfile would pin it.
5. **`cloudflare/mcp-direct.js` injects the upstream token on `/mcp`.** Anyone
   reaching that hostname gets an unauthenticated, CORS-open path to all 39
   tools, including destructive ones. Serve the OAuth gateway instead, or drop
   the `/mcp` alias.
6. **`change-me` defaults still in the repo.** `mcp_admin_core/config/store.py`
   falls back to `admin_password: "admin"`, and `docker-compose.yml` /
   `.env.example` ship `JWT_SECRET=change-me`. The chart never uses them —
   `required()` makes every credential explicit — but the Docker paths do.
