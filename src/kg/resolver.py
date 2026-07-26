"""Entity resolution and alias merging for KG extraction.

Handles deduplication, fuzzy matching via pg_trgm, confidence decay,
and pruning of stale/low-confidence entities.

See SPEC-001 v3.0 §3.2 for design.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.kg.models import KGEntity

logger = logging.getLogger(__name__)

# Confidence decay constants
CONFIDENCE_DECAY_DAYS = 30
PRUNE_DAYS = 90
PRUNE_CONFIDENCE_THRESHOLD = 0.1

# Trigram similarity threshold for fuzzy alias matching
TRIGRAM_THRESHOLD = 0.6


async def resolve_entity(
    db: AsyncSession,
    workspace_name: str,
    name: str,
    entity_type: str,
    aliases: list[str] | None = None,
) -> KGEntity:
    """Find existing entity by exact name or fuzzy alias match, or create new.
    
    Resolution order:
    1. Exact match on (workspace_name, name) → update last_seen, merge aliases
    2. Trigram match on aliases → merge into matched entity
    3. No match → create new entity
    
    Returns the resolved KGEntity (persisted, committed).
    """
    now = datetime.now(timezone.utc)

    # Strategy 1: Exact match on canonical name
    stmt = select(KGEntity).where(
        KGEntity.workspace_name == workspace_name,
        KGEntity.name == name,
    )
    result = await db.execute(stmt)
    entity = result.scalar_one_or_none()

    if entity:
        _update_existing(entity, aliases, now)
        await db.commit()
        return entity

    # Strategy 2: Trigram fuzzy match on aliases (requires pg_trgm extension)
    if aliases:
        from sqlalchemy import func as sa_func

        for alias in aliases:
            # pg_trgm similarity query
            alias_clean = alias.lower().strip()
            stmt = (
                select(KGEntity)
                .where(KGEntity.workspace_name == workspace_name)
                .where(
                    sa_func.similarity(
                        sa_func.array_to_string(KGEntity.aliases, " "),
                        alias_clean,
                    )
                    > TRIGRAM_THRESHOLD
                )
                .order_by(
                    sa_func.similarity(
                        sa_func.array_to_string(KGEntity.aliases, " "),
                        alias_clean,
                    ).desc()
                )
                .limit(1)
            )
            result = await db.execute(stmt)
            entity = result.scalar_one_or_none()
            if entity:
                _update_existing(entity, aliases, now)
                await db.commit()
                return entity

    # Strategy 3: Create new entity
    entity = KGEntity(
        workspace_name=workspace_name,
        name=name,
        entity_type=entity_type,
        aliases=aliases or [],
        first_seen_at=now,
        last_seen_at=now,
        confidence=1.0,
    )
    db.add(entity)
    await db.commit()
    return entity


def _update_existing(
    entity: KGEntity,
    new_aliases: list[str] | None,
    now: datetime,
) -> None:
    """Update an existing entity's metadata and merge aliases."""
    entity.last_seen_at = now
    entity.mention_count = (entity.mention_count or 0) + 1

    if new_aliases:
        existing_set = set(a.lower().strip() for a in (entity.aliases or []))
        existing_set.update(a.lower().strip() for a in new_aliases)
        entity.aliases = sorted(existing_set)


def decay_confidence(entity: KGEntity) -> float:
    """Halve confidence for each CONFIDENCE_DECAY_DAYS without an update.
    
    Returns the decayed confidence value (does NOT persist).
    """
    if not entity.last_seen_at:
        return entity.confidence
    days_since = (datetime.now(timezone.utc) - entity.last_seen_at).days
    periods = max(0, days_since // CONFIDENCE_DECAY_DAYS)
    return entity.confidence * (0.5**periods)


def is_dormant(entity: KGEntity) -> bool:
    """Check if an entity should be marked dormant.
    
    Dormant = unseen for > PRUNE_DAYS AND confidence below threshold after decay.
    Dormant entities are excluded from normal queries but can be explicitly
    searched.
    """
    if not entity.last_seen_at:
        return False
    days_since = (datetime.now(timezone.utc) - entity.last_seen_at).days
    if days_since < PRUNE_DAYS:
        return False
    return decay_confidence(entity) < PRUNE_CONFIDENCE_THRESHOLD


def merge_aliases(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """Merge new aliases into an existing list with dedup and normalization."""
    result = set()
    if existing:
        result.update(a.lower().strip() for a in existing)
    if new:
        result.update(a.lower().strip() for a in new)
    return sorted(result)
