#!/usr/bin/env bash
# Two drift checks for charts/emqx-mcp. Exit 0 = in sync, 1 = drift.
#
#   1. chart vs k8s-deploy.yaml  (always)
#      The chart must render exactly the objects the hand-written manifest at
#      the repository root declares. The ONLY tolerated difference is the
#      `helm.sh/resource-policy: keep` annotation that keepOnUninstall adds.
#      Run with no -f for this check. Adding
#      `-f deploy/local-k3s/emqx-mcp.yaml` reports one expected extra
#      difference - the Service label the live object carries but the manifest
#      never declared (see that file) - so check 1 is not meant to be run that
#      way; use check 2 against the cluster instead.
#
#   2. chart vs a live cluster   (only when CONTEXT is set)
#      kubectl diff of the render against the running objects.
#
#   scripts/check-drift.sh
#   CONTEXT=default NAMESPACE=emqx-mcp scripts/check-drift.sh
#
# Extra arguments are passed to `helm template`, e.g. -f my-values.yaml.
set -euo pipefail

RELEASE="${RELEASE:-emqx-mcp}"
NAMESPACE="${NAMESPACE:-emqx-mcp}"
cd "$(dirname "$0")/.."
MANIFEST="${MANIFEST:-../../k8s-deploy.yaml}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# -n must differ from namespace.name, otherwise the Namespace is deliberately
# not rendered and there would be nothing to compare it with.
helm template "$RELEASE" . -n default --skip-tests "$@" > "$tmp/chart.yaml"

rc=0
if python3 - "$MANIFEST" "$tmp/chart.yaml" <<'PY'
import sys, yaml

IGNORED_ANNOTATIONS = {"helm.sh/resource-policy"}


def load(path):
    out = {}
    for doc in yaml.safe_load_all(open(path)):
        if not doc:
            continue
        ann = (doc.get("metadata") or {}).get("annotations") or {}
        for key in list(ann):
            if key in IGNORED_ANNOTATIONS:
                del ann[key]
        if not ann:
            (doc.get("metadata") or {}).pop("annotations", None)
        meta = doc["metadata"]
        out[(doc["kind"], meta["name"], meta.get("namespace", ""))] = doc
    return out


def walk(path, a, b, diffs):
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                diffs.append(f"{path}.{key}: only in chart -> {b[key]!r}")
            elif key not in b:
                diffs.append(f"{path}.{key}: only in manifest -> {a[key]!r}")
            else:
                walk(f"{path}.{key}", a[key], b[key], diffs)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{path}: length {len(a)} (manifest) vs {len(b)} (chart)")
        for i, (x, y) in enumerate(zip(a, b)):
            walk(f"{path}[{i}]", x, y, diffs)
    elif a != b:
        diffs.append(f"{path}: {a!r} (manifest) != {b!r} (chart)")


manifest, chart = load(sys.argv[1]), load(sys.argv[2])
diffs = []
for key in sorted(set(manifest) | set(chart)):
    name = "/".join(p for p in key if p)
    if key not in manifest:
        diffs.append(f"{name}: only rendered by the chart")
    elif key not in chart:
        diffs.append(f"{name}: only in the manifest")
    else:
        walk(name, manifest[key], chart[key], diffs)
print(f"compared {len(manifest)} manifest objects against {len(chart)} rendered objects")
if diffs:
    print("\n".join(diffs))
    sys.exit(1)
PY
then
  echo "1. chart == $MANIFEST"
else
  echo "1. DRIFT: the chart no longer renders $MANIFEST"
  rc=1
fi

if [ -n "${CONTEXT:-}" ]; then
  helm template "$RELEASE" . -n "$NAMESPACE" --skip-tests \
    --set namespace.create=false "$@" > "$tmp/live-shape.yaml"
  set +e
  kubectl --context "$CONTEXT" diff -f "$tmp/live-shape.yaml" > "$tmp/live.diff" 2>&1
  krc=$?
  set -e
  case "$krc" in
    0) echo "2. cluster == chart (context ${CONTEXT})" ;;
    1) echo "2. DRIFT: live objects differ from the chart:"; cat "$tmp/live.diff"; rc=1 ;;
    *) cat "$tmp/live.diff" >&2; exit "$krc" ;;
  esac
else
  echo "2. skipped (set CONTEXT to diff against a cluster)"
fi
exit "$rc"
