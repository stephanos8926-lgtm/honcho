"""SQLAlchemy ORM models for the Knowledge Graph overlay.

Defines KGEntity, KGRelationship, and KGQueryLog tables that store
extracted entities, typed relationships between them, and query telemetry.

See SPEC-001 v3.0 §3.1 for full schema documentation.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nanoid import generate as generate_nanoid

from src.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KGEntity(Base):
    """A named entity extracted from messages/observations.

    Entities are workspace-scoped. Cross-workspace resolution is handled
    by alias matching (pg_trgm), not by shared IDs.

    When an entity corresponds to a Honcho peer (user/agent), the peer_name
    field links it to the peer model, enabling peer→KG cross-referencing.
    """

    __tablename__ = "kg_entities"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=generate_nanoid
    )
    workspace_name: Mapped[str] = mapped_column(
        String, ForeignKey("workspaces.name"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    aliases: Mapped[list] = mapped_column(JSONB, default=list)
    entity_type: Mapped[str] = mapped_column(
        String, nullable=False, index=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    peer_name: Mapped[str | None] = mapped_column(String, nullable=True)
    mention_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("idx_kg_entities_ws_name", "workspace_name", "name"),
        Index("idx_kg_entities_type", "workspace_name", "entity_type"),
    )


class KGRelationship(Base):
    """A typed, directed edge between two entities.

    Relationship types come from a controlled vocabulary (see
    relationship_types.py). The (workspace, source, target, type) composite
    unique constraint prevents duplicates under concurrent extraction.
    """

    __tablename__ = "kg_relationships"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=generate_nanoid
    )
    workspace_name: Mapped[str] = mapped_column(
        String, ForeignKey("workspaces.name"), nullable=False
    )
    source_entity_id: Mapped[str] = mapped_column(
        String, ForeignKey("kg_entities.id"), nullable=False, index=True
    )
    target_entity_id: Mapped[str] = mapped_column(
        String, ForeignKey("kg_entities.id"), nullable=False, index=True
    )
    relationship_type: Mapped[str] = mapped_column(
        String, nullable=False, index=True
    )
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    observation_id: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    __table_args__ = (
        Index("idx_kg_rel_source", "workspace_name", "source_entity_id"),
        Index("idx_kg_rel_target", "workspace_name", "target_entity_id"),
        Index("idx_kg_rel_type", "workspace_name", "relationship_type"),
        Index(
            "idx_kg_rel_traverse",
            "workspace_name",
            "relationship_type",
            "source_entity_id",
            "target_entity_id",
        ),
        UniqueConstraint(
            "workspace_name",
            "source_entity_id",
            "target_entity_id",
            "relationship_type",
            name="uq_kg_relationship",
        ),
    )


class KGQueryLog(Base):
    """Telemetry for graph query performance and cache warming.

    Written on both extraction and query paths. Used for cache warming
    on startup by replaying the most frequent queries.
    """

    __tablename__ = "kg_query_log"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=generate_nanoid
    )
    workspace_name: Mapped[str] = mapped_column(
        String, nullable=False
    )
    query_fingerprint: Mapped[str] = mapped_column(
        String, nullable=False, index=True
    )
    max_depth: Mapped[int] = mapped_column(Integer)
    filters_applied: Mapped[dict] = mapped_column(JSONB, default=dict)
    result_count: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("idx_kg_query_log_ws", "workspace_name", "created_at"),
    )
