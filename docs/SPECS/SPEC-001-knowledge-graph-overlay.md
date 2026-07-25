# SPEC-001: Knowledge Graph Overlay on the Peer Model

## Status: Draft

## 1. Executive Summary

Add a knowledge graph layer to Honcho that extracts entities, relationships, and
temporal facts from messages and observations, storing them as typed edges between
peer-managed entities. This gives Honcho multi-hop reasoning and cross-entity
querying while preserving its unique peer/social memory model — no competitor has
this combination.

## 2. Motivation

### 2.1 Market Gap

The top three pain points in agent memory systems (2026):

| Pain Point | Honcho Status | Competitor Status |
|---|---|---|
| Multi-hop reasoning ("what decisions were made about X?") | ❌ Not supported | Hindsight: KG + temporal |
| Memory importance & decay | ❌ No scoring | Hindsight: temporal decay |
| Single-binary deploy | ❌ Requires PG + Redis + separate workers | Hindsight: bundled; ByteRover: file-based |

### 2.2 Competitive Analysis

| Feature | Honcho | Hindsight | Mem0 |
|---|---|---|---|
| Social/peer model | ✅ Native | ❌ | ❌ |
| Knowledge graph | ❌ | ✅ Built-in | ✅ Pro tier ($249/mo) |
| Multi-strategy retrieval | ❌ Single | ✅ 4 strategies | ✅ Hybrid |
| Cross-memory synthesis | ❌ | ✅ `reflect` | ❌ |
| Entity resolution | ❌ | ✅ | ✅ |
| Temporal reasoning | ❌ | ✅ | ❌ |
| Peer cards | ✅ | ❌ | ❌ |
| Dialectic reasoning | ✅ | ❌ | ❌ |

### 2.3 Why This Matters for RapidWebs

Our fork already has:
- RW InferenceEngine (embeddings + reranker) — can serve entity extraction
- Running PG/pgvector — can store graph edges as adjacency tables
- Control of the Honcho codebase — full AGPL freedom to modify

## 3. Design

### 3.1 Data Model

```python
# New tables (appended to existing Honcho schema)

class KGEntity(Base):
    """A named entity extracted from messages/observations."""
    __tablename__ = "kg_entities"
    
    id: str = Column(String, primary_key=True, default=nanoid)
    workspace_name: str = Column(String, ForeignKey("workspaces.name"))
    name: str = Column(String, nullable=False)  # Canonical name
    aliases: list[str] = Column(JSONB, default=[])  # Resolved aliases
    entity_type: str = Column(String, nullable=False)  # person, service, project, concept, tool
    first_seen_at: datetime = Column(DateTime)
    last_seen_at: datetime = Column(DateTime)
    confidence: float = Column(Float, default=1.0)


class KGRelationship(Base):
    """A typed, directed edge between two entities."""
    __tablename__ = "kg_relationships"
    
    id: str = Column(String, primary_key=True, default=nanoid)
    workspace_name: str = Column(String, ForeignKey("workspaces.name"))
    source_entity_id: str = Column(String, ForeignKey("kg_entities.id"))
    target_entity_id: str = Column(String, ForeignKey("kg_entities.id"))
    relationship_type: str = Column(String)  # depends_on, manages, configured_with, deployed_at...
    properties: dict = Column(JSONB, default={})
    first_seen_at: datetime = Column(DateTime)
    last_seen_at: datetime = Column(DateTime)
    observation_id: str = Column(String, ForeignKey("observations.id"), nullable=True)
    confidence: float = Column(Float, default=1.0)


class KGQueryLog(Base):
    """Tracks graph queries for telemetry and cache warming."""
    __tablename__ = "kg_query_log"
    
    id: str = Column(String, primary_key=True, default=nanoid)
    workspace_name: str = Column(String)
    query_entity: str = Column(String)
    max_depth: int = Column(Integer)
    result_count: int = Column(Integer)
    duration_ms: int = Column(Integer)
    created_at: datetime = Column(DateTime)
```

### 3.2 Entity Extraction Pipeline

New extraction step in the Deriver pipeline, running alongside existing observation
extraction:

```
Message → Deriver → Existing OL (observations) ──→ Database
                  → New KG-Extractor (entity + relationship extraction)
                      ↓
                  KGEntity dedup (alias resolution)
                      ↓
                  KGRelationship creation (typed edges)
                      ↓
                  KGObservationLink (provenance)
```

The extractor uses a structured-output LLM call (same model as the Deriver):

```json
{
  "entities": [
    {"name": "Redis", "type": "service", "aliases": ["redis", "redis-server"]}
  ],
  "relationships": [
    {
      "source": "Auth Service",
      "target": "Redis",
      "type": "depends_on",
      "properties": {"port": 6379}
    }
  ]
}
```

### 3.3 Graph Traversal Queries

New API endpoints and Dialectic tools:

```python
# Graph traversal
GET /v3/workspaces/{w}/kg/query?entity=Redis&max_depth=3&relationship_types=depends_on,manages

# Entity resolution
GET /v3/workspaces/{w}/kg/entities?name=auth&fuzzy=true

# Path finding
GET /v3/workspaces/{w}/kg/path?from=A&to=B&max_depth=5
```

### 3.4 Dialectic Integration

The Dialectic agent gains a `kg_query` tool that:
1. Extracts entity names from the user's question
2. Resolves them against KG entities (fuzzy match on aliases)
3. Traverses the graph up to configurable depth
4. Returns structured relationship data as context for the LLM response

Example: "How does our auth system connect to the database?"
→ Entities: ["auth system" → "Auth Service"], ["database" → "PostgreSQL"]
→ Graph traversal: Auth Service →depends_on→ PostgreSQL
→ Response: "Auth Service connects to PostgreSQL on port 5433 via connection pool..."

### 3.5 Temporal Filtering

All relationships carry `first_seen_at` / `last_seen_at`. Graph queries accept
optional time bounds:

```
GET /v3/workspaces/{w}/kg/query?entity=API&before=2026-06-01
```

Returns only relationships that were observed before that date — effectively a
"point-in-time" knowledge graph for temporal queries.

## 4. Implementation Plan

### Phase 1: Data Model + Migration (3-5 days)
- Add KG entities + relationships tables
- Alembic migration
- Pydantic schemas for API validation

### Phase 2: Extraction Pipeline (5-7 days)
- Add KG extractor to Deriver pipeline
- Entity resolution / alias merging
- Relationship deduplication (merge same-typed edges, update last_seen)

### Phase 3: Graph Queries (3-5 days)
- BFS/DFS traversal implementation
- API endpoints
- Dialectic tool registration

### Phase 4: Dialectic Integration (2-3 days)
- Add `kg_query` to Dialectic toolset
- Context injection for graph results
- Confidence-weighted response formatting

### Total: ~13-18 days

## 5. Dependencies

- RW InferenceEngine (for entity extraction LLM calls) — ✅ Already deployed
- PostgreSQL (for KG tables) — ✅ Already deployed
- No new infrastructure required

## 6. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Entity extraction LLM cost per message | Self-hosted RW IE with local model; no per-token cost |
| Graph traversal performance at scale | BFS depth capped (default 3, configurable); index on entity names |
| Entity resolution accuracy | Alias list + fuzzy matching; human correction via API |
| Storage growth | Relationship TTL / archiving; periodic KG compaction job |

## 7. Success Metrics

- Multi-hop query accuracy on internal test set > 80%
- Dialectic response quality improvement (user-blind A/B) > 15%
- Graph traversal latency < 200ms for depth-3 queries
- Entity extraction adds < 100ms to Deriver pipeline
