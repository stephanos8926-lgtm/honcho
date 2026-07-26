"""Relationship creation and deduplication for KG extraction.

Handles upsert semantics: if a relationship with the same (workspace,
source, target, type) already exists, update last_seen_at and merge
properties. Otherwise, insert a new relationship.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.kg.models import KGRelationship

logger = logging.getLogger(__name__)


async def create_or_update_relationship(
    db: AsyncSession,
    workspace_name: str,
    *,
    source_entity_id: str,
    target_entity_id: str,
    rel_type: str,
    properties: dict | None = None,
    observation_id: str | None = None,
    confidence: float = 1.0,
) -> KGRelationship:
    """Create or update a relationship using upsert semantics.
    
    The unique constraint on (workspace, source, target, type) prevents
    duplicates under concurrent extraction. On conflict, updates the
    existing relationship's last_seen_at and merges properties.
    
    Returns the (created or updated) KGRelationship.
    """
    now = datetime.now(timezone.utc)

    # Upsert: INSERT ... ON CONFLICT (workspace, source, target, type) DO UPDATE
    stmt = pg_insert(KGRelationship).values(
        workspace_name=workspace_name,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type=rel_type,
        properties=properties or {},
        first_seen_at=now,
        last_seen_at=now,
        observation_id=observation_id,
        confidence=confidence,
    )

    # On conflict, update last_seen_at and merge properties
    stmt = stmt.on_conflict_do_update(
        constraint="uq_kg_relationship",
        set_={
            "last_seen_at": now,
            "confidence": confidence,
            "properties": pg_insert(KGRelationship)
            .excluded.properties,
            "observation_id": observation_id,
        },
    )

    result = await db.execute(stmt)
    await db.commit()

    # Fetch the relationship (either newly inserted or updated)
    stmt = select(KGRelationship).where(
        KGRelationship.workspace_name == workspace_name,
        KGRelationship.source_entity_id == source_entity_id,
        KGRelationship.target_entity_id == target_entity_id,
        KGRelationship.relationship_type == rel_type,
    )
    result = await db.execute(stmt)
    return result.scalar_one()
