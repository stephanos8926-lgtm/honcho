# Single-Container Honcho Deployment (RapidWebs Fork)

Deploy Honcho (API + Deriver) as a single Podman container with PostgreSQL
and RW InferenceEngine for embeddings. No Redis required.

## Quick Start

```bash
# 1. Clone the fork
git clone https://github.com/stephanos8926-lgtm/honcho.git
cd honcho

# 2. Configure environment
cp deploy/.env.example .env
# Edit .env: set LLM_GEMINI_API_KEY and DATABASE_URL

# 3. Start
docker compose -f deploy/docker-compose.single-container.yml up -d

# 4. Verify
curl http://localhost:8000/health
# Expected: {"status":"ok","deriver":{"status":"healthy",...}}
```

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Honcho API  │────▶│  PostgreSQL  │     │ RW InferenceEngine│
│  + Deriver   │     │  (pgvector)  │     │ (srv1:8300)      │
│  (container) │     │  (container) │     │ (external)       │
└─────────────┘     └──────────────┘     └──────────────────┘
       │
       │ (port 8000)
       ▼
   Hermes Agent (workstation)
   memory.provider: honcho
   honcho.base_url: http://<host>:8000
```

## Environment Variables

See `.env.example` for all options. Key variables:

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `LLM_GEMINI_API_KEY` | Gemini API key for Deriver/Dialectic | Required |
| `EMBEDDING_MODEL_CONFIG__TRANSPORT` | Embedding provider | `rw_inference` |
| `DERIVER_IN_PROCESS_MODE` | Single-container mode | `true` |
| `EMBEDDING_VECTOR_DIMENSIONS` | Embedding dimension | `384` |

## Embedding Migration (Existing Deployments)

If restoring from an existing Honcho backup that used 1536-dim embeddings
(e.g., Gemini or OpenAI), you must migrate the embedding columns:

```bash
# 1. Set new dimension
export EMBEDDING_VECTOR_DIMENSIONS=384

# 2. Run the migration script
uv run python scripts/configure_embeddings.py --yes

# 3. Re-embed existing messages
# This happens automatically as the Deriver processes messages.
# For bulk re-embedding, set EMBED_MESSAGES=true and restart.
```

**Note:** This is a destructive operation — existing 1536-dim vectors become
unusable until messages are re-embedded.

## Connecting Hermes Agent

In Hermes config.yaml:
```yaml
memory:
  provider: honcho
honcho:
  base_url: http://<honcho-host>:8000
```
