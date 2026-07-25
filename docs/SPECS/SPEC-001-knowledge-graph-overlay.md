# SPEC-001 v3.0: Knowledge Graph Overlay on the Peer Model

## Status: Draft v3.0 (Post-Pipeline-Audit Revision)

## 1. Executive Summary

Add a knowledge graph layer to Honcho that extracts entities, relationships, and
temporal facts from messages and observations, storing them as typed edges between
peer-managed entities. This gives Honcho multi-hop reasoning and cross-entity
querying while preserving its unique peer/social memory model — creating the only
memory system that combines peer context with entity-relationship queries.

## 2. Motivation

### 2.1 Market Gap

The top three pain points in agent memory systems (2026):

| Pain Point | Honcho Status | Competitor Status |
|---|---|---|
| Multi-hop reasoning | ❌ Not supported | Hindsight: KG + temporal |
| Memory importance & decay | ❌ No scoring | Hindsight: temporal decay |
| Entity-relationship queries | ❌ Not supported | Mem0 Pro ($249/mo), Hindsight (MIT) |

### 2.2 Competitive Analysis

| Feature | Honcho | Hindsight | Mem0 |
|---|---|---|---|
| Social/peer model | ✅ Native | ❌ | ❌ |
| Knowledge graph | ❌ | ✅ Built-in | ✅ Pro tier ($249/mo) |
| Peer↔Entity linking | ❌ (proposed) | ❌ | ❌ |
| Entity resolution | ❌ | ✅ | ✅ |
| Temporal reasoning | ❌ | ✅ | ❌ |
| Peer cards | ✅ | ❌ | ❌ |
| Dialectic reasoning | ✅ | ❌ | ❌ |

**Uniqueness claim:** Hindsight has KG + temporal but no peer model. Mem0 has entity
extraction but graphs are paywalled at $249/mo and have no peer social context.
Our combination of peer cards + knowledge graph + dialectic reasoning creates a
"social knowledge graph" that no competitor offers.

### 2.3 Why This Matters for RapidWebs

- RW InferenceEngine (already deployed on srv1:8300) serves entity extraction
- PostgreSQL + pgvector (already deployed) stores graph edges
- Full AGPL access to modify Honcho's codebase

## 3. Design

### 3.1 Data Model

```python
# === NEW TABLES ===

class KGEntity(Base):
    __tablename__ = "kg_entities"
    
    id: str = Column(String, primary_key=True, default=nanoid)
    workspace_name: str = Column(String, ForeignKey("workspaces.name"), nullable=False)
    name: str = Column(String, nullable=False)
    aliases: list[str] = Column(JSONB, default=[])
    entity_type: str = Column(String, nullable=False)  # From KGEntityType registry
    first_seen_at: datetime = Column(DateTime(timezone=True), nullable=False)
    last_seen_at: datetime = Column(DateTime(timezone=True), nullable=False)
    confidence: float = Column(Float, default=1.0)
    peer_name: str | None = Column(String, nullable=True)
    mention_count: int = Column(Integer, default=0)  # Track how many times mentioned
    
    __table_args__ = (
        Index("idx_kg_entities_ws_name", "workspace_name", "name"),
        Index("idx_kg_entities_type", "workspace_name", "entity_type"),
    )


class KGRelationship(Base):
    __tablename__ = "kg_relationships"
    
    id: str = Column(String, primary_key=True, default=nanoid)
    workspace_name: str = Column(String, ForeignKey("workspaces.name"), nullable=False)
    source_entity_id: str = Column(
        String, ForeignKey("kg_entities.id"), nullable=False, index=True
    )
    target_entity_id: str = Column(
        String, ForeignKey("kg_entities.id"), nullable=False, index=True
    )
    relationship_type: str = Column(String, nullable=False)
    properties: dict = Column(JSONB, default={})
    first_seen_at: datetime = Column(DateTime(timezone=True), nullable=False)
    last_seen_at: datetime = Column(DateTime(timezone=True), nullable=False)
    observation_id: str = Column(
        String, ForeignKey("observations.id", ondelete="SET NULL"), nullable=True
    )
    confidence: float = Column(Float, default=1.0)
    
    __table_args__ = (
        Index("idx_kg_rel_source", "workspace_name", "source_entity_id"),
        Index("idx_kg_rel_target", "workspace_name", "target_entity_id"),
        Index("idx_kg_rel_type", "workspace_name", "relationship_type"),
        # Composite index for traversal queries: (type + source + target)
        Index("idx_kg_rel_traverse", "workspace_name", "relationship_type", 
              "source_entity_id", "target_entity_id"),
    )


class KGQueryLog(Base):
    __tablename__ = "kg_query_log"
    
    id: str = Column(String, primary_key=True, default=nanoid)
    workspace_name: str = Column(String, nullable=False)
    query_fingerprint: str = Column(String, nullable=False, index=True)  # Deterministic hash of query params
    max_depth: int = Column(Integer)
    filters_applied: dict = Column(JSONB, default={})
    result_count: int = Column(Integer)
    duration_ms: int = Column(Integer)
    cache_hit: bool = Column(Boolean, default=False)
    created_at: datetime = Column(DateTime(timezone=True), nullable=False)
    
    __table_args__ = (
        Index("idx_kg_query_log_ws", "workspace_name", "created_at"),
    )
```

**v3 changes from v2:**
- All DateTime fields now `DateTime(timezone=True)` to prevent timezone comparison bugs
- Added `mention_count` to KGEntity for importance scoring
- Added composite traversal index `idx_kg_rel_traverse`
- Changed `query_entity` to `query_fingerprint` for deterministic query caching
- `query_log` now written on BOTH query AND extraction paths (enables cache warming on boot)

### 3.2 Entity Extraction Pipeline

```
Message enters Deriver pipeline
    │
    ├──→ Existing OL (observation extraction) ──→ Database
    │
    └──→ KG Extraction (NEW) — runs as SEPARATE async step after OL
            │
            ├──→ LLM structured-output call (configurable model, defaults to Deriver model)
            │     Schema: KG_EXTRACTION_SCHEMA (defined in 3.7)
            │     Timeout: 15s per message
            │     Retry: 2 attempts on transient failure
            │     Isolation: runs as a separate task, NOT inside existing process_representation_batch
            │
            ├──→ Entity resolution + alias merging
            │     Algorithm: trigram similarity via pg_trgm (pg_similarity)
            │     Threshold: 0.6 for fuzzy alias matching
            │     Fallback: exact name match first, then trigram
            │     - Exact name match → update existing entity (last_seen_at, increment mention_count)
            │     - Trigram match on aliases → merge aliases into existing entity
            │     - New entity → insert
            │     - Confidence decay: entities unseen for >30 days have confidence halved
            │
            ├──→ Relationship dedup + creation
            │     - Same source + target + type → update last_seen_at, merge properties
            │     - New (source, target, type) → insert
            │     - Relationships with confidence < 0.1 are pruned
            │
            ├──→ Peer→Entity linking
            │     - If entity type is "person" or "agent" AND name matches a workspace peer
            │     - AND confidence > 0.7 → set entity.peer_name = peer_name
            │
            └──→ KGQueryLog write (async, non-blocking)
                  - Record extraction metadata for cache warming
                  - query_fingerprint = sha256(workspace_name + "extraction")
```

**v3 changes from v2:**
- Explicit isolation: KG extraction runs as a separate async step, NOT inside existing `process_representation_batch`. This avoids tight coupling with the complex observation extraction logic.
- Fuzzy matching algorithm specified: pg_trgm trigram similarity (already available if pg_trgm extension is enabled)
- Added `increment mention_count` for importance tracking
- Cache warming now works because query_log is written on BOTH extraction and query paths

### 3.3 Entity Types Registry

```python
# src/kg/entity_types.py
KGEntityType = Literal[
    "person",       # Human individual
    "agent",        # AI agent
    "service",      # Software service (Redis, PostgreSQL, etc.)
    "tool",         # CLI tool, library, framework
    "project",      # Software project, codebase
    "concept",      # Abstract concept (authentication, caching)
    "location",     # Physical or network location
    "organization", # Company, team, group
    "event",        # Deployments, incidents, milestones
    "unknown",      # Fallback for unclassified entities
]
```

### 3.4 Relationship Types Registry

```python
# src/kg/relationship_types.py
KG_RELATIONSHIP_TYPES = frozenset({
    "depends_on",     # A depends on B (service dependency, import)
    "manages",        # A manages B (ownership, admin)
    "configured_with", # A is configured with B (settings, environment)
    "deployed_at",    # A is deployed at B (hosting, location)
    "communicates_with", # A communicates with B (API, network)
    "part_of",        # A is part of B (hierarchy, composition)
    "preceded_by",    # A happened before B (temporal ordering)
    "caused",         # A caused B (causal relationship)
    "mentioned_in",   # A was mentioned in context of B (association)
    "related_to",     # Generic relationship (fallback)
})
```

### 3.5 Graph Traversal Queries

```python
# === API ENDPOINTS ===

# Graph traversal with compound filtering
GET /v3/workspaces/{w}/kg/traverse
    ?entity=Redis
    &max_depth=3
    &relationship_types=depends_on,manages
    &entity_types=service
    &before=2026-06-01
    &after=2025-01-01
    &min_confidence=0.5
    &limit=100

# Entity resolution (fuzzy search via pg_trgm)
GET /v3/workspaces/{w}/kg/entities
    ?q=auth
    &type=service
    &min_confidence=0.3
    &limit=20

# Path finding (shortest BFS path with cycle detection)
GET /v3/workspaces/{w}/kg/path
    ?from=AuthService
    &to=PostgreSQL
    &max_depth=5
    &relationship_types=depends_on

# Subgraph snapshot (export neighborhood for caching/visualization)
GET /v3/workspaces/{w}/kg/subgraph
    ?entity=Redis&depth=2&format=json

# Peer→Entity linking query
GET /v3/workspaces/{w}/kg/peer-entities
    ?peer_name=alice
    &relationship_types=mentioned_in
```

### 3.6 Dialectic Integration

The Dialectic agent gains a `kg_query` tool. Availability by reasoning level:

| Level | kg_query available? | Max depth | Cache TTL |
|---|---|---|---|
| minimal | ❌ | — | — |
| low | ✅ | 2 | 300s |
| medium | ✅ | 3 | 120s |
| high | ✅ | 4 | 60s |
| max | ✅ | 6 | 0s (no cache) |

Tool flow:
1. Extract entity names from user question (LLM-internal, no API call)
2. Resolve entities against KG (trigram fuzzy match on aliases)
3. Traverse graph with level-appropriate max depth
4. Cache-aware: check L1 (in-memory LRU) before querying DB
5. On cache miss: query DB, populate L1 cache, write to KGQueryLog
6. Return structured relationship data as context
7. Optionally include entity→peer links if the entity maps to a peer

### 3.7 KG Extraction Structured-Output Schema

Identical to v2.0 schema. (Defined in `src/kg/extraction_schema.py`.)

### 3.8 Caching Strategy

```python
# Three-tier cache:
# L0: In-request cache (per-api-call, cleared after each response)
# L1: In-memory LRU cache (default 1000 entries, TTL based on reasoning level)
# L2: KGQueryLog table (persistent, used for cache warming on restart)

CACHE_CONFIG = {
    "l1_size": 1000,
    "l1_ttl_seconds": 300,
    "warming_enabled": True,
    "warming_limit": 100,
}

# Write-path caching:
# - Entity/relationship CREATION does NOT invalidate L1 (no write-through)
# - L1 entries TTL-expire naturally within 300s
# - L2 query log is APPEND-ONLY (no updates, no deletes)
# - Cache warming on boot: read most frequent query_fingerprints from L2
```

### 3.9 Peer→Entity Linking

Identical to v2.0 with addition: entity mentions tracked via `mention_count`.

### 3.10 Temporal Filtering

All DateTime fields use `DateTime(timezone=True)` to ensure consistent comparisons.

```python
# Timezone-safe comparison pattern:
from datetime import timezone

# All stored timestamps include timezone
# Query parameters are treated as UTC if no timezone provided
# before/after/as_of filters use UTC for comparison

# Example query:
GET /v3/workspaces/{w}/kg/traverse?entity=API&before=2026-06-01T00:00:00Z
```

### 3.11 Concurrent Write Handling

```python
# When two messages are processed simultaneously and both reference the same entity:

# Entity creation uses INSERT ... ON CONFLICT DO NOTHING + follow-up SELECT
# (postgresql.insert with on_conflict_do_nothing)
# This prevents duplicate entity creation under concurrent access.

# Relationship creation: same pattern. Source + target + type composite
# unique constraint prevents duplicates:
__table_args__ = (
    UniqueConstraint("workspace_name", "source_entity_id", 
                     "target_entity_id", "relationship_type",
                     name="uq_kg_relationship"),
)
```

### 3.12 Pruning Policy

```python
# Entities:
# - Never pruned (they represent named things that may reappear)
# - Low-confidence entities (confidence < 0.1, unseen > 90 days) moved to
#   "dormant" status (filtered from normal queries, still queryable explicitly)
# - Dormant entities periodically archived (configurable, default: monthly)

# Relationships:
# - Confidence < 0.1 AND last_seen_at > 90 days → deleted
# - Max edges per entity: configurable (default 500), oldest dropped on overflow
# - Periodic compaction job runs every 24 hours (configurable interval)
```

## 4. Implementation Plan

### Phase 1: Data Model + Migration (4 days)
- Add KG entities + relationships + query_log tables with timezone-aware DateTime
- Add unique constraint on (workspace, source, target, type) for concurrent safety
- Composite index for traversal queries
- Alembic migration
- Pydantic schemas for API request/response validation
- Entity + relationship types registry modules

### Phase 2: Extraction Pipeline (7 days)
- Define KG structured-output schema
- Add KG extraction as SEPARATE async step alongside Deriver (NOT inside process_representation_batch)
- Entity resolution with trigram fuzzy matching (pg_trgm)
- Relationship dedup with concurrent-safe upsert pattern
- Peer→Entity linking with mention_count tracking
- Confidence decay + pruning logic
- Extraction timeout (15s) + retry (2 attempts)

### Phase 3: Graph Queries (5 days)
- BFS traversal with cycle detection + visited set
- Compound filter implementation (type + time + confidence)
- Path finding (shortest BFS path)
- Subgraph snapshot export
- Three-tier cache (L0 request-level + L1 in-memory LRU + L2 query log)
- Cache warming on boot from L2 query log
- API endpoints with input validation

### Phase 4: Dialectic Integration (3 days)
- Register `kg_query` tool with level-appropriate availability
- Entity extraction from user questions (LLM-internal, no API call)
- Context injection with cache-aware response
- Integration tests with mock graph data

### Phase 5: Testing + Hardening (4 days)
- Unit tests for entity resolution (trigram + exact + concurrency)
- Unit tests for relationship dedup (upsert + concurrency)
- Integration tests for all API endpoints
- Performance benchmarks (traversal at depth 3/4/6)
- Cache hit ratio measurement
- Adversarial input testing
- Documentation: AGENTS.md, RAPIDWEBS.README.md

### Total: ~23 days

## 5. Dependencies

- RW InferenceEngine — ✅ Already deployed on srv1:8300
- PostgreSQL + pgvector — ✅ Already deployed
- pg_trgm extension — ⚠️ May need `CREATE EXTENSION pg_trgm;`
- Honcho Deriver process — ✅ Running
- No new infrastructure required

## 6. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Entity extraction LLM latency | Medium | Medium | Configurable model; 15s timeout; skip on timeout (log only) |
| Relationship edge explosion | High | Medium | Confidence pruning (< 0.1); max 500 edges/entity; 24h compaction |
| Entity name collision | Medium | Medium | Workspace-scoped dedup + trigram; human correction via PATCH |
| Stale relationships | High | Low | Decay + 90-day pruning; `last_seen_at` tracking |
| Concurrent entity creation race | Low | Medium | `INSERT ... ON CONFLICT DO NOTHING` + unique constraint |
| Prompt injection via message content | Low | High | Isolated LLM call with system prompt; structured-output enforcement |
| Cache invalidation on graph update | Medium | Low | TTL-based expiry only; no write-through; acceptable staleness |
| GIL contention during extraction | Low | Medium | Runs in executor thread (not event loop) |

## 7. Success Metrics

| Metric | Target | Measurement |
|---|---|---|
| Multi-hop query accuracy | > 80% | Internal test set of 50 multi-hop queries |
| Graph traversal latency (depth 3) | < 200ms p95 | Benchmark via KGQueryLog telemetry |
| Entity extraction overhead per message | < 500ms p95 | Deriver pipeline timing |
| Cache hit ratio (L1) | > 60% after 1 week | KGQueryLog.cache_hit field |
| Entity resolution precision | > 90% | Manual audit of first 500 entities |

## 8. Audit Trail

### v2→v3 Changes (from LOW-mode pipeline audit)

| Issue | v2 Status | v3 Resolution |
|---|---|---|
| Extraction pipeline coupling to Deriver | Implicit: "alongside existing" | ✅ Explicitly separated as its own async step |
| Fuzzy matching algorithm unspecified | "alias matching" (ambiguous) | ✅ pg_trgm trigram at 0.6 threshold |
| Concurrent entity creation | Not addressed | ✅ INSERT ON CONFLICT DO NOTHING + unique constraint |
| Timezone handling | DateTime without timezone | ✅ DateTime(timezone=True) everywhere |
| Cache warming on fresh deployment | Empty KGQueryLog → no warm | ✅ query_log written on both extraction AND query paths |
| Entity pruning policy | None | ✅ "Dormant" status for low-confidence, long-unseen entities |
| Entity importance metric | None | ✅ mention_count field tracks frequency |
| Traversal query index | Individual indexes only | ✅ Composite index (type + source + target) |
| Cache layer hierarchy | Two-tier (L1 + L2) | ✅ Three-tier (+ L0 per-request cache) |
| Unique constraint on relationships | None | ✅ (workspace, source, target, type) composite unique |

### Remaining concerns (not addressed in v3)
1. Complex query DSL not specified — compound queries require multiple params, not a query language
2. Entity pruning doesn't auto-restore (dormant entities must be explicitly queried)
3. pg_trgm extension not guaranteed on all PostgreSQL deployments
4. No bidirectional peer→KG linking (entities link to peers, but peers don't list their entities)
