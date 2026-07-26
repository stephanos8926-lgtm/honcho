"""Links extracted KG entities to Honcho peers when applicable.

When an entity type is "person" or "agent" and its name matches a peer
in the same workspace, the entity's peer_name field is populated for
cross-referencing between the KG and Honcho's peer model.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.kg.models import KGEntity

logger = logging.getLogger(__name__)

# Minimum confidence threshold for automatic peer linking
PEER_LINK_CONFIDENCE_THRESHOLD = 0.7

# Entity types that can represent peers
PEER_ENTITY_TYPES = frozenset({"person", "agent"})


async def link_entity_to_peer(
    db: AsyncSession,
    workspace_name: str,
    *,
    entity_id: str,
    peer_name: str,
) -> KGEntity | None:
    """Link a KG entity to a Honcho peer by setting peer_name.
    
    Only applies when the entity type is person/agent and confidence
    exceeds the threshold. Returns the updated entity or None if the
    entity doesn't exist or doesn't qualify.
    """
    stmt = select(KGEntity).where(
        KGEntity.id == entity_id,
        KGEntity.workspace_name == workspace_name,
        KGEntity.entity_type.in_(PEER_ENTITY_TYPES),
        KGEntity.confidence >= PEER_LINK_CONFIDENCE_THRESHOLD,
    )
    result = await db.execute(stmt)
    entity = result.scalar_one_or_none()

    if not entity:
        return None

    entity.peer_name = peer_name
    await db.commit()
    logger.info(
        "Linked KG entity %s (%s) to peer %s",
        entity.name,
        entity.entity_type,
        peer_name,
    )
    return entity


async def auto_link_entities_in_workspace(
    db: AsyncSession,
    workspace_name: str,
    peer_names: list[str],
) -> int:
    """Auto-link all unlinked entities matching known peer names.
    
    Scans KG entities with type person/agent that don't have a peer_name
    set and whose name matches a known peer. Returns count of links created.
    """
    link_count = 0
    peer_name_set = frozenset(p.lower() for p in peer_names)

    stmt = select(KGEntity).where(
        KGEntity.workspace_name == workspace_name,
        KGEntity.entity_type.in_(PEER_ENTITY_TYPES),
        KGEntity.peer_name.is_(None),
        KGEntity.confidence >= PEER_LINK_CONFIDENCE_THRESHOLD,
    )
    result = await db.execute(stmt)
    entities = result.scalars().all()

    for entity in entities:
        if entity.name.lower() in peer_name_set:
            entity.peer_name = entity.name
            link_count += 1

    if link_count > 0:
        await db.commit()
        logger.info("Auto-linked %d KG entities to peers", link_count)

    return link_count
