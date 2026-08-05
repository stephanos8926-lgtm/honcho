"""KG API router — entity/relationship queries, traversal, pathfinding.

Exposes the Knowledge Graph overlay via Honcho's standard API patterns.
All endpoints are workspace-scoped and require standard Honcho auth.

See SPEC-001 v3.0 §3.5 for endpoint specifications.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src import schemas
from src.dependencies import get_read_db, tracked_db
from src.exceptions import ResourceNotFoundException, ValidationException
from src.kg.graph import find_path, subgraph, traverse
from src.kg.models import KGEntity, KGRelationship
from src.kg.peer_linker import auto_link_entities_in_workspace
from src.security import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v3/workspaces/{workspace_id}/kg",
    tags=["knowledge-graph"],
    dependencies=[Depends(require_auth(workspace_name="workspace_id"))],
)


@router.get("/traverse")
async def kg_traverse(
    workspace_id: str = Path(...),
    entity: str = Query(..., min_length=1, max_length=256),
    max_depth: int = Query(default=3, ge=1, le=10),
    relationship_types: str | None = Query(default=None),
    entity_types: str | None = Query(default=None),
    before: datetime | None = Query(default=None),
    after: datetime | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=500),
    db_session: AsyncSession = Depends(get_read_db),
):
    """BFS traversal from an entity through the knowledge graph.
    
    Returns entities and relationships at each depth level, filtered by
    optional relationship types, entity types, time bounds, and confidence.
    """
    parsed_rel_types = (
        relationship_types.split(",") if relationship_types else None
    )
    parsed_ent_types = (
        entity_types.split(",") if entity_types else None
    )

    results = await traverse(
        db_session,
        workspace_id,
        entity,
        max_depth=max_depth,
        relationship_types=parsed_rel_types,
        entity_types=parsed_ent_types,
        before=before,
        after=after,
        min_confidence=min_confidence,
        limit=limit,
    )
    return {"results": results, "total": len(results)}


@router.get("/entities")
async def kg_search_entities(
    workspace_id: str = Path(...),
    q: str = Query(..., min_length=1),
    type: str | None = Query(default=None, alias="entity_type"),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=20, ge=1, le=100),
    include_dormant: bool = Query(default=False),
    db_session: AsyncSession = Depends(get_read_db),
):
    """Fuzzy search for entities by name or alias.
    
    Uses pg_trgm similarity when available, falls back to ILIKE.
    By default excludes dormant entities (confidence < 0.1, unseen > 90 days).
    """
    from sqlalchemy import or_, select

    stmt = select(KGEntity).where(
        KGEntity.workspace_name == workspace_id,
    )

    # Search by name or aliases
    stmt = stmt.where(
        or_(
            KGEntity.name.ilike(f"%{q}%"),
            KGEntity.aliases.contains([q]),
        )
    )

    if type:
        stmt = stmt.where(KGEntity.entity_type == type)

    if min_confidence > 0:
        stmt = stmt.where(KGEntity.confidence >= min_confidence)

    if not include_dormant:
        stmt = stmt.where(KGEntity.confidence >= 0.1)

    stmt = stmt.order_by(KGEntity.confidence.desc()).limit(limit)
    result = await db_session.execute(stmt)
    entities = result.scalars().all()

    return {
        "entities": [
            {
                "id": e.id,
                "name": e.name,
                "type": e.entity_type,
                "aliases": e.aliases,
                "confidence": e.confidence,
                "peer_name": e.peer_name,
                "mention_count": e.mention_count,
                "first_seen_at": e.first_seen_at.isoformat() if e.first_seen_at else None,
                "last_seen_at": e.last_seen_at.isoformat() if e.last_seen_at else None,
            }
            for e in entities
        ],
        "total": len(entities),
    }


@router.get("/path")
async def kg_find_path(
    workspace_id: str = Path(...),
    from_: str = Query(..., alias="from", min_length=1),
    to: str = Query(..., min_length=1),
    max_depth: int = Query(default=5, ge=1, le=10),
    relationship_types: str | None = Query(default=None),
    db_session: AsyncSession = Depends(get_read_db),
):
    """Find the shortest path between two entities."""
    parsed_types = (
        relationship_types.split(",") if relationship_types else None
    )

    path = await find_path(
        db_session,
        workspace_id,
        from_,
        to,
        max_depth=max_depth,
        relationship_types=parsed_types,
    )

    if not path:
        return {"path": [], "found": False}

    return {"path": path, "found": True, "hops": len(path)}


@router.get("/subgraph")
async def kg_subgraph(
    workspace_id: str = Path(...),
    entity: str = Query(..., min_length=1),
    depth: int = Query(default=2, ge=1, le=5),
    limit: int = Query(default=100, ge=1, le=500),
    db_session: AsyncSession = Depends(get_read_db),
):
    """Export a subgraph neighborhood for visualization or caching."""
    result = await subgraph(
        db_session,
        workspace_id,
        entity,
        depth=depth,
        limit=limit,
    )
    return result


@router.get("/peer-entities")
async def kg_peer_entities(
    workspace_id: str = Path(...),
    peer_name: str = Query(..., min_length=1),
    relationship_types: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db_session: AsyncSession = Depends(get_read_db),
):
    """Find entities linked to a specific peer."""
    from sqlalchemy import select

    stmt = select(KGEntity).where(
        KGEntity.workspace_name == workspace_id,
        KGEntity.peer_name == peer_name,
    )

    if relationship_types:
        # Also join through relationships to filter by type
        parsed_types = relationship_types.split(",")
        from sqlalchemy import join
        stmt = (
            select(KGEntity)
            .join(
                KGRelationship,
                KGEntity.id == KGRelationship.target_entity_id,
            )
            .where(
                KGEntity.workspace_name == workspace_id,
                KGRelationship.relationship_type.in_(parsed_types),
            )
        )

    stmt = stmt.limit(limit)
    result = await db_session.execute(stmt)
    entities = result.scalars().all()

    return {
        "entities": [
            {
                "id": e.id,
                "name": e.name,
                "type": e.entity_type,
                "confidence": e.confidence,
                "peer_name": e.peer_name,
            }
            for e in entities
        ],
        "total": len(entities),
    }


@router.post("/auto-link")
async def kg_auto_link(
    workspace_id: str = Path(...),
    db_session: AsyncSession = Depends(get_read_db),
):
    """Auto-link unlinked person/agent entities to workspace peers.
    
    Scans all KG entities with type 'person' or 'agent' that don't have
    a peer_name set and whose name matches a known workspace peer.
    """
    # Get list of peer names from the workspace
    from src import models
    from sqlalchemy import select as sel

    stmt = sel(models.Peer.name).where(
        models.Peer.workspace_name == workspace_id
    )
    result = await db_session.execute(stmt)
    peer_names = [row[0] for row in result]

    if not peer_names:
        return {"linked": 0, "total_peers": 0}

    linked = await auto_link_entities_in_workspace(
        db_session, workspace_id, peer_names
    )
    return {"linked": linked, "total_peers": len(peer_names)}


@router.patch("/entities/{entity_id}")
async def kg_update_entity(
    workspace_id: str = Path(...),
    entity_id: str = Path(...),
    name: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    peer_name: str | None = Query(default=None),
    db_session: AsyncSession = Depends(get_read_db),
):
    """Update a KG entity's metadata (human correction endpoint).
    
    Allows operators to correct entity name, type, or peer linkage
    that the automated extraction got wrong.
    """
    from sqlalchemy import select as sel, update as upd

    stmt = sel(KGEntity).where(
        KGEntity.id == entity_id,
        KGEntity.workspace_name == workspace_id,
    )
    result = await db_session.execute(stmt)
    entity = result.scalar_one_or_none()

    if not entity:
        raise ResourceNotFoundException(
            f"KG entity {entity_id} not found in workspace {workspace_id}"
        )

    from src.kg.entity_types import validate_entity_type

    update_values = {}
    if name is not None:
        update_values["name"] = name
    if entity_type is not None:
        update_values["entity_type"] = validate_entity_type(entity_type)
    if peer_name is not None:
        update_values["peer_name"] = peer_name if peer_name else None

    if update_values:
        upd_stmt = (
            upd(KGEntity)
            .where(KGEntity.id == entity_id)
            .values(**update_values)
        )
        await db_session.execute(upd_stmt)
        await db_session.commit()

    return {"updated": True, "fields": list(update_values.keys())}
