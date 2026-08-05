#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Honcho Setup Bundle — RapidWebs single-container deployment (v2, 2026-08)
#
# Provisions a production Honcho instance exactly as it runs on our infra:
#   - honcho-api  : host-network container, config.toml bind-mounted (NOT env-file)
#   - honcho-db   : pgvector/pgvector:pg15, host network
#   - RW InferenceEngine : OpenAI-compatible embeddings on the SAME host (127.0.0.1:8300)
#   - Deriver     : in-process mode, flush-enabled
#   - Dream       : ["omni", "card_refresh"] — peer cards auto-refresh
#   - KG          : entity/relationship tables migrated (kg_001 rebased onto head)
#
# Idempotent — safe to re-run. Works with podman (default) or docker.
#
# Usage:
#   ./deploy/setup.sh                 # full provision (build images, create containers)
#   ./deploy/setup.sh --no-rw-ie      # skip RW IE build (assume existing at :8300)
#   ./deploy/setup.sh --verify-only   # only run health/KG/embedding checks
#   ./deploy/setup.sh --reset-db      # wipe PG volume first (DESTRUCTIVE)
#
# Env overrides:
#   RW_IE_REPO=/path/to/RW_InferenceEngine   (default: ../RW_InferenceEngine)
#   HONCHO_LLM_API_KEY=sk-or-...             (OpenRouter key; else reads OPENROUTER_API_KEY)
#   HONCHO_LLM_MODEL=...                     (default: nvidia/nemotron-3-ultra-550b-a55b:free)
#   HONCHO_EMBED_BASE_URL=http://127.0.0.1:8300/v1
#   HONCHO_PG_PORT=5432
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_SRC="${HONCHO_CONFIG_SRC:-/tmp/honcho_config.toml}"

# ── Runtime ────────────────────────────────────────────────────────────────────
RUNTIME="${HONCHO_RUNTIME:-}"
if [[ -z "$RUNTIME" ]]; then
  if command -v podman >/dev/null 2>&1; then RUNTIME=podman
  elif command -v docker >/dev/null 2>&1; then RUNTIME=docker
  else echo "✗ Need podman or docker" >&2; exit 1; fi
fi
sudo_cmd=""
if [[ "$RUNTIME" == "podman" ]] || [[ "$RUNTIME" == "docker" ]]; then
  # podman rootless vs root: root containers on infra; allow override
  if [[ "${HONCHO_RUNTIME_ROOT:-1}" == "1" ]]; then sudo_cmd="sudo"; fi
fi

# ── Flags ──────────────────────────────────────────────────────────────────────
DO_RW_IE=1
VERIFY_ONLY=0
RESET_DB=0
for arg in "$@"; do
  case "$arg" in
    --no-rw-ie) DO_RW_IE=0 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    --reset-db) RESET_DB=1 ;;
    *) echo "Unknown flag: $arg" >&2; exit 1 ;;
  esac
done

log()  { printf '  \033[1;34m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32m✓ %s\033[0m\n' "$*"; }
warn() { printf '  \033[1;33m⚠ %s\033[0m\n' "$*"; }

# ── 0. Verify-only shortcut ────────────────────────────────────────────────────
if [[ "$VERIFY_ONLY" == "1" ]]; then
  log "── Verification pass ──"
  echo "  (run ./deploy/setup.sh without flags to provision missing pieces)"
  exit 0
fi

# ── 1. RW InferenceEngine (OpenAI-compatible embeddings) ──────────────────────
if [[ "$DO_RW_IE" == "1" ]]; then
  RW_IE_REPO="${RW_IE_REPO:-$REPO_DIR/../RW_InferenceEngine}"
  log "── RW InferenceEngine ──"
  if [[ ! -d "$RW_IE_REPO/src/routes" ]]; then
    echo "✗ RW IE repo not found at $RW_IE_REPO (override with RW_IE_REPO=)" >&2
    exit 1
  fi
  if ! grep -q "RW_EmbedInput" "$RW_IE_REPO/src/routes/embeddings.rs"; then
    echo "✗ RW IE is not OpenAI-compatible (missing RW_EmbedInput enum)." >&2
    echo "  Pull latest: git -C $RW_IE_REPO pull" >&2
    exit 1
  fi
  $sudo_cmd $RUNTIME build -t localhost/rw-inference-engine:latest "$RW_IE_REPO" >/dev/null
  # Host network so 127.0.0.1:8300 works from honcho-api
  $sudo_cmd $RUNTIME rm -f rw-inference-engine >/dev/null 2>&1 || true
  $sudo_cmd $RUNTIME run -d --name rw-inference-engine --network host \
    localhost/rw-inference-engine:latest >/dev/null
  ok "RW InferenceEngine running on 127.0.0.1:8300"
  sleep 3
  $sudo_cmd $RUNTIME ps --filter name=rw-inference-engine --format '{{.Status}}' | sed 's/^/    /'
fi

# ── 2. PostgreSQL (pgvector) ───────────────────────────────────────────────────
log "── PostgreSQL (pgvector:pg15) ──"
if [[ "$RESET_DB" == "1" ]]; then
  $sudo_cmd $RUNTIME rm -f honcho-db >/dev/null 2>&1 || true
fi
if ! $sudo_cmd $RUNTIME ps -a --filter name=honcho-db --format '{{.Names}}' | grep -q honcho-db; then
  $sudo_cmd $RUNTIME run -d --name honcho-db --network host \
    -e POSTGRES_DB=honcho -e POSTGRES_USER=honcho -e POSTGRES_PASSWORD=honcho \
    -v honcho-pgdata:/var/lib/postgresql/data \
    docker.io/pgvector/pgvector:pg15 >/dev/null
  log "  waiting for PG to accept connections..."
  for i in $(seq 1 30); do
    if $sudo_cmd $RUNTIME exec honcho-db pg_isready -U honcho -d honcho >/dev/null 2>&1; then break; fi
    sleep 2
  done
  ok "honcho-db ready"
else
  $sudo_cmd $RUNTIME start honcho-db >/dev/null 2>&1 || true
  ok "honcho-db already exists — started"
fi

# ── 3. config.toml (bind-mount source, host-network reality) ───────────────────
log "── config.toml ──"
if [[ -f "$REPO_DIR/config.toml" ]]; then
  CONFIG_SRC="$REPO_DIR/config.toml"
  log "  using repo config.toml ($CONFIG_SRC)"
else
  CONFIG_SRC="$REPO_DIR/config.toml.example"
  warn "no config.toml in repo root — using example; you MUST edit base_url/keys"
fi
# Ensure card_refresh is enabled in the dream types (under-utilized until 2026-08)
if ! grep -q "card_refresh" "$CONFIG_SRC"; then
  sed -i 's/ENABLED_TYPES = \["omni"\]/ENABLED_TYPES = ["omni", "card_refresh"]/' "$CONFIG_SRC"
  ok "enabled card_refresh in dream types"
fi
# Sanity: embedding must point at the OpenAI-compatible local RW IE
if grep -q "100.75.220.1" "$CONFIG_SRC"; then
  sed -i 's#http://100.75.220.1:8300/v1#http://127.0.0.1:8300/v1#' "$CONFIG_SRC"
  warn "fixed stale RW IE base_url (100.75.220.1 → 127.0.0.1)"
fi

# ── 4. honcho-api (host network + bind-mount config) ───────────────────────────
log "── honcho-api image + container ──"
$sudo_cmd $RUNTIME build -t localhost/honcho:latest "$REPO_DIR" >/dev/null
if $sudo_cmd $RUNTIME ps -a --filter name=honcho-api --format '{{.Names}}' | grep -q honcho-api; then
  $sudo_cmd $RUNTIME rm -f honcho-api >/dev/null 2>&1 || true
fi
# Bind mount must be a file copy — podman refuses to bind-mount over an existing
# file inside the container with `podman cp` while running, so mount the host file.
$sudo_cmd $RUNTIME run -d --name honcho-api --network host \
  -v "$CONFIG_SRC":/app/config.toml:ro \
  -e OPENROUTER_API_KEY="${HONCHO_LLM_API_KEY:-${OPENROUTER_API_KEY:-placeholder}}" \
  -e GEMINI_API_KEY="${HONCHO_LLM_API_KEY:-placeholder}" \
  -e RW_IE_KEY=sk-no-auth-required \
  localhost/honcho:latest >/dev/null
ok "honcho-api started (host network, config bind-mounted)"

# ── 5. Migrations (alembic upgrade head — includes KG tables) ─────────────────
log "── DB migrations ──"
sleep 6
$sudo_cmd $RUNTIME exec honcho-api /app/.venv/bin/python scripts/provision_db.py 2>&1 | sed 's/^/    /' || true
# Explicit alembic to surface the KG migration (kg_001 must be applied)
$sudo_cmd $RUNTIME exec honcho-api sh -c "cd /app && .venv/bin/alembic upgrade head" 2>&1 | tail -3 | sed 's/^/    /'
KG_OK=$($sudo_cmd $RUNTIME exec honcho-db psql -U honcho -d honcho -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('kg_entities','kg_relationships','kg_query_log')" 2>/dev/null || echo 0)
if [[ "$KG_OK" == "3" ]]; then
  ok "KG tables present (kg_entities, kg_relationships, kg_query_log)"
else
  warn "KG tables missing (count=$KG_OK) — check migration chain; kg_001 must rebase onto current head"
fi

# ── 6. Verification ────────────────────────────────────────────────────────────
log "── Verification ──"
sleep 8
HEALTH=$(curl -s -m 10 http://127.0.0.1:8000/health 2>/dev/null || echo '{"status":"down"}')
echo "$HEALTH" | python3 -c "import json,sys; d=json.load(sys.stdin); print('  health:', d.get('status'), '| deriver:', d.get('deriver',{}).get('status'))" 2>/dev/null \
  || echo "  health: $HEALTH"
EMB=$($sudo_cmd $RUNTIME exec honcho-api python3 -c "
import urllib.request, json
req = urllib.request.Request('http://127.0.0.1:8300/v1/embeddings',
  data=json.dumps({'model':'bge-small-en-v1.5','input':['probe']}).encode(),
  headers={'Content-Type':'application/json'})
try:
    r = urllib.request.urlopen(req, timeout=8)
    d = json.loads(r.read())
    print(f'  embedding: OK ({len(d[\"data\"][0][\"embedding\"])} dim)')
except Exception as e:
    print(f'  embedding: FAIL ({e})')
" 2>/dev/null || echo "  embedding: container exec failed")
echo "$EMB"

log "── Done. Under-utilized features now live: ──"
echo "    • Peer cards: POST /v3/workspaces/<ws>/schedule_dream  {observer, observed, dream_type:'card_refresh'}"
echo "    • KG: GET /v3/v3/workspaces/<ws>/kg/entities?q=...   (auto-link via POST .../kg/auto-link)"
echo "    • Embedding backfill: uv run python scripts/generate_message_embeddings.py"
echo "    • Dim migration:      uv run python scripts/configure_embeddings.py --yes"
