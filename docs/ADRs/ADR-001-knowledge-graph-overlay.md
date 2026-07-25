# ADR-001 v2.0: Knowledge Graph Overlay on Peer Model

## Status: Draft v2.0 (Post-Audit Revision)

## Context
Honcho excels at social/peer memory but lacks entity-relationship reasoning.
Our fork has RW InferenceEngine for local entity extraction. The v1 spec
missed peer↔entity linking, type enforcement, indexing strategy, and caching.

## Decision
Add a knowledge graph overlay with:
1. Entity types registry (validated, not free-text)
2. Relationship types controlled vocabulary
3. Peer→Entity linking via `kg_entities.peer_name` field
4. Two-tier cache (L1 in-memory LRU + L2 query log)
5. Temporal filtering with point-in-time snapshots
6. Compound query support (type + relationship + time + confidence)
7. Extraction timeout (15s) + retry (2 attempts) + confidence decay

## Consequences
- Positive: Multi-hop reasoning, cross-entity queries, temporal queries
- Positive: Unique "social knowledge graph" (peer context + KG)
- Positive: No new infrastructure (uses existing PG + RW IE)
- Negative: ~10-20% storage growth from KG tables
- Negative: Extraction latency adds ~300-500ms to Deriver per message
- Risk: Entity resolution accuracy at small scale (fuzzy matching + PATCH endpoint)

## Compliance
- All KG tables workspace-scoped (consistent with existing Honcho schema)
- Types registries validated at startup
- Extraction uses same structured-output pattern as existing Deriver
- API endpoints follow `/v3/workspaces/{w}/kg/*` pattern

## References
- SPEC-001 v2.0 (this revision)
- v1 audit: forward, reverse, adversarial reviews
- Hindsight architecture (graph + temporal retrieval)
