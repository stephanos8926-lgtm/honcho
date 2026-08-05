# Single-Container Honcho Deployment — RapidWebs Fork

Deploy Honcho (API + Deriver) as a single Podman/Docker container with PostgreSQL
and **local RW InferenceEngine** for embeddings. No Redis required.

## Quick Start

```bash
# 1. Clone the fork
git clone https://github.com/stephanos8926-lgtm/honcho.git
cd honcho

# 2. Configure (REQUIRED: OPENROUTER_API_KEY)
cp deploy/.env.example .env
# Edit .env — paste your OpenRouter key

# 3. Provision everything (idempotent, handles RW IE build + DB + migrations + KG)
./deploy/setup.sh

# 4. Verify
curl http://localhost:8000/health
# Expected: {"status":"ok","deriver":{"status":"healthy",...}}
```

## Architecture (What Actually Runs)

```
┌─────────────────────────────────────────────────────────────────┐
│                        HOST NETWORK                             │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────────┐  │
│  │  honcho-api  │   │  honcho-db   │   │ RW InferenceEngine │  │
│  │  + Deriver   │──▶│  pgvector    │   │  127.0.0.1:8300    │  │
│  │  (container) │   │  (container) │   │  (container)       │  │
│  └──────────────┘   └──────────────┘   └────────────────────┘  │
│        │                                                                 │
│        │ config.toml (bind-mounted from host)                        │
│        ▼                                                                 │
│   Hermes Agent / MCP client / any REST client                        │
└─────────────────────────────────────────────────────────────────┘
```

**Key realities (docs were stale until 2026-08):**
- **host network** — no compose internal DNS, all containers on `127.0.0.1`
- **config.toml is authoritative** — bind-mounted from host (`/tmp/honcho_config.toml` or repo `config.toml`); `.env` only injects secrets
- **RW InferenceEngine is local** — runs on same host at `127.0.0.1:8300` (not srv1)
- **Embeddings are OpenAI-compatible** — string **or** array input, `data[{index, embedding}]` response
- **Deriver runs in-process** — flush-enabled, no batch threshold
- **Dream includes `card_refresh`** — peer cards auto-refresh every dream cycle
- **KG tables exist** — migration `kg_001` rebased onto current head; `kg_entities`, `kg_relationships`, `kg_query_log`

## Environment Variables (`.env`)

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | ✅ | LLM key for Deriver/Dialectic/Summary/Dream |
| `DATABASE_URL` | ⚠️ | PG connection (default works with compose) |
| `EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL` | ⚠️ | RW IE URL (default `http://127.0.0.1:8300/v1`) |

All other settings live in `config.toml` (see `config.toml.example`).

## What Gets Provisioned by `./deploy/setup.sh`

1. **RW InferenceEngine** — builds from local `../RW_InferenceEngine`, runs host-network on 127.0.0.1:8300
2. **PostgreSQL (pgvector:pg15)** — host-network, `honcho-pgdata` volume
3. **config.toml** — binds repo `config.toml` (or example) into container at `/app/config.toml:ro`
4. **honcho-api image** — builds from repo, runs host-network with config bind-mount
5. **Migrations** — `alembic upgrade head` (includes KG tables `kg_001`)
6. **Health + embedding + KG verification** — prints status

Flags:
```bash
./deploy/setup.sh --no-rw-ie      # skip RW IE build (assume existing :8300)
./deploy/setup.sh --verify-only   # only checks
./deploy/setup.sh --reset-db      # WIPE PG volume first (destructive)
```

## Under-Utilized Features (Now Live)

### Peer Card Auto-Refresh
Cards were empty because `DREAM_ENABLED_TYPES` only had `omni`. Now includes `card_refresh` — lightweight dream that only writes/refreshes the peer card (no observation mutation).

```bash
# Manual trigger (or wait for auto-cycle):
curl -X POST http://localhost:8000/v3/workspaces/hermes/schedule_dream \
  -H "Content-Type: application/json" \
  -d '{"observer":"hermes","observed":"sysop","dream_type":"card_refresh"}'
```

Read back:
```bash
curl http://localhost:8000/v3/workspaces/hermes/peers/hermes/card
```

### Knowledge Graph (KG) Endpoints
Tables created by migration `kg_001`. Endpoints:

| Endpoint | Method | Purpose |
|---|---|---|
| `/v3/v3/workspaces/{ws}/kg/entities?q=...` | GET | Search entities by name/alias |
| `/v3/v3/workspaces/{ws}/kg/peer-entities?peer_name=...` | GET | Entities linked to a peer |
| `/v3/v3/workspaces/{ws}/kg/traverse?entity=...` | GET | Graph traversal (depth-limited) |
| `/v3/v3/workspaces/{ws}/kg/subgraph?entity=...` | GET | Subgraph extraction |
| `/v3/v3/workspaces/{ws}/kg/auto-link` | POST | Auto-link entities across peers |

Auto-link scans all peers and links matching aliases (pg_trgm similarity).

### Embedding Backfill & Migration

**Backfill missing message embeddings** (after RW IE fix, 403 were NULL):
```bash
uv run python scripts/generate_message_embeddings.py --workspace-name hermes
```

**Dimension migration** (1536 → 384 or any change):
```bash
# 1. alembic upgrade head     (creates vector(1536) schema)
# 2. uv run python scripts/configure_embeddings.py --yes   (ALTER to target dim)
# 3. restart API              (validators refuse on mismatch)
```

### Message Embedding Reconciliation
Background reconciler runs every 5 min (configurable `VECTOR_STORE_RECONCILIATION_INTERVAL_SECONDS`), claims pending `MessageEmbedding` rows, embeds via RW IE, upserts to pgvector. Never holds DB session during network call.

### Multi-Agent Dialectic Chat
```bash
curl -X POST http://localhost:8000/v3/workspaces/hermes/peers/hermes/chat \
  -H "Content-Type: application/json" \
  -d '{"query":"What does Steven prefer for gateway restarts?","reasoning_level":"medium"}'
```

Levels: `minimal` | `low` | `medium` | `high` | `max` (tool iterations scale).

### Conclusions (Observations) Search
```bash
curl -X POST http://localhost:8000/v3/workspaces/hermes/conclusions/query \
  -H "Content-Type: application/json" \
  -d '{"query":"gateway restart","limit":5}'
```

## Connecting Hermes Agent

In `~/.hermes/config.yaml`:
```yaml
memory:
  provider: honcho
honcho:
  base_url: http://<honcho-host>:8000
```

Hermes uses `provider: honcho` with the above config — no separate plugin needed.

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `health: down` | PG not ready / config.toml missing | `./deploy/setup.sh` or check bind mount |
| Embeddings 422/NULL | RW IE not OpenAI-compatible | `./deploy/setup.sh --no-rw-ie` rebuilds RW IE |
| KG endpoints 500 | `kg_001` migration not applied | `alembic upgrade head` inside container |
| Peer cards empty | `card_refresh` not in dream types | Verify `config.toml` has `ENABLED_TYPES = ["omni", "card_refresh"]` |
| "text = integer" on embed | ORM type mismatch (message_id is TEXT) | Use `message_id` not `id` when calling embed scripts |

## File Layout

```
deploy/
├── setup.sh                    # 👉 MAIN provisioning script (idempotent)
├── docker-compose.single-container.yml
├── .env.example
├── README.md                   # (this file)
├── config.toml.example         # authoritative config template
docker/
├── entrypoint.sh               # alembic → fastapi
├── grafana-datasource.yml
└── prometheus.yml
```

## Version Compatibility

| Component | Version |
|---|---|
| Honcho API | fork `stephanos8926-lgtm/honcho` (main) |
| PostgreSQL | pgvector/pgvector:pg15 |
| RW InferenceEngine | local build (`rw_inference` transport, 384-dim) |
| Deriver | in-process, flush-enabled |

---

**Last verified:** 2026-08-05 (all endpoints green, 405/405 embeddings synced, peer cards auto-refreshing, KG tables live)