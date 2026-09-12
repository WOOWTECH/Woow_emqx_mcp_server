{{/*
Helper templates for the emqx-mcp chart.

Resource names, labels and selectors are fixed (not derived from the release
name): the Cloudflare tunnel routes to the exact Service name
emqx-mcp-admin.emqx-mcp:8080, and changing a selector or a pod-template label
would recreate the ReplicaSet and restart the pod.
*/}}

{{/*
The namespace every namespaced object is rendered into. Defaults to the
release namespace (`-n`) so a plain `helm install foo charts/emqx-mcp -n bar`
is always safe and never silently escapes to a different namespace; only the
documented live instance (deploy/local-k3s/emqx-mcp.yaml) sets
namespace.name explicitly, to the same value it already installs with `-n`.
*/}}
{{- define "emqx-mcp.ns" -}}
{{ .Values.namespace.name | default .Release.Namespace }}
{{- end -}}

{{/* Shared label on the Namespace and the Deployment. */}}
{{- define "emqx-mcp.partOf" -}}
app.kubernetes.io/part-of: woow-emqx-mcp
{{- end -}}

{{/* The pod-template / selector label. Never change it: it is immutable on
     the Deployment and changing it restarts the pod. */}}
{{- define "emqx-mcp.selectorLabel" -}}
app.kubernetes.io/name: emqx-mcp-admin
{{- end -}}

{{/* `annotations:` block with the keep policy, or nothing. */}}
{{- define "emqx-mcp.keepAnnotations" -}}
{{- if .Values.keepOnUninstall -}}
annotations:
  helm.sh/resource-policy: keep
{{- end -}}
{{- end -}}

{{- define "emqx-mcp.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}

{{- define "emqx-mcp.initImage" -}}
{{ .Values.initImage.repository }}:{{ .Values.initImage.tag }}
{{- end -}}

{{/*
config.json for the emqx-mcp-config Secret, rendered only when
secrets.create=true. The shape is the one documented in README.md
("Configuration"); mcp_server.args match emqx_mcp_server.server's own CLI
(--transport http --host 127.0.0.1 --port 3000), which keeps the MCP server on
loopback so only the in-process proxy can reach it.
*/}}
{{- define "emqx-mcp.configJson" -}}
{{- $s := .Values.secrets -}}
{{- $t := .Values.tools -}}
{{- $cfg := dict
  "admin_password" (required "secrets.adminPassword is required when secrets.create=true" $s.adminPassword)
  "mcp_auth_token" (required "secrets.mcpAuthToken is required when secrets.create=true" $s.mcpAuthToken)
  "connection" (dict
    "emqx_mcp_base_url" (required "secrets.emqx.baseUrl is required when secrets.create=true" $s.emqx.baseUrl)
    "emqx_mcp_api_key" (required "secrets.emqx.apiKey is required when secrets.create=true" $s.emqx.apiKey)
    "emqx_mcp_api_secret" (required "secrets.emqx.apiSecret is required when secrets.create=true" $s.emqx.apiSecret))
  "tools" (dict
    "disabled_tools" $t.disabledTools
    "disabled_categories" $t.disabledCategories
    "disabled_operations" $t.disabledOperations
    "readonly" $t.readonly
    "permissions" (dict "allowed_tools" (list "*") "denied_tools" (list)))
  "mcp_server" (dict
    "command" "python3"
    "args" (list "-m" "emqx_mcp_server.server" "--transport" "http" "--host" "127.0.0.1" "--port" "3000")
    "port" 3000
    "env" (dict
      "EMQX_MCP_READONLY" (ternary "true" "false" $t.readonly)
      "EMQX_MCP_DISABLED_CATEGORIES" (join "," $t.disabledCategories)
      "EMQX_MCP_DISABLED_TOOLS" (join "," $t.disabledTools)
      "EMQX_MCP_DISABLED_OPERATIONS" (toJson $t.disabledOperations)))
  "proxy" (dict "timeout" 86400 "bearer_token" "")
  "token_history" (list)
-}}
{{ toPrettyJson $cfg }}
{{- end -}}
