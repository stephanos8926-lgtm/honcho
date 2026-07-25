# SPEC-001 v2.0: Knowledge Graph Overlay on the Peer Model

## Status: Draft v2.0 (Post-Audit Revision)

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
| Multi-hop reasoning ("what decisions were made about X?") | ❌ Not supported | Hindsight: KG + temporal |
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
    """A named entity extracted from messages/observations.
    
    Entity types are validated against a registry (KGEntityType).
    Entities are workspace-scoped; cross-workspace resolution is
    handled by alias matching, not by shared IDs.
    """
    __tablename__ = "kg_entities"
    
    id: str = Column(String, primary_key=True, default=nanoid)
    workspace_name: str = Column(String, ForeignKey("workspaces.name"), nullable=False)
    name: str = Column(String, nullable=False)  # Canonical name
    aliases: list[str] = Column(JSONB, default=[])  # Resolved aliases
    entity_type: str = Column(String, nullable=False)  # From KGEntityType registry
    first_seen_at: datetime = Column(DateTime, nullable=False)
    last_seen_at: datetime = Column(DateTime, nullable=False)
    confidence: float = Column(Float, default=1.0)
    
    # Optional link to Honcho peer model — when an entity represents a peer
    peer_name: str | None = Column(String, nullable=True)
    
    __table_args__ = (
        Index("idx_kg_entities_workspace_name", "workspace_name", "name"),
        Index("idx_kg_entities_type", "workspace_name", "entity_type"),
    )


class KGRelationship(Base):
    """A typed, directed edge between two entities.
    
    Relationship types come from a controlled vocabulary (not free-text)
    to enable reliable querying. See 3.6 for type registry.
    """
    __tablename__ = "kg_relationships"
    
    id: str = Column(String, primary_key=True, default=nanoid)
    workspace_name: str = Column(String, ForeignKey("workspaces.name"), nullable=False)
    source_entity_id: str = Column(
        String, ForeignKey("kg_entities.id"), nullable=False, index=True
    )
    target_entity_id: str = Column(
        String, ForeignKey("kg_entities.id"), nullable=False, index=True
    )
    relationship_type: str = Column(String, nullable=False)  # From KG_RELATIONSHIP_TYPES
    properties: dict = Column(JSONB, default={})
    first_seen_at: datetime = Column(DateTime, nullable=False)
    last_seen_at: datetime = Column(DateTime, nullable=False)
    observation_id: str = Column(
        String, ForeignKey("observations.id", ondelete="SET NULL"), nullable=True
    )
    confidence: float = Column(Float, default=1.0)
    
    __table_args__ = (
        Index("idx_kg_rel_source", "workspace_name", "source_entity_id"),
        Index("idx_kg_rel_target", "workspace_name", "target_entity_id"),
        Index("idx_kg_rel_type", "workspace_name", "relationship_type"),
    )


class KGQueryLog(Base):
    """Telemetry for graph query performance and cache warming."""
    __tablename__ = "kg_query_log"
    
    id: str = Column(String, primary_key=True, default=nanoid)
    workspace_name: str = Column(String, nullable=False)
    query_entity: str = Column(String)
    max_depth: int = Column(Integer)
    filters_applied: dict = Column(JSONB, default={})
    result_count: int = Column(Integer)
    duration_ms: int = Column(Integer)
    cache_hit: bool = Column(Boolean, default=False)
    created_at: datetime = Column(DateTime, nullable=False)
    
    __table_args__ = (
        Index("idx_kg_query_log_workspace", "workspace_name", "created_at"),
    )
```

### 3.2 Entity Extraction Pipeline

```
Message enters Deriver pipeline
    │
    ├──→ Existing OL (observation extraction) ──→ Database
    │
    └──→ KG Extraction (NEW)
            │
            ├──→ LLM structured-output call (configurable model, defaults to Deriver model)
            │     Schema: KG_EXTRACTION_SCHEMA (defined in 3.7)
            │     Timeout: 15s per message
            │     Retry: 2 attempts on transient failure
            │
            ├──→ Entity resolution + alias merging
            │     - Exact name match → update existing entity (last_seen_at)
            │     - Fuzzy alias match → merge aliases
            │     - New entity → insert
            │     - Confidence decay: entities unseen for >30 days have confidence halved
            │
            ├──→ Relationship dedup + creation
            │     - Same source + target + type → update last_seen_at, merge properties
            │     - New (source, target, type) → insert
            │     - Relationships with confidence < 0.1 are pruned
            │
            └──→ KGQueryLog write (async, non-blocking)
```

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
KG_RELATIONSHIP_TYPES = {
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
}
# NOTE: This vocabulary is validated at config load time. Adding new types
# requires updating both the schema and the LLM extraction prompt.
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

# Entity resolution (fuzzy search)
GET /v3/workspaces/{w}/kg/entities
    ?q=auth           # Fuzzy search by name/aliases
    &type=service     # Optional type filter
    &min_confidence=0.3
    &limit=20

# Path finding (shortest path between two entities)
GET /v3/workspaces/{w}/kg/path
    ?from=AuthService
    &to=PostgreSQL
    &max_depth=5
    &relationship_types=depends_on

# Subgraph snapshot (export a neighborhood for caching)
GET /v3/workspaces/{w}/kg/subgraph
    ?entity=Redis&depth=2&format=json

# Peer→Entity linking
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
2. Resolve entities against KG (fuzzy match on aliases)
3. Traverse graph with level-appropriate max depth
4. Return structured relationship data as context
5. Optionally include entity→peer links if the entity maps to a peer

### 3.7 KG Extraction Structured-Output Schema

```json
{
  "type": "object",
  "properties": {
    "entities": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": {"type": "string"},
          "type": {"$ref": "#/definitions/KGEntityType"},
          "aliases": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["name", "type"]
      }
    },
    "relationships": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "source": {"type": "string"},
          "target": {"type": "string"},
          "type": {"$ref": "#/definitions/KGRelationshipType"},
          "properties": {"type": "object"}
        },
        "required": ["source", "target", "type"]
      }
    }
  },
  "definitions": {
    "KGEntityType": {
      "type": "string",
      "enum": ["person", "agent", "service", "tool", "project", "concept", "location", "organization", "event", "unknown"]
    },
    "KGRelationshipType": {
      "type": "string",
      "enum": ["depends_on", "manages", "configured_with", "deployed_at", "communicates_with", "part_of", "preceded_by", "caused", "mentioned_in", "related_to"]
    }
  }
}
```

### 3.8 Caching Strategy

```python
# Two-tier cache:
# L1: In-memory LRU cache (default 1000 entries, TTL based on reasoning level)
# L2: KGQueryLog table (persistent, used for cache warming on restart)

CACHE_CONFIG = {
    "l1_size": 1000,
    "l1_ttl_seconds": 300,       # Default TTL
    "warming_enabled": True,      # Pre-warm from KGQueryLog on startup
    "warming_limit": 100,         # Most frequent queries to warm
}
```

### 3.9 Peer→Entity Linking

Entities that correspond to Honcho peers (users, agents) are linked via the
`kg_entities.peer_name` field. This is populated when:
1. The entity name matches a peer name in the workspace
2. The entity type is "person" or "agent"
3. Confidence exceeds 0.7

This enables queries like: "What entities has Alice mentioned?" via the
peer-entities endpoint and allows the Dialectic to cross-reference peer
context with the knowledge graph.

### 3.10 Temporal Filtering Architecture

All relationships carry `first_seen_at` / `last_seen_at`. Graph queries accept
compound time bounds:

```
# Single bound
?before=2026-06-01    → relationships last_seen_at <= date
?after=2026-01-01     → relationships first_seen_at >= date

# Range
?after=2026-01-01&before=2026-06-01  → relationships active within window

# Point-in-time snapshot
?as_of=2026-03-15     → relationships where first_seen_at <= date AND
                        (last_seen_at >= date OR last_seen_at IS NULL)
```

## 4. Implementation Plan

### Phase 1: Data Model + Migration (4 days)
- Add KG entities + relationships + query_log tables
- Alembic migration with proper indexes
- Pydantic schemas for API request/response validation
- Entity types registry module
- Relationship types registry module

### Phase 2: Extraction Pipeline (7 days)
- Define KG structured-output schema
- Add KG extraction step to Deriver pipeline
- Entity resolution with alias merging
- Relationship deduplication with confidence scoring
- Peer→Entity linking logic
- Confidence decay algorithm
- Extraction timeout + retry logic

### Phase 3: Graph Queries (5 days)
- BFS traversal with cycle detection
- Compound filter implementation (type + time + confidence)
- Path finding (shortest BFS path)
- Subgraph snapshot export
- Two-tier cache (L1 in-memory + L2 query log)
- API endpoints with input validation
- Index-based query optimization

### Phase 4: Dialectic Integration (3 days)
- Register `kg_query` tool with level-appropriate availability
- Entity extraction from user questions (LLM-internal)
- Context injection formatting
- Cache-aware response
- Integration tests with mock graph data

### Phase 5: Testing + Hardening (4 days)
- Unit tests for entity resolution
- Unit tests for relationship dedup
- Integration tests for all API endpoints
- Performance benchmarks (traversal at depth 3/4/6)
- Cache hit ratio measurement
- Adversarial input testing (malformed extraction output, injection)
- Documentation: AGENTS.md, RAPIDWEBS.README.md

### Total: ~23 days

## 5. Dependencies

- RW InferenceEngine (entity extraction LLM) — ✅ Already deployed on srv1:8300
- PostgreSQL + pgvector — ✅ Already deployed
- Honcho Deriver process — ✅ Running
- No new infrastructure required

## 6. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Entity extraction LLM latency | Medium | Medium | Configurable model; timeout at 15s; skip extraction on timeout (log only) |
| Relationship edge explosion | High | Medium | Confidence threshold pruning (< 0.1); max edges per entity (configurable, default 500); periodic compaction job |
| Entity name collision | Medium | Medium | Workspace-scoped dedup by (name, type) composite; human correction via PATCH endpoint |
| Stale relationships | High | Low | `last_seen_at` decay; confidence halving at 30 days; pruning at 90 days no updates |
| Prompt injection via message content | Low | High | Extraction runs in isolated LLM call with system prompt guard; output schema enforced by structured-output API |
| Cache invalidation on graph update | Medium | Low | L1 TTL-based expiry; L2 append-only; no write-through cache |
| GIL contention during extraction | Low | Medium | Extraction runs in asyncio executor thread (not event loop) |

## 7. Success Metrics

| Metric | Target | Measurement |
|---|---|---|
| Multi-hop query accuracy | > 80% | Internal test set of 50 multi-hop queries |
| Graph traversal latency (depth 3) | < 200ms p95 | Benchmark via KGQueryLog telemetry |
| Entity extraction overhead per message | < 500ms p95 | Deriver pipeline timing |
| Cache hit ratio (L1) | > 60% after 1 week of use | KGQueryLog.cache_hit field |
| Dialectic response relevance | Subjective improvement | User feedback (not A/B — single-user deployment) |
| Entity resolution precision | > 90% | Manual audit of first 500 extracted entities |

## 8. Audit Findings (v1→v2 Changes)

| Issue | v1 Status | v2 Resolution |
|---|---|---|
| KGObservationLink missing from schema | ❌ Missing | ✅ Added provenance via optional observation_id FK on KGRelationship |
| Entity type enforcement | ❌ Illustrative only | ✅ Type registry with validation (KGEntityType Literal) |
| Peer↔Entity linking | ❌ Not addressed | ✅ peer_name field + peer-entities endpoint |
| Indexing strategy | ❌ None | ✅ Indexes on workspace, name, type, source, target |
| Compound queries | ❌ Single-dimension | ✅ All filters composable in single endpoint |
| Relationship type validation | ❌ Free-text | ✅ Controlled vocabulary + config-load validation |
| Dialectic level availability | ❌ Undefined | ✅ Table per reasoning level |
| Cycle detection | ❌ Not mentioned | ✅ BFS with visited set (standard) |
| Entity resolution conflicts | ❌ Not addressed | ✅ Workspace-scoped dedup + fuzzy matching |
| Caching | ❌ Not mentioned | ✅ Two-tier L1+L2 cache |
| KG extraction schema | ❌ Mentioned without definition | ✅ Full JSON Schema defined |
| Cache warming | ❌ Not mentioned | ✅ Starting from KGQueryLog on boot |
| Extraction timeout/retry | ❌ Not mentioned | ✅ 15s timeout, 2 retries |
| Confidence decay | ❌ Not mentioned | ✅ 30-day halving, 90-day pruning |
| Health impact on Deriver | ❌ Not addressed | ✅ Sync vs async modes documented |
