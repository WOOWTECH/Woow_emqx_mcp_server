# emqx-mcp Helm chart

部署 EMQX MCP admin bundle —— 39 個工具的 EMQX v5 MCP server、FastAPI 管理 GUI
和 MCP 反向代理，全部在同一個容器的 8080 埠上。

這個 chart 是 repo 根目錄 [`k8s-deploy.yaml`](../../k8s-deploy.yaml) 的忠實轉換，
渲染出同樣三個資源，逐欄相同：

| 資源 | 名稱 |
|------|------|
| Namespace | `emqx-mcp`（等於 release namespace 時不渲染 — 見下方 `namespace.name`） |
| Deployment | `emqx-mcp-admin` —— init container `seed-config` + container `admin` |
| Service | `emqx-mcp-admin` —— ClusterIP `:8080` |

唯一刻意的差異是 `keepOnUninstall` 加上的 `helm.sh/resource-policy: keep`
annotation。`scripts/check-drift.sh` 會證明這一點，CI 每次 push 都會跑它。

這個 chart 刻意**不包含**：

- **Cloudflare Tunnel。** `cloudflared` 在自己的 `cloudflare` namespace，一條
  tunnel 服務多個主機名；同一個 token 再起一組 connector 會把流量拆開。
  `emqx-mcp.woowtech.io` 的路由在那邊，指向
  `http://emqx-mcp-admin.emqx-mcp.svc.cluster.local:8080`。
- **Cloudflare Worker**（[`cloudflare/`](../../cloudflare)）。那兩支用 `wrangler`
  部署，不走 Helm。
- **broker 本身。** 這個 console 透過 REST API 連外部的 EMQX。要自己的 broker 請看
  [WOOWTECH/Woow_k3s_emqx](https://github.com/WOOWTECH/Woow_k3s_emqx)。

## 三個 Secret

`k8s-deploy.yaml` 引用了三個 Secret，一個都沒建立；README 甚至寫說 manifest 會
建立其中一個。它們是在叢集上手動建的，git 裡沒有任何紀錄。這個 chart 兩件事一起修：

| Secret | key | 用途 |
|--------|-----|------|
| `emqx-mcp-config` | `config.json` | init container 複製到 `/data/config.json`：admin 密碼、MCP path token、EMQX API 憑證、工具開關 |
| `emqx-mcp-jwt` | `secret` | `JWT_SECRET`，簽發管理端 session |
| `ghcr-pull` | `.dockerconfigjson` | private GHCR package 的拉取憑證 |

`secrets.create` 預設是 `false`：chart 只「引用」它們，所以 `helm upgrade` 不可能
用空值蓋掉線上憑證。它們的完整形狀（占位字）在
[`examples/secrets.example.yaml`](examples/secrets.example.yaml) —— 複製到 repo
外面、填好、再 apply。

設 `secrets.create=true` 就改由 chart 從 values 渲染這三個（全新安裝和測試用）。
這時每個值都有 `required()` 守門，打錯字會讓渲染失敗，而不是產生一個半殘的 Secret。

## 安裝

從 clone：

```bash
git clone https://github.com/WOOWTECH/Woow_emqx_mcp_server.git
cd Woow_emqx_mcp_server

# 先建 Secret（見 examples/secrets.example.yaml）
kubectl create namespace emqx-mcp
kubectl -n emqx-mcp apply -f /secure/path/emqx-mcp-secrets.yaml

helm install emqx-mcp charts/emqx-mcp -n emqx-mcp --create-namespace
```

不 clone，直接用 GitHub tarball：

```bash
curl -sSL https://github.com/WOOWTECH/Woow_emqx_mcp_server/archive/refs/heads/main.tar.gz | tar -xz
helm install emqx-mcp Woow_emqx_mcp_server-main/charts/emqx-mcp \
  -n emqx-mcp --create-namespace
```

或是全新安裝時讓 chart 順便建 Secret：

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

## 主要 values

| Value | 預設 | 說明 |
|-------|------|------|
| `namespace.create` / `namespace.name` | `true` / `""`（沒設就跟 `-n`） | 每個帶 namespace 的物件都渲染進 `namespace.name`，留空就用 `-n`——所以單純 `-n <ns>` 安裝一定只會動到 `<ns>`。只有要重現 `k8s-deploy.yaml` 寫死的 `emqx-mcp`，或 takeover 進一個跟 `-n` 不同的 namespace 時才需要明確設它。等於實際 namespace 的 Namespace 永遠不渲染，`helm uninstall` 就刪不掉它 |
| `keepOnUninstall` | `true` | 幫 Namespace 和 chart 建的 Secret 加上 `helm.sh/resource-policy: keep` |
| `image.repository` / `.tag` | `ghcr.io/woowtech/woow-emqx-mcp-admin` / `v1.0.0` | 手動建的映像，見 `k8s-deploy.yaml` 檔頭 |
| `imagePullSecret` | `ghcr-pull` | GHCR package 改成 public 後設成 `""`，pod spec 裡的 `imagePullSecrets` 就會整個消失 |
| `config.secretName` | `emqx-mcp-config` | 必須含 key `config.json` |
| `jwt.secretName` / `.secretKey` / `.expiryHours` | `emqx-mcp-jwt` / `secret` / `24` | |
| `probes.readiness` / `probes.liveness` | `10`/`10`、`30`/`30` | 原樣渲染在 `httpGet: /healthz:8080` 底下；要加 `timeoutSeconds` 就加在這裡 |
| `resources` | 100m/192Mi → 1000m/768Mi | |
| `service.type` / `.port` | `ClusterIP` / `8080` | |
| `service.labels` | `{}` | 只加在 Service 物件上的額外 label（不是 selector、也不是 pod template）。留空時渲染結果就完全等於 `k8s-deploy.yaml`；線上那個 instance 會設 `app.kubernetes.io/name`，因為跑著的 Service 帶著它 —— 見「接管現有的部署」 |
| `hardening.enabled` | `false` | 選配的 `securityContext` + `automountServiceAccountToken: false`。**會改到 pod template，所以會重啟 pod** |
| `tools.*` | 全開 | 只有 `secrets.create=true` 時才會寫進 `config.json` |
| `secrets.create` | `false` | 見上 |
| `tests.enabled` | `true` | `helm test` 煙霧測試 pod |
| `tests.requireBrokerHealthy` | `false` | broker 本身連不到時讓煙霧測試失敗 |

## 驗證

```bash
kubectl -n emqx-mcp rollout status deploy/emqx-mcp-admin --timeout=5m
helm test emqx-mcp -n emqx-mcp --logs
```

煙霧測試 pod 是唯讀的，依序檢查：`/healthz` 回 200；`/` 有送出建好的 SPA；沒帶 token
的 `/api/health` 回 401；用 Secret 裡的 admin 密碼可以登入；帶那把 JWT 的
`/api/health` 回報 `app_type=emqx` 而且 MCP 子行程在跑；`/private_<mcp_auth_token>/mcp/`
的 `initialize` 回 200；錯的 path token 被 403 擋掉。

手動測（沒有 Ingress 也沒有 NodePort）：

```bash
kubectl -n emqx-mcp port-forward svc/emqx-mcp-admin 8080:8080
curl -s localhost:8080/healthz          # {"status":"ok"}
open http://localhost:8080              # GUI，用 admin_password 登入
```

對 manifest（以及選配的叢集）做 drift 檢查：

```bash
charts/emqx-mcp/scripts/check-drift.sh
CONTEXT=default NAMESPACE=emqx-mcp charts/emqx-mcp/scripts/check-drift.sh
```

### 在本機重跑 CI 的檢查

`.github/workflows/chart.yml` 把工具版本釘死了，所以回報結果時請一起寫上版本 ——
本機換一個 binary，數字就可能不一樣。

```bash
helm version                 # CI 釘 v3.19.5（azure/setup-helm@v4）
kubeconform -v               # CI 釘 v0.6.7
```

要拿到跟 CI 同一版的 kubeconform，而不是 `$PATH` 上剛好那一版：

```bash
curl -sSL https://github.com/yannh/kubeconform/releases/download/v0.6.7/kubeconform-linux-amd64.tar.gz \
  | tar -xz -C /usr/local/bin kubeconform
helm lint charts/emqx-mcp
kubeconform -strict -summary <各種 values 組合算出來的 render>
# 7 種組合（default-ns、explicit-ns、instance、production、
# secrets.create=true、hardening、minimal）
#   -> 24 resources found in 7 files - Valid: 24, Invalid: 0, Errors: 0
```

這個 chart 目前用 v0.6.7 和 v0.8.0 跑都是 24/24，但只有 v0.6.7 是 CI 會重現的數字。

template 裡不可以寫死 namespace。CI 會檢查，你自己也可以驗：渲染出來的每一行
`namespace:` 都必須等於 `-n` 給的值。

```bash
helm template t charts/emqx-mcp -n SENTINEL --skip-tests | grep '^  namespace:'
#   namespace: SENTINEL
#   namespace: SENTINEL
```

## 移除 —— 資料會留著

```bash
helm uninstall emqx-mcp -n emqx-mcp
```

`keepOnUninstall: true`（預設）時，Namespace 和 chart 建立的每個 Secret 都會留下來，
admin 密碼、MCP token、EMQX API 憑證不會被毀掉。這個 chart 沒有 PVC：`/data` 是
`emptyDir`，Secret 才是真實來源。你自己在外面建的 Secret 從來就不屬於這個 release，
兩種情況下都不會被動到。

真的要連憑證一起刪，就在之後自己明確刪掉。

### 重新裝回同一個 namespace

留著的 Secret 比 release 活得久，但也保留了當初 release 給它的 ownership metadata
（`app.kubernetes.io/managed-by: Helm`、`meta.helm.sh/release-name`、
`meta.helm.sh/release-namespace`）。所以之後再安裝會發生什麼，取決於你怎麼裝 ——
以下四種都在真的叢集上用 helm v3.19.5 驗過：

| 重裝方式 | 結果 |
|----------|------|
| 同 release 名稱、同 namespace，`secrets.create=false`（預設） | 成功。chart 只引用已經在那裡的 Secret，admin 密碼、MCP token 和 EMQX 憑證原封不動，pod 起來還是同一組登入。 |
| 同 release 名稱、同 namespace，`secrets.create=true` | 成功 —— 而且會**默默覆蓋**留著的 Secret，寫進你傳的值。要傳一模一樣的值，否則等於無意間換了憑證。 |
| 換 release 名稱（或換 namespace），`secrets.create=true` | 在建立任何東西之前就被擋下來：留著的 Secret 上面寫的還是舊 release（見下）。 |
| 蓋在你照 `examples/secrets.example.yaml` 手動建立的 Secret 上，`secrets.create=true` | 同樣被擋：手動建的 Secret 根本沒有 Helm metadata。這就是 `secrets.create` 預設為 `false` 的主因。 |

被擋下來時長這樣：

```
Error: INSTALLATION FAILED: Unable to continue with install: Secret "emqx-mcp-config"
in namespace "emqx-mcp" exists and cannot be imported into the current release:
invalid ownership metadata; label validation error: missing key
"app.kubernetes.io/managed-by": must be set to "Helm"; ...
```

這是 keep 政策正常運作，不是 bug。用 `secrets.create=false` 重裝（憑證留著），
或者你真的要讓 chart 接手並重寫它們，就先刪掉 —— 刪掉等於毀掉憑證：

```bash
kubectl -n emqx-mcp delete secret emqx-mcp-config emqx-mcp-jwt ghcr-pull
```

`namespace.create=true` 且 Namespace 不等於 release namespace 時，規則一樣。

## 接管現有的部署

線上的資源是 `kubectl apply -f k8s-deploy.yaml` 建的，沒有 Helm 的 ownership
metadata。`--take-ownership` 可以原地收編：

```bash
helm --kube-context default upgrade --install emqx-mcp charts/emqx-mcp \
  -n emqx-mcp --take-ownership \
  -f charts/emqx-mcp/deploy/local-k3s/emqx-mcp.yaml
```

`deploy/local-k3s/emqx-mcp.yaml` 是這個 instance 的 values（不含機密）。帶上它，
`helm template` 渲染出的 Deployment 和 `k8s-deploy.yaml` **逐欄位相同**（整個 pod
template 都包含在內），所以收編不會改到 pod template 的任何欄位，什麼都不會重啟。
接管時請保持 `hardening.enabled=false`，那是唯一會讓 pod 重啟的開關。

線上叢集有兩個物件帶著 `k8s-deploy.yaml` 從來沒宣告過的 label（git 歷史裡沒有任何一
版有，是在外面手動加上去的）：

| 線上物件 | 多出來的 label | 接管時會發生什麼 |
| --- | --- | --- |
| `Service/emqx-mcp-admin` | `app.kubernetes.io/name: emqx-mcp-admin` | 會留著：instance values 的 `service.labels` 重現了它。沒有這個值的話 upgrade 會把 label 拿掉 —— 無害（Service 的 label 不屬於 selector，什麼都不會重啟），但是一個無聲的變更。 |
| `Namespace/emqx-mcp` | `purpose: e2e` | 不會被動到：這個 Namespace 等於 release 的 namespace，chart 根本不會渲染那個物件。 |

也就是說，對 Deployment 和 Service 來說「和 `k8s-deploy.yaml` 相同」與「和線上跑的
相同」是同一句話；而這個 chart 刻意不重現線上的 Namespace。

另外 Helm 自己會在每個收編的物件上蓋上 `app.kubernetes.io/managed-by: Helm` label
和 `meta.helm.sh/release-name`、`meta.helm.sh/release-namespace` annotation。那是
Helm 記錄 ownership 的方式，沒有任何 template 會渲染它，而且只會加在物件自己的
metadata 上、不會碰到 pod template，所以同樣不會造成重啟。

截至 2026-09-12 這個部署是停擺的 —— pod 排到的節點 `NotReady`、`cloudflared` 0/1 ——
所以接管要先有一個健康的叢集。

## 這個 chart 沒修的後續事項

這些需要重啟 pod、改程式或做決策，不在「一比一轉成 chart」的範圍內：

1. **`hardening.enabled=true` 是關的。** 線上容器以 root 執行、沒有
   `securityContext`，還自動掛了一個程式根本用不到的 ServiceAccount token。
   這個開關已經在拋棄式 namespace 驗證過 —— pod 會以 uid 1000 起來、
   `automountServiceAccountToken: false`、capabilities 全部 drop，完整的煙霧測試
   照樣通過 —— 但打開它會改到 pod template、因此會重啟 pod，所以請挑維護時間做，
   不要放在接管那一步。
2. **`/data` 是 `emptyDir`。** 每次重啟都用 Secret 重新覆寫 `config.json`，在 GUI
   輪替過的 token 會無聲無息地被改回去。要修就得把 GUI 的變更寫回 Secret，或是改成
   PVC 加上 `setdefault` 式的 seeding（像 `Woow_k3s_litellm` 那樣）。
3. **映像 tag 可覆寫而且是 `IfNotPresent`。** `v1.0.0` 被反覆重建、沒有 digest pin，
   不同節點可能在同一個 tag 下跑到不同的程式。請 pin digest，或用 CI 發不可變的 tag。
4. **frontend 建置不可重現。** 沒有 `frontend/package-lock.json`，所以
   `npm install --include=dev` 當天解析到什麼就是什麼：同一份 `Dockerfile` 今天可以
   乾淨建起來（`vite v6.4.3`、1642 個模組），但解析結果會漂移，而且至少壞過一次
   （解析到一個建不起來的 `react-router`，`Rollup failed to resolve import "cookie"`）。
   補一個 lockfile 就能把它釘住。
5. **`cloudflare/mcp-direct.js` 的 `/mcp` 會由 edge 注入上游 token。** 任何人連到那個
   主機名，就有一條免驗證、CORS 全開的路可以呼叫全部 39 個工具，包含破壞性的那些。
   請改上 OAuth gateway，或把 `/mcp` 別名拿掉。
6. **repo 裡還有 `change-me` 之類的預設值。** `mcp_admin_core/config/store.py`
   會 fallback 到 `admin_password: "admin"`，`docker-compose.yml` 和 `.env.example`
   則帶著 `JWT_SECRET=change-me`。chart 從不使用它們（`required()` 讓每個憑證都必須
   明確給定），但 Docker 那幾條路徑會踩到。
