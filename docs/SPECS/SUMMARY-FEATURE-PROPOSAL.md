# Feature Proposal Summary — Knowledge Graph Overlay & In-Process Deriver

## Overview

Two feature proposals for `stephanos8926-lgtm/honcho` (our RapidWebs fork of plastic-labs/honcho), developed from competitive analysis of the agent memory market, Honcho's GitHub issues, and our own self-hosting experience.

---

## Feature 1: Knowledge Graph Overlay on the Peer Model

**SPEC-001** | **ADR-001**

### The Problem
Honcho excels at social/peer memory — tracking what one entity knows about another — but cannot answer multi-hop questions like *"What decisions were made about the API deployment between January and March?"* or *"Which services depend on Redis?"* Competitors (Hindsight, Mem0 Pro) solve this with knowledge graphs. Honcho has none.

### The Market Gap
| Query Type | Honcho | Hindsight | Mem0 |
|---|---|---|---|
| "What does Alice prefer?" | ✅ Peer card | ❌ | ❌ |
| "Which services depend on Redis?" | ❌ | ✅ KG | ✅ (Pro tier) |
| "How did the auth system change over time?" | ❌ | ✅ Temporal | ❌ |

No competitor combines **social/peer memory** with **knowledge graphs**. That's our wedge.

### What We'd Build
A knowledge graph layer that sits **on top of** Honcho's existing peer/observation model (not replacing it):

1. **Entity Extraction** — New step in the Deriver pipeline that extracts named entities (people, services, tools, projects) and typed relationships (depends_on, manages, configured_with) from every message
2. **Graph Storage** — Two new database tables (`kg_entities`, `kg_relationships`) alongside existing Honcho schema
3. **Graph Queries** — New API endpoints for entity resolution, relationship traversal, and path finding
4. **Dialectic Tool** — The Dialectic agent gains a `kg_query` tool that extracts entities from user questions and traverses the graph to provide structured, context-rich answers

### Architecture
```
Message → Deriver → Existing OL (observations) ──→ Database
                  → KG-Extractor (entity + relationship extraction)
                      ↓
                  KGEntity dedup (alias resolution)
                      ↓
                  KGRelationship creation (typed edges)
```

### Why It's Unique
Honcho would be the **only** memory system that combines peer-modeled social context with entity-relationship queries. You can ask *"What does Alice think about the auth migration?"* and get both Alice's personal perspective (from peer cards) AND the factual dependency graph (from KG).

### Effort: ~13-18 days
- Phase 1: Data model + migration (3-5 days)
- Phase 2: Extraction pipeline (5-7 days)
- Phase 3: Graph queries (3-5 days)
- Phase 4: Dialectic integration (2-3 days)

### Dependencies
- ✅ RW InferenceEngine (local, already deployed on srv1:8300)
- ✅ PostgreSQL + pgvector (already deployed)
- ❌ No new infrastructure needed

### ADR-001 Decision
Add KG as an opt-in extension, not a replacement. No changes to existing Honcho abstractions. All new tables are workspace-scoped. Entity extraction uses the same LLM provider as the Deriver (no new provider types).

---

## Feature 2: In-Process Deriver / Single-Container Mode

**SPEC-002** | **ADR-002**

### The Problem
Honcho requires **four moving parts** to self-host: PostgreSQL, Redis, an API server, and a separate Deriver worker process. This is the #1 complaint across Honcho's GitHub issues (#494, #822, #789). Our own deployment on an 8GB Hetzner VPS is overkill for single-user traffic.

### The Competitive Comparison
| System | Dependencies | Single Process? |
|---|---|---|
| Honcho (current) | PG + Redis + API + Deriver | ❌ |
| Hindsight | PG (bundled) | ✅ Single container |
| Mem0 | Qdrant/PG | ✅ Library mode |
| ByteRover | None | ✅ Single binary |

### What We'd Build
An **opt-in** `DERIVER_IN_PROCESS_MODE=true` flag that runs the Deriver worker as an asyncio background task inside the API process, using an in-memory queue instead of Redis.

```
Current:  API ←[Redis]→ Deriver ←→ PG
New:      API+Deriver ──→ PG  (when IN_PROCESS_MODE=true)
```

Key design decisions:
1. **In-memory queue** — replace Redis-backed queue with `asyncio.Queue`. Queue loss on restart is acceptable (work re-derives from messages)
2. **Background asyncio task** — runs inside the API process via FastAPI lifespan hooks
3. **Backpressure** — max queue size (default 1000), per-work-unit timeout (30s), oldest-dropped on overflow
4. **Reconciliation** — existing Reconciler (vector sync) works identically in both modes

### Backward Compatibility
- Default remains `false` → **zero config change** for existing deployments
- Redis connection is NOT established when in-process mode is on → saves resources
- All existing behavior identical when not opted in

### Effort: ~6-9 days
- Phase 1: Queue abstraction (extract QueueInterface protocol) + InMemoryQueue implementation (2-3 days)
- Phase 2: API lifespan integration + config wiring (2-3 days)
- Phase 3: Testing + single-container docs (2-3 days)

### ADR-002 Decision
Opt-in flag, not a replacement of the existing architecture. Default stays `false`. `QueueInterface` protocol allows transparent swapping. No changes to existing deployment paths.

---

## Why These Two Together

| | KG Overlay | In-Process Deriver |
|---|---|---|
| **Problem** | Can't answer multi-hop/fact queries | Too complex to self-host |
| **Market gap** | No peer + KG combo exists | Single-container is table-stakes |
| **Our advantage** | We already have RW IE + PG | We already run on constrained VMs |
| **Effort** | 13-18 days | 6-9 days |
| **Unlocks** | Dialectic becomes a true reasoning agent | Honcho becomes deployable in 5 minutes |

Together they transform Honcho from "powerful but heavy" into "powerful, deployable, and unique" — the only memory system that combines social reasoning, knowledge graphs, and single-container deployment.

## Status
Both SPEC documents and ADRs are written, committed, and pushed to our fork at `github.com/stephanos8926-lgtm/honcho`. Ready for review and prioritization.
