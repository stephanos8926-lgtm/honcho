"""Pydantic schemas for KG API request/response validation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class KGEntityResponse(BaseModel):
    """Response schema for a single KG entity."""

    id: str
    workspace_name: str
    name: str
    aliases: list[str] = []
    entity_type: str
    first_seen_at: datetime
    last_seen_at: datetime
    confidence: float = 1.0
    peer_name: str | None = None
    mention_count: int = 0


class KGEntityListResponse(BaseModel):
    """Response schema for entity search results."""

    entities: list[KGEntityResponse]
    total: int


class KGRelationshipResponse(BaseModel):
    """Response schema for a single KG relationship."""

    id: str
    workspace_name: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: str
    properties: dict[str, Any] = {}
    first_seen_at: datetime
    last_seen_at: datetime
    confidence: float = 1.0


class KGTraverseRequest(BaseModel):
    """Request schema for graph traversal queries."""

    entity: str = Field(..., min_length=1, max_length=256)
    max_depth: int = Field(default=3, ge=1, le=10)
    relationship_types: list[str] | None = None
    entity_types: list[str] | None = None
    before: datetime | None = None
    after: datetime | None = None
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    limit: int = Field(default=100, ge=1, le=500)


class KGEntitiesQuery(BaseModel):
    """Request schema for entity resolution/fuzzy search."""

    q: str = Field(..., min_length=1, max_length=256)
    type: str | None = None
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    limit: int = Field(default=20, ge=1, le=100)


class KGPathQuery(BaseModel):
    """Request schema for path finding between entities."""

    from_: str = Field(alias="from", min_length=1, max_length=256)
    to: str = Field(..., min_length=1, max_length=256)
    max_depth: int = Field(default=5, ge=1, le=10)
    relationship_types: list[str] | None = None


class KGSubgraphRequest(BaseModel):
    """Request schema for subgraph snapshots."""

    entity: str = Field(..., min_length=1)
    depth: int = Field(default=2, ge=1, le=5)
    format: str = Field(default="json", pattern="^(json|cypher)$")


class KGPeerEntitiesQuery(BaseModel):
    """Request schema for peer→entity linking queries."""

    peer_name: str = Field(..., min_length=1)
    relationship_types: list[str] | None = None
    limit: int = Field(default=50, ge=1, le=500)
