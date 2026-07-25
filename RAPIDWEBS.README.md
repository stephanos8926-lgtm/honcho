# ═══════════════════════════════════════════════════════════════
# RAPIDWEBS — Specialized Fork of Honcho
# ═══════════════════════════════════════════════════════════════
#
# Custom Change Log — Unique Changes Independent to Our Fork
#
# This file tracks every custom change, improvement, patch, and
# bug-fix that we make to Honcho beyond the upstream plastic-labs
# source. It is the single source of truth for what we've changed
# and why.
#
# Format:
#   [YYYY-MM-DD] CATEGORY: Description
#       - Files modified
#       - Rationale
#       - Configuration implications
#
# ═══════════════════════════════════════════════════════════════

## P0 — Critical Bugfixes & Self-Hosted Defaults

### [2026-07-25] DERIVER_FLUSH_ENABLED default changed to True

**Files:**
- `src/config.py:888` — `DeriverSettings.FLUSH_ENABLED`

**What changed:**
Changed the default value of `FLUSH_ENABLED` from `False` to `True` in the
`DeriverSettings` class.

**Rationale:**
The upstream default (`False`) batches representation work units until they
accumulate 1024 tokens before making them claimable by the Deriver worker.
In a low-volume self-hosted deployment (single user, tens to hundreds of
messages), this threshold is rarely crossed — meaning observations silently
never get written, peer cards stay empty, and the entire memory pipeline
appears broken.

The community has confirmed this as the #1 self-hosted footgun (see
plastic-labs/honcho#494). Setting `FLUSH_ENABLED=true` makes the Deriver
process work units as soon as any new messages arrive, regardless of size.

**Trade-off:**
- Pro: Memory works immediately in low-volume deployments
- Con: Slightly more LLM API calls (one per message instead of batched)
- For self-hosted deployments the latency/cost trade-off strongly favors
  immediate flushing

**Upstream default:** `False`
**Our default:** `True`

---

### [2026-07-25] Added "rw_inference" as a named embedding transport type

**Files:**
- `src/config.py:26` — `EmbeddingTransport` literal type
- `src/config.py:35-38` — `_default_embedding_model_for_transport()`
- `src/config.py:485-490` — `_default_embedding_api_key()`
- `src/embedding_client.py:198-209` — `_EmbeddingClient.__init__()`

**What changed:**
Added `"rw_inference"` as a third embedding transport option alongside
`"openai"` and `"gemini"`. This allows configuring Honcho to use our
RW InferenceEngine server for embeddings.

**Rationale:**
RW InferenceEngine is a Rust/ONNX microservice deployed on our LAN that
serves embeddings (bge-small-en-v1.5, 384-dim) and cross-encoder reranking.
It exposes an OpenAI-compatible API at `http://<host>:8300/v1` with no
API key required (network-level access control only).

Adding it as a named transport rather than configuring it as a generic
OpenAI-compatible endpoint ensures:
1. Proper default model selection (`bge-small-en-v1.5`)
2. Correct dimension handling (384 instead of 1536)
3. No spurious API key validation errors
4. Clean separation from cloud OpenAI usage

**Configuration:**
```env
EMBEDDING_MODEL_CONFIG__TRANSPORT=rw_inference
EMBEDDING_MODEL_CONFIG__MODEL=bge-small-en-v1.5
EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=http://<rw-ie-host>:8300/v1
EMBEDDING_VECTOR_DIMENSIONS=384
# No API key needed
```

**Dimension note:**
The embedding dimension MUST be set to 384 in Honcho config to match
bge-small-en-v1.5. Upstream defaults to 1536 (OpenAI text-embedding-3-small).
Without setting `EMBEDDING_VECTOR_DIMENSIONS=384`, Honcho will fail startup
validation with a dimension mismatch error.

---

## P1 — Planned Improvements

(Placeholder — improvements will be documented here as they are implemented.)

### Reranker Integration in Message Search
- **Status:** Planned
- **Description:** After RRF fusion in `src/utils/search.py`, pass top-30
  results through RW IE's `/v1/rerank` endpoint for better precision.
- **Dependencies:** RW InferenceEngine running and accessible

### Embedding Dimension Auto-Detection
- **Status:** Planned
- **Description:** Have the startup validator probe RW IE's `/health`
  endpoint to auto-detect the embedding dimension rather than requiring
  manual config.
