# Implementation Plan: Knowledge Graph Overlay (SPEC-001 v2.0)

**Feature:** KG-Overlay
**Specification:** `docs/SPECS/SPEC-001-knowledge-graph-overlay.md`
**ADR:** `docs/ADRs/ADR-001-knowledge-graph-overlay.md`
**Planned Effort:** ~23 days (5 phases)
**Architecture:** New tables + Deriver pipeline extension + API endpoints + Dialectic tool

---

## Phase 1: Data Model + Migration (4 days)

### Pre-Phase Research (0.5 days)

**Research Objective:** Understand Honcho's SQLAlchemy patterns, Alembic migration conventions, and pgvector constraints.

**Files to read:**
- `src/models.py` — existing Base model, table definitions, workspace FKs, nanoid defaults
- `src/db.py` — DB connection setup, schema configuration
- `migrations/versions/` — at least 3 existing migration scripts to understand naming conventions, upgrade/downgrade patterns
- `src/schemas/api.py` — Pydantic validation patterns

**Research questions to answer:**
1. How does Base.metadata.schema get populated from settings.DB.SCHEMA?
2. What's the pattern for workspace-scoped foreign keys?
3. How are composite indexes defined in SQLAlchemy 2.x mapped_column style?
4. What's the nanoid generation pattern? (from models.py imports)
5. How do existing Alembic migrations handle pgvector column types?
6. What's the max supported index size on pgvector for HNSW?

**Research output:** Document answers in `docs/research/phase1-schema-patterns.md`

### Task 1.1: Define entity types registry

**Objective:** Create the KG entity types registry module with validation.

**Files:**
- Create: `src/kg/__init__.py` — package init
- Create: `src/kg/entity_types.py` — entity type registry

**Step 1: Write failing test**

```python
# tests/kg/test_entity_types.py
from src.kg.entity_types import KGEntityType, validate_entity_type

def test_valid_entity_types():
    assert validate_entity_type("service") == "service"
    assert validate_entity_type("person") == "person"

def test_invalid_entity_type_raises():
    from src.exceptions import ValidationException
    try:
        validate_entity_type("banana")
        assert False, "Should have raised"
    except ValidationException:
        pass
```

**Step 2: Implement**

```python
# src/kg/entity_types.py
from typing import Literal

from src.exceptions import ValidationException

KGEntityType = Literal[
    "person", "agent", "service", "tool", "project",
    "concept", "location", "organization", "event", "unknown",
]

VALID_ENTITY_TYPES: frozenset[str] = frozenset(KGEntityType.__args__)


def validate_entity_type(t: str) -> KGEntityType:
    if t not in VALID_ENTITY_TYPES:
        raise ValidationException(f"Invalid entity type: {t}")
    return t  # type: ignore
```

**Step 3: Run test** — `pytest tests/kg/test_entity_types.py -xvs`

**Step 4: Commit** — `git add src/kg/ tests/kg/ && git commit -m "kg: add entity types registry"`

### Task 1.2: Define relationship types registry

**Objective:** Create the KG relationship types controlled vocabulary.

**Files:**
- Create: `src/kg/relationship_types.py`

**Step 1: Write failing test**

```python
# tests/kg/test_relationship_types.py
from src.kg.relationship_types import (
    VALID_RELATIONSHIP_TYPES, validate_relationship_type,
)

def test_valid_relationship_types():
    assert "depends_on" in VALID_RELATIONSHIP_TYPES
    assert validate_relationship_type("manages") == "manages"

def test_invalid_relationship_type_raises():
    from src.exceptions import ValidationException
    try:
        validate_relationship_type("foobar")
        assert False
    except ValidationException:
        pass
```

**Step 2: Implement**

```python
# src/kg/relationship_types.py
from src.exceptions import ValidationException

VALID_RELATIONSHIP_TYPES: frozenset[str] = frozenset({
    "depends_on", "manages", "configured_with", "deployed_at",
    "communicates_with", "part_of", "preceded_by", "caused",
    "mentioned_in", "related_to",
})


def validate_relationship_type(t: str) -> str:
    if t not in VALID_RELATIONSHIP_TYPES:
        raise ValidationException(f"Invalid relationship type: {t}")
    return t
```

**Step 3: Run tests** — `pytest tests/kg/ -xvs`

**Step 4: Commit**

### Task 1.3: Add KG tables to SQLAlchemy models

**Objective:** Add KGEntity, KGRelationship, KGQueryLog models to Honcho's ORM layer.

**Files:**
- Create: `src/kg/models.py` — SQLAlchemy ORM models for KG tables
- Modify: `src/models.py` — no changes needed (new package loads models)
- Create: `src/kg/__init__.py` — export all models

**Step 1: Write failing test (schema validation)**

```python
# tests/kg/test_models.py
import pytest
from sqlalchemy import inspect
from src.kg.models import KGEntity, KGRelationship, KGQueryLog

@pytest.mark.asyncio
async def test_kg_tables_exist(db_session):
    """Verify the KG tables were created by Alembic."""
    inspector = inspect(db_session.bind)
    tables = inspector.get_table_names()
    assert "kg_entities" in tables
    assert "kg_relationships" in tables
    assert "kg_query_log" in tables

@pytest.mark.asyncio
async def test_kg_entity_create(db_session):
    entity = KGEntity(
        workspace_name="test_ws",
        name="PostgreSQL",
        entity_type="service",
    )
    db_session.add(entity)
    await db_session.commit()
    fetched = await db_session.get(KGEntity, entity.id)
    assert fetched is not None
    assert fetched.name == "PostgreSQL"
    assert fetched.entity_type == "service"
```

**Step 2: Implement models**

```python
# src/kg/models.py
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from nanoid import generate as generate_nanoid

from src.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KGEntity(Base):
    __tablename__ = "kg_entities"
    
    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_nanoid)
    workspace_name: Mapped[str] = mapped_column(
        String, ForeignKey("workspaces.name"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, default=list)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    peer_name: Mapped[str | None] = mapped_column(String, nullable=True)
    
    __table_args__ = (
        Index("idx_kg_entities_ws_name", "workspace_name", "name"),
        Index("idx_kg_entities_type", "workspace_name", "entity_type"),
    )


class KGRelationship(Base):
    __tablename__ = "kg_relationships"
    
    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_nanoid)
    workspace_name: Mapped[str] = mapped_column(
        String, ForeignKey("workspaces.name"), nullable=False
    )
    source_entity_id: Mapped[str] = mapped_column(
        String, ForeignKey("kg_entities.id"), nullable=False, index=True
    )
    target_entity_id: Mapped[str] = mapped_column(
        String, ForeignKey("kg_entities.id"), nullable=False, index=True
    )
    relationship_type: Mapped[str] = mapped_column(String, nullable=False)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    observation_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("observations.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    
    __table_args__ = (
        Index("idx_kg_rel_source", "workspace_name", "source_entity_id"),
        Index("idx_kg_rel_target", "workspace_name", "target_entity_id"),
        Index("idx_kg_rel_type", "workspace_name", "relationship_type"),
    )


class KGQueryLog(Base):
    __tablename__ = "kg_query_log"
    
    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_nanoid)
    workspace_name: Mapped[str] = mapped_column(String, nullable=False)
    query_entity: Mapped[str] = mapped_column(String)
    max_depth: Mapped[int] = mapped_column(Integer)
    filters_applied: Mapped[dict] = mapped_column(JSONB, default=dict)
    result_count: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    
    __table_args__ = (
        Index("idx_kg_query_log_ws", "workspace_name", "created_at"),
    )
```

**Step 3: Import models in package init**

```python
# src/kg/__init__.py
from src.kg.entity_types import KGEntityType, validate_entity_type
from src.kg.relationship_types import (
    VALID_RELATIONSHIP_TYPES, validate_relationship_type,
)
from src.kg.models import KGEntity, KGRelationship, KGQueryLog

__all__ = [
    "KGEntity", "KGRelationship", "KGQueryLog",
    "KGEntityType", "validate_entity_type",
    "VALID_RELATIONSHIP_TYPES", "validate_relationship_type",
]
```

**Step 4: Run unit tests**

**Step 5: Commit**

### Task 1.4: Create Alembic migration

**Objective:** Generate and verify the migration that creates KG tables.

**Files:**
- Create: `migrations/versions/xxxx_kg_add_entity_relationship_tables.py`

**Step 1: Generate migration**

```bash
uv run alembic revision --autogenerate -m "kg: add entity and relationship tables"
```

**Step 2: Verify generated migration** reads the model definitions from `src/kg/models.py` and creates `kg_entities`, `kg_relationships`, `kg_query_log` tables with proper indexes and foreign keys.

**Step 3: Run migration**

```bash
uv run alembic upgrade head
```

**Step 4: Verify** — `pytest tests/kg/test_models.py -xvs`

**Step 5: Commit**

### Task 1.5: Create Pydantic schemas for API validation

**Objective:** Define request/response schemas for KG API endpoints.

**Files:**
- Create: `src/schemas/kg.py`

**Step 1: Write schema test**

```python
# tests/kg/test_schemas.py
from src.schemas.kg import KGEntityResponse, KGTraverseRequest

def test_kg_traverse_request_valid():
    req = KGTraverseRequest(
        entity="Redis",
        max_depth=3,
        relationship_types=["depends_on"],
    )
    assert req.entity == "Redis"

def test_kg_traverse_request_invalid_depth():
    from pydantic import ValidationError
    try:
        KGTraverseRequest(entity="Redis", max_depth=11)
        assert False
    except ValidationError:
        pass
```

**Step 2: Implement schemas**

```python
# src/schemas/kg.py
from datetime import datetime
from pydantic import BaseModel, Field


class KGEntityResponse(BaseModel):
    id: str
    workspace_name: str
    name: str
    aliases: list[str] = []
    entity_type: str
    first_seen_at: datetime
    last_seen_at: datetime
    confidence: float = 1.0
    peer_name: str | None = None


class KGRelationshipResponse(BaseModel):
    id: str
    workspace_name: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: str
    properties: dict = {}
    first_seen_at: datetime
    last_seen_at: datetime
    confidence: float = 1.0


class KGTraverseRequest(BaseModel):
    entity: str = Field(..., min_length=1, max_length=256)
    max_depth: int = Field(default=3, ge=1, le=10)
    relationship_types: list[str] | None = None
    entity_types: list[str] | None = None
    before: datetime | None = None
    after: datetime | None = None
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    limit: int = Field(default=100, ge=1, le=500)


class KGEntitiesQuery(BaseModel):
    q: str = Field(..., min_length=1)
    type: str | None = None
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    limit: int = Field(default=20, ge=1, le=100)


class KGPathQuery(BaseModel):
    from_: str = Field(alias="from", min_length=1)
    to: str = Field(..., min_length=1)
    max_depth: int = Field(default=5, ge=1, le=10)
    relationship_types: list[str] | None = None
```

**Step 3: Run tests**

**Step 4: Commit**

---

## Phase 2: Extraction Pipeline (7 days)

### Pre-Phase Research (0.5 days)

**Research Objective:** Understand the Deriver pipeline structure and identify the correct KG extraction injection point.

**Files to read:**
- `src/deriver/deriver.py` — main Deriver loop, how it processes work units
- `src/deriver/__main__.py` — if exists, entry point
- `src/crud/representation.py` — how existing observations are created/stored
- `src/llm/` — how structured-output LLM calls work

**Research questions:**
1. What does `process_representation_tasks_batch` look like? Where does it call the observation extraction LLM?
2. How are structured outputs requested from the LLM (response_format, tool_use, etc.)?
3. What retry/timeout logic exists around the extraction LLM call?
4. How do I add a NEW structured-output extraction that runs alongside the existing one?
5. Can KG extraction be async (fire-and-forget after message processing) or must it be synchronous?

**Research output:** `docs/research/phase2-deriver-pipeline.md`

### Task 2.1: Define KG extraction structured-output schema

**Objective:** Create the JSON schema used for KG extraction LLM calls.

**Files:**
- Create: `src/kg/extraction_schema.py`

**Step 1: Write test**

```python
# tests/kg/test_extraction_schema.py
import json
from src.kg.extraction_schema import KG_EXTRACTION_SCHEMA, validate_extraction_output

def test_schema_valid_json():
    assert json.dumps(KG_EXTRACTION_SCHEMA)

def test_valid_extraction_output_passes():
    output = {
        "entities": [{"name": "Redis", "type": "service"}],
        "relationships": [],
    }
    assert validate_extraction_output(output) is True

def test_invalid_entity_type_raises():
    from src.exceptions import ValidationException
    try:
        validate_extraction_output({
            "entities": [{"name": "Redis", "type": "banana"}],
            "relationships": [],
        })
        assert False
    except ValidationException:
        pass
```

**Step 2: Implement**

```python
# src/kg/extraction_schema.py
KG_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["person", "agent", "service", "tool", "project",
                                 "concept", "location", "organization", "event", "unknown"],
                    },
                    "aliases": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "type"],
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["depends_on", "manages", "configured_with",
                                 "deployed_at", "communicates_with", "part_of",
                                 "preceded_by", "caused", "mentioned_in", "related_to"],
                    },
                    "properties": {"type": "object"},
                },
                "required": ["source", "target", "type"],
            },
        },
    },
    "required": ["entities", "relationships"],
}


def validate_extraction_output(output: dict) -> bool:
    from src.kg.entity_types import validate_entity_type
    from src.kg.relationship_types import validate_relationship_type
    
    for entity in output.get("entities", []):
        validate_entity_type(entity.get("type", ""))
    for rel in output.get("relationships", []):
        validate_relationship_type(rel.get("type", ""))
    return True
```

### Task 2.2: Implement entity resolution + alias merging

**Objective:** Create the entity resolution logic that handles deduplication, alias merging, and confidence decay.

**Files:**
- Create: `src/kg/resolver.py`

**Step 1: Write comprehensive tests (3 test functions)**

```python
# tests/kg/test_resolver.py
import pytest
from datetime datetime, timedelta, timezone
from src.kg.resolver import (
    resolve_entity, merge_aliases, decay_confidence, needs_pruning,
)

@pytest.mark.asyncio
async def test_resolve_entity_exact_match(db_session):
    # Create existing entity
    from src.kg.models import KGEntity
    entity = KGEntity(workspace_name="ws", name="Redis", entity_type="service")
    db_session.add(entity)
    await db_session.commit()
    
    result = await resolve_entity(db_session, "ws", "Redis", "service")
    assert result.id == entity.id  # Same entity returned

@pytest.mark.asyncio
async def test_resolve_entity_new_creation(db_session):
    result = await resolve_entity(db_session, "ws", "PostgreSQL", "service")
    assert result is not None
    assert result.name == "PostgreSQL"

def test_merge_aliases():
    existing = ["redis", "redis-server"]
    new = ["Redis", "redis-cache"]
    merged = merge_aliases(existing, new)
    assert "redis-cache" in merged
    assert len(merged) == 3  # Deduped

def test_decay_confidence_halved_after_30_days():
    ts = datetime.now(timezone.utc) - timedelta(days=31)
    assert decay_confidence(1.0, last_seen=ts) < 0.6

def test_needs_pruning_90_days_no_updates():
    ts = datetime.now(timezone.utc) - timedelta(days=91)
    assert needs_pruning(1.0, last_seen=ts) is True
```

**Step 2: Implement resolver**

```python
# src/kg/resolver.py
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.kg.models import KGEntity

logger = logging.getLogger(__name__)

CONFIDENCE_DECAY_DAYS = 30
PRUNE_DAYS = 90
PRUNE_CONFIDENCE_THRESHOLD = 0.1


async def resolve_entity(
    db: AsyncSession,
    workspace_name: str,
    name: str,
    entity_type: str,
    aliases: list[str] | None = None,
) -> KGEntity:
    """Find existing entity or create new one. Supports alias matching."""
    # Exact match first
    stmt = select(KGEntity).where(
        KGEntity.workspace_name == workspace_name,
        KGEntity.name == name,
    )
    result = await db.execute(stmt)
    entity = result.scalar_one_or_none()
    
    if entity:
        # Update last_seen and merge aliases
        entity.last_seen_at = datetime.now(timezone.utc)
        if aliases:
            entity.aliases = list(set(entity.aliases or []) | set(aliases))
        await db.commit()
        return entity
    
    # Fuzzy match on aliases
    if aliases:
        for alias in aliases:
            # This is simplified; production would use trigram similarity
            stmt = select(KGEntity).where(
                KGEntity.workspace_name == workspace_name,
                KGEntity.aliases.contains(alias),
            )
            result = await db.execute(stmt)
            entity = result.scalar_one_or_none()
            if entity:
                entity.last_seen_at = datetime.now(timezone.utc)
                await db.commit()
                return entity
    
    # Create new entity
    entity = KGEntity(
        workspace_name=workspace_name,
        name=name,
        entity_type=entity_type,
        aliases=aliases or [],
    )
    db_session.add(entity)
    await db.commit()
    return entity


def merge_aliases(existing: list[str], new: list[str]) -> list[str]:
    """Merge new aliases into existing list, deduplicating and normalizing."""
    combined = set(a.lower().strip() for a in (existing or []))
    combined.update(a.lower().strip() for a in (new or []))
    return sorted(combined)


def decay_confidence(confidence: float, *, last_seen: datetime) -> float:
    """Halve confidence for every CONFIDENCE_DAYS period without update."""
    days_since = (datetime.now(timezone.utc) - last_seen).days
    periods = days_since // CONFIDENCE_DECAY_DAYS
    if periods <= 0:
        return confidence
    return confidence * (0.5 ** periods)


def needs_pruning(confidence: float, *, last_seen: datetime) -> bool:
    """Entities unseen for PRUNE_DAYS with confidence below threshold."""
    days_since = (datetime.now(timezone.utc) - last_seen).days
    return days_since >= PRUNE_DAYS and confidence < PRUNE_CONFIDENCE_THRESHOLD
```

### Task 2.3: Implement relationship deduplication

**Objective:** Create the relationship deduplication logic.

**Files:**
- Create: `src/kg/relationship_manager.py`

**Step 1: Write test**

```python
# tests/kg/test_relationship_manager.py
@pytest.mark.asyncio
async def test_create_or_update_relationship_new(db_session, kg_entity_a, kg_entity_b):
    from src.kg.relationship_manager import create_or_update_relationship
    rel = await create_or_update_relationship(
        db_session, "ws",
        source_id=kg_entity_a.id,
        target_id=kg_entity_b.id,
        rel_type="depends_on",
    )
    assert rel is not None
    assert rel.relationship_type == "depends_on"

@pytest.mark.asyncio
async def test_create_or_update_relationship_existing(db_session, kg_entity_a, kg_entity_b):
    # Create relationship first, then update — should update last_seen, not duplicate
    rel1 = await create_or_update_relationship(...)
    rel2 = await create_or_update_relationship(...)
    assert rel1.id == rel2.id  # Same relationship updated
```

### Task 2.4: Inject KG extraction into Deriver pipeline

**Objective:** Add the KG extraction step to the Deriver's message processing flow.

**Files:**
- Modify: `src/deriver/deriver.py` (or the file that processes work units)
- Create: `src/kg/extractor.py` — the KG extraction orchestrator

**Step 1: Write test**

```python
# tests/kg/test_extractor.py
@pytest.mark.asyncio
async def test_kg_extract_from_message(db_session, llm_client):
    from src.kg.extractor import extract_kg_from_message
    result = await extract_kg_from_message(
        db_session, llm_client,
        workspace_name="ws",
        message_content="We deployed PostgreSQL on port 5433",
    )
    assert "entities" in result
    assert "relationships" in result
```

**Step 2: Implement extractor**

```python
# src/kg/extractor.py
import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.kg.extraction_schema import KG_EXTRACTION_SCHEMA, validate_extraction_output
from src.kg.resolver import resolve_entity
from src.kg.relationship_manager import create_or_update_relationship

logger = logging.getLogger(__name__)

EXTRACTION_TIMEOUT_SECONDS = 15
MAX_RETRIES = 2


async def extract_kg_from_message(
    db: AsyncSession,
    llm_client: Any,
    workspace_name: str,
    message_content: str,
    observation_id: str | None = None,
) -> dict:
    """Extract entities and relationships from a single message.
    
    Returns {"entities_count": N, "relationships_count": N, "duration_ms": N}.
    Returns empty counts on failure (does not raise — extraction is best-effort).
    """
    start = time.perf_counter()
    
    try:
        # 1. Call LLM for entity/relationship extraction
        extraction = await _call_kg_llm(
            llm_client, message_content, KG_EXTRACTION_SCHEMA
        )
        
        # 2. Validate output
        validate_extraction_output(extraction)
        
        # 3. Resolve entities (dedup + create)
        entity_cache: dict[str, str] = {}  # name -> entity_id
        for entity_data in extraction.get("entities", []):
            entity = await resolve_entity(
                db, workspace_name,
                name=entity_data["name"],
                entity_type=entity_data.get("type", "unknown"),
                aliases=entity_data.get("aliases"),
            )
            entity_cache[entity_data["name"]] = entity.id
        
        # 4. Create/update relationships
        for rel_data in extraction.get("relationships", []):
            source_id = entity_cache.get(rel_data.get("source", ""))
            target_id = entity_cache.get(rel_data.get("target", ""))
            if not source_id or not target_id:
                logger.warning(
                    "KG extraction: skipping relationship with unresolvable entities: %s",
                    rel_data,
                )
                continue
            await create_or_update_relationship(
                db, workspace_name,
                source_id=source_id,
                target_id=target_id,
                rel_type=rel_data["type"],
                properties=rel_data.get("properties"),
                observation_id=observation_id,
            )
        
        duration_ms = int((time.perf_counter() - start) * 1000)
        return {
            "entities_count": len(extraction.get("entities", [])),
            "relationships_count": len(extraction.get("relationships", [])),
            "duration_ms": duration_ms,
        }
    
    except Exception as e:
        logger.warning(
            "KG extraction failed for message (best-effort, skipping): %s", e
        )
        return {"entities_count": 0, "relationships_count": 0, "duration_ms": 0}
```

### Task 2.5: Peer→Entity linking

**Objective:** Link extracted entities to Honcho peer model when applicable.

**Files:**
- Create: `src/kg/peer_linker.py`

**Step 1: Write test**

```python
# tests/kg/test_peer_linker.py
@pytest.mark.asyncio
async def test_link_entity_to_peer(db_session):
    from src.kg.peer_linker import link_entity_to_peer
    entity = await link_entity_to_peer(
        db_session, "ws", entity_id="...", peer_name="alice"
    )
    assert entity.peer_name == "alice"
```

---

## Phase 3: Graph Queries (5 days)

### Pre-Phase Research (0.5 days)

**Research Objective:** Understand Honcho's FastAPI routing conventions, query patterns, and DB session management.

**Files to read:**
- `src/routers/peers.py` — exemplary router file
- `src/dependencies.py` — `tracked_db` and session management
- `src/exceptions.py` — exception handling patterns

### Task 3.1–3.6: Implement traversal, entity resolution, path finding, subgraph, cache, and peer→entity endpoints

[Space-constrained — each task follows same pattern: write test, implement, verify, commit]

---

## Phase 4: Dialectic Integration (3 days)

### Task 4.1: Register `kg_query` tool

**Files:**
- Modify: `src/dialectic/` — tool registration

### Task 4.2–4.3: Context injection, integration tests

---

## Phase 5: Testing + Hardening (4 days)

### Task 5.1–5.4: Unit tests, integration tests, performance benchmarks, adversarial testing
