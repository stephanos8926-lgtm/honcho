# ADR-001: Knowledge Graph Overlay on Peer Model

## Status: Draft

## Context
Honcho excels at social/peer memory but lacks entity-relationship reasoning.
Competitors (Hindsight, Mem0 Pro) have knowledge graphs for multi-hop queries.
Our fork has RW InferenceEngine for local LLM calls, making entity extraction
cost-effective.

## Decision
Add a knowledge graph layer (entities, relationships, typed edges) as an
extension to the existing peer/observation model. The graph is an overlay, not
a replacement — existing Honcho abstractions remain unchanged.

## Consequences
- Positive: Multi-hop reasoning, temporal queries, Dialectic gains kg_query tool
- Positive: Differentiates from Hindsight by combining social memory + KG
- Positive: No new infrastructure (uses existing PG + RW IE)
- Negative: Storage growth from KG tables (~10-20% over observation storage)
- Negative: Entity extraction adds latency to Deriver pipeline
- Risk: Entity resolution accuracy at small scale (mitigated by fuzzy matching)

## Compliance
- Follows existing Honcho schema conventions (workspace-scoped, nanoid PKs)
- Uses same OL/LLM provider as Deriver (no new provider types)
- API endpoints follow existing `/v3/workspaces/{w}/...` pattern

## References
- SPEC-001: Knowledge Graph Overlay
- Issue #590, #728 (community evidence for needed improvements)
- Hindsight architecture (knowledge graph + temporal)
