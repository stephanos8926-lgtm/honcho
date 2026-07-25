# ADR-001 v3.0: Knowledge Graph Overlay on Peer Model

## Status: Draft v3.0 (Post-Pipeline-Audit Revision)

## Context
Honcho excels at social/peer memory but lacks entity-relationship reasoning.
v2.0 addressed the initial audit issues but missed: extraction pipeline coupling,
fuzzy matching specification, concurrent write safety, timezone handling, and
entity pruning.

## Decision
Add a knowledge graph overlay with key v3 refinements:

1. **Isolated extraction step** — KG extraction runs as its own async task, NOT
   inside the existing `process_representation_batch` function. Prevents tight
   coupling with complex observation logic.
2. **Trigram fuzzy matching** — Use `pg_trgm` similarity (0.6 threshold) for
   entity resolution. Fallback: exact match first, then trigram.
3. **Concurrent write safety** — `INSERT ... ON CONFLICT DO NOTHING` pattern for
   entity/relationship creation under concurrent access. Unique constraint on
   (workspace, source, target, type).
4. **Timezone-aware timestamps** — All KG DateTime fields use
   `DateTime(timezone=True)` for consistent comparisons.
5. **Three-tier cache** — L0 per-request, L1 in-memory LRU (1000 entries), L2
   KGQueryLog (persistent). Query log written on both extraction AND query paths.
6. **Entity pruning** — Low-confidence entities (confidence < 0.1, unseen > 90
   days) move to "dormant" status. Not deleted (they may reappear).
7. **`mention_count`** — Tracks entity mention frequency for importance scoring.

## Consequences
- Positive: Multi-hop reasoning, cross-entity queries, temporal queries
- Positive: Unique "social knowledge graph" (peer context + KG)
- Positive: Safe under concurrent access (idempotent upserts)
- Negative: Requires `pg_trgm` extension (may need `CREATE EXTENSION`)
- Negative: Dormant entities aren't auto-restored (must be explicitly queried)

## Compliance
- All KG tables workspace-scoped
- Types registries validated at startup
- API endpoints follow `/v3/workspaces/{w}/kg/*` pattern
- Same structured-output pattern as existing Deriver

## References
- SPEC-001 v3.0 (this revision)
- LOW-mode pipeline audit findings
- pg_trgm documentation: https://www.postgresql.org/docs/current/pgtrgm.html
