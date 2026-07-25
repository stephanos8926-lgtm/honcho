# RW InferenceEngine Integration Plan

## 1. Add as Additional Embedding Provider

**Goal:** Add RW InferenceEngine (`http://localhost:8300`) as a named embedding provider alongside OpenAI and Gemini.

**What already works:** The OpenAI transport supports custom `base_url`. Configuring `transport=openai` + `base_url=http://localhost:8300/v1` with model `bge-small-en-v1.5` will work today via env vars.

**Proper patch required (code changes):**

### Files to modify:
- `src/config.py`:
  - Add `"rw_inference"` to `EmbeddingTransport` type: `Literal["openai", "gemini", "rw_inference"]`
  - Add `"rw_inference"` handling in `_default_embedding_model_for_transport()` → return `"bge-small-en-v1.5"`
  - Add `"rw_inference"` handling in `_default_embedding_api_key()` → return `None` (no auth needed)

- `src/embedding_client.py`:
  - Add `"rw_inference"` branch in `_EmbeddingClient.__init__()`:
    - Uses `httpx.AsyncClient` directly (not OpenAI SDK — RW InferenceEngine uses a simpler API)
    - Or reuse OpenAI client with `base_url=http://localhost:8300/v1` and no API key
  - Add embed methods for RW InferenceEngine transport:
    - `POST /v1/embeddings` with `{"input": text, "model": "bge-small-en-v1.5"}`
    - Returns `{"data": [{"embedding": [...]}]}` — OpenAI-compatible format

### Config (no-code):
```toml
# config.toml or env vars:
EMBEDDING__TRANSPORT=rw_inference
EMBEDDING__MODEL=bge-small-en-v1.5
EMBEDDING__BASE_URL=http://localhost:8300/v1
# Or keep existing and add a second embedding provider
```

## 2. Reranker Integration Opportunities

**Current state:** Honcho does NOT use any reranker model.

### Opportunity A: Message Search Re-ranking (HIGH VALUE)
**Where:** `src/utils/search.py` → `search()` function
**Current flow:** Embed query → semantic search (pgvector) + fulltext search → RRF fusion → return top-N
**Proposed flow:** Embed query → semantic search + fulltext → RRF fusion → **rerank top-30 with cross-encoder** → return top-10

**Why it helps:** RRF is a rank-combination method, not a relevance scorer. A cross-encoder (like the one RW IE serves at `/v1/rerank`) evaluates query-document pairs directly, giving much better relevance ordering than RRF alone. This directly improves conclusion/message search quality for the Dialectic agent.

**Implementation:**
```python
# After RRF fusion, before returning:
if settings.RERANKER.ENABLED:
    top_k = combined_results[:30]
    pairs = [(query, msg.content) for msg in top_k]
    reranked = await reranker_client.rerank(pairs)  # POST /v1/rerank
    return reranked[:limit]  # Now ordered by cross-encoder score
```

### Opportunity B: Dreamer/Observation Search (MEDIUM VALUE)
**Where:** `src/dreamer/specialists.py` — when generating peer cards, the Dreamer searches through stored observations. Reranking these results could produce higher-quality peer cards.

**Why it helps:** Peer card generation quality directly affects the Dialectic agent's understanding of user context.

### Opportunity C: Conclusion Search (MEDIUM VALUE)
**Where:** `src/routers/conclusions.py` — the `query_conclusions` endpoint
**Current flow:** Embed query → vector search on conclusions → return results
**Proposed:** Add reranker step after initial vector search for precision improvement.

## 3. Implementation Priority

| Priority | Feature | Effort | Impact |
|----------|---------|--------|--------|
| P0 | Embedding provider config | 1-2 files, ~50 lines | ✅ Required for self-hosted operation |
| P1 | Message search reranker | 1 file, ~30 lines | High — improves Dialectic quality |
| P2 | Dreamer reranking | 1-2 files, ~40 lines | Medium — better peer cards |
| P3 | Conclusion reranker | 1 file, ~20 lines | Medium — better API responses |
