"""Graph traversal, path finding, and subgraph extraction for the KG overlay.

Provides BFS-based traversal with cycle detection, shortest path finding,
and subgraph neighborhood export. All functions operate on the KG tables
via SQLAlchemy async sessions.
"""

import logging
from collections import deque
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.kg.models import KGEntity, KGRelationship

logger = logging.getLogger(__name__)

# Default traversal limits
MAX_TRAVERSE_DEPTH = 6
MAX_PATH_DEPTH = 10
MAX_SUBGRAPH_DEPTH = 3
MAX_RESULTS = 500


async def traverse(
    db: AsyncSession,
    workspace_name: str,
    entity_name: str,
    *,
    max_depth: int = 3,
    relationship_types: list[str] | None = None,
    entity_types: list[str] | None = None,
    before: datetime | None = None,
    after: datetime | None = None,
    min_confidence: float = 0.0,
    limit: int = 100,
) -> list[dict]:
    """BFS traversal from a starting entity through the KG.
    
    Returns a list of (entity, [relationships]) dicts representing the
    traversed subgraph. Each entry includes the entity and its relationships
    to other entities at the next depth level.
    
    Cycle detection via visited set of entity IDs.
    """
    # Resolve starting entity
    stmt = select(KGEntity).where(
        KGEntity.workspace_name == workspace_name,
        KGEntity.name == entity_name,
    )
    result = await db.execute(stmt)
    start_entity = result.scalar_one_or_none()

    if not start_entity:
        return []

    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque()
    queue.append((start_entity.id, 0))
    visited.add(start_entity.id)

    results: list[dict] = []
    
    # Pre-build filter clauses for efficiency
    type_filter = (
        [KGRelationship.relationship_type.in_(relationship_types)]
        if relationship_types
        else []
    )

    while queue and len(results) < limit:
        current_id, depth = queue.popleft()

        if depth >= max_depth:
            continue

        # Fetch outgoing relationships from current entity
        rel_query = select(KGRelationship).where(
            KGRelationship.workspace_name == workspace_name,
            KGRelationship.source_entity_id == current_id,
            *type_filter,
        )

        if before:
            rel_query = rel_query.where(
                KGRelationship.last_seen_at <= before
            )
        if after:
            rel_query = rel_query.where(
                KGRelationship.first_seen_at >= after
            )
        if min_confidence > 0:
            rel_query = rel_query.where(
                KGRelationship.confidence >= min_confidence
            )

        rel_query = rel_query.limit(MAX_RESULTS)
        result = await db.execute(rel_query)
        relationships = result.scalars().all()

        for rel in relationships:
            target_id = rel.target_entity_id

            if target_id not in visited:
                visited.add(target_id)
                queue.append((target_id, depth + 1))

                # Fetch target entity
                ent_stmt = select(KGEntity).where(KGEntity.id == target_id)
                ent_result = await db.execute(ent_stmt)
                target_entity = ent_result.scalar_one_or_none()

                if target_entity:
                    # Apply entity type filter if specified
                    if entity_types and target_entity.entity_type not in entity_types:
                        continue

                    results.append({
                        "entity": {
                            "id": target_entity.id,
                            "name": target_entity.name,
                            "type": target_entity.entity_type,
                            "confidence": target_entity.confidence,
                        },
                        "relationship": {
                            "type": rel.relationship_type,
                            "properties": rel.properties,
                            "confidence": rel.confidence,
                            "first_seen_at": rel.first_seen_at.isoformat() if rel.first_seen_at else None,
                            "last_seen_at": rel.last_seen_at.isoformat() if rel.last_seen_at else None,
                        },
                        "depth": depth + 1,
                    })

    return results[:limit]


async def find_path(
    db: AsyncSession,
    workspace_name: str,
    from_entity: str,
    to_entity: str,
    *,
    max_depth: int = 5,
    relationship_types: list[str] | None = None,
) -> list[dict]:
    """BFS shortest path finding between two entities.
    
    Returns the path as a list of hops, each containing source entity,
    target entity, and relationship. Empty list if no path exists within
    max_depth.
    """
    # Resolve both entities
    from_stmt = select(KGEntity).where(
        KGEntity.workspace_name == workspace_name,
        KGEntity.name == from_entity,
    )
    to_stmt = select(KGEntity).where(
        KGEntity.workspace_name == workspace_name,
        KGEntity.name == to_entity,
    )

    from_result = await db.execute(from_stmt)
    to_result = await db.execute(to_stmt)
    from_ent = from_result.scalar_one_or_none()
    to_ent = to_result.scalar_one_or_none()

    if not from_ent or not to_ent:
        return []

    # BFS with parent tracking
    visited: set[str] = set()
    # parent_map: neighbor_id -> (current_id, relationship, is_outgoing)
    parent: dict[str, tuple[str, KGRelationship, bool]] = {}
    queue: deque[tuple[str, int]] = deque()
    queue.append((from_ent.id, 0))
    visited.add(from_ent.id)

    type_filter = (
        [KGRelationship.relationship_type.in_(relationship_types)]
        if relationship_types
        else []
    )

    found = False
    while queue and not found:
        current_id, depth = queue.popleft()

        if depth >= max_depth:
            continue

        # Check BOTH directions: outgoing and incoming relationships
        rel_query = select(KGRelationship).where(
            KGRelationship.workspace_name == workspace_name,
            or_(
                KGRelationship.source_entity_id == current_id,
                KGRelationship.target_entity_id == current_id,
            ),
            *type_filter,
        )
        result = await db.execute(rel_query)
        relationships = result.scalars().all()

        for rel in relationships:
            # Determine the connected entity ID based on direction
            is_outgoing = (rel.source_entity_id == current_id)
            neighbor_id = rel.target_entity_id if is_outgoing else rel.source_entity_id

            if neighbor_id not in visited:
                visited.add(neighbor_id)
                parent[neighbor_id] = (current_id, rel, is_outgoing)
                queue.append((neighbor_id, depth + 1))

                if neighbor_id == to_ent.id:
                    found = True
                    break

    if not found:
        return []

    # Reconstruct path from target back to source
    path: list[dict] = []
    current = to_ent.id
    while current in parent:
        source_id, rel, is_outgoing = parent[current]
        
        # Determine display direction based on relationship orientation
        if is_outgoing:
            disp_source, disp_target = source_id, current
        else:
            disp_source, disp_target = current, source_id
        
        # Fetch entity names for display
        s = await db.execute(select(KGEntity).where(KGEntity.id == disp_source))
        t = await db.execute(select(KGEntity).where(KGEntity.id == disp_target))
        source_entity = s.scalar_one_or_none()
        target_entity = t.scalar_one_or_none()

        path.insert(0, {
            "source": source_entity.name if source_entity else disp_source,
            "target": target_entity.name if target_entity else disp_target,
            "relationship_type": rel.relationship_type,
            "properties": rel.properties,
            "confidence": rel.confidence,
        })
        current = source_id

    return path


async def subgraph(
    db: AsyncSession,
    workspace_name: str,
    entity_name: str,
    *,
    depth: int = 2,
    limit: int = 100,
) -> dict:
    """Export a complete subgraph neighborhood around an entity.
    
    Returns a dict with 'entities' and 'relationships' lists suitable for
    JSON serialization or visualization (e.g., Mermaid, D3.js).
    """
    entities_map: dict[str, dict] = {}
    edges: list[dict] = []
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque()

    # Resolve starting entity
    stmt = select(KGEntity).where(
        KGEntity.workspace_name == workspace_name,
        KGEntity.name == entity_name,
    )
    result = await db.execute(stmt)
    start = result.scalar_one_or_none()
    if not start:
        return {"entities": [], "relationships": []}

    queue.append((start.id, 0))
    visited.add(start.id)

    while queue and len(entities_map) < limit:
        current_id, d = queue.popleft()

        if d >= depth:
            continue

        # Fetch entity
        e = await db.execute(select(KGEntity).where(KGEntity.id == current_id))
        entity = e.scalar_one()
        if current_id not in entities_map:
            entities_map[current_id] = {
                "id": entity.id,
                "name": entity.name,
                "type": entity.entity_type,
                "confidence": entity.confidence,
            }

        # Fetch relationships with a hard cap
        r = await db.execute(
            select(KGRelationship).where(
                KGRelationship.workspace_name == workspace_name,
                KGRelationship.source_entity_id == current_id,
            ).limit(limit)
        )
        for rel in r.scalars().all():
            if len(edges) >= limit:
                break
            edges.append({
                "source": rel.source_entity_id,
                "target": rel.target_entity_id,
                "type": rel.relationship_type,
                "confidence": rel.confidence,
            })
            if rel.target_entity_id not in visited:
                visited.add(rel.target_entity_id)
                queue.append((rel.target_entity_id, d + 1))

    return {
        "entities": list(entities_map.values()),
        "relationships": edges,
    }
