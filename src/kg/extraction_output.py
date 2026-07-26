"""Pydantic model for KG extraction LLM structured output.

Used as the response_model for honcho_llm_call during KG extraction.
Mirrors the JSON Schema in extraction_schema.py but as a typed Pydantic
model for integration with Honcho's LLM client.
"""

from pydantic import BaseModel, Field


class KGEntityOutput(BaseModel):
    """A single entity extracted from a message."""

    name: str = Field(description="Canonical entity name")
    type: str = Field(
        description="Entity type: person, agent, service, tool, project, "
        "concept, location, organization, event, unknown"
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative names or references for this entity",
    )


class KGRelationshipOutput(BaseModel):
    """A typed relationship between two entities."""

    source: str = Field(description="Name of the source entity")
    target: str = Field(description="Name of the target entity")
    type: str = Field(
        description="Relationship type: depends_on, manages, configured_with, "
        "deployed_at, communicates_with, part_of, preceded_by, caused, "
        "mentioned_in, related_to"
    )
    properties: dict = Field(
        default_factory=dict,
        description="Optional key-value pairs (e.g., port, version, URL)",
    )


class KGExtractionOutput(BaseModel):
    """Structured output from the KG extraction LLM call."""

    entities: list[KGEntityOutput] = Field(
        default_factory=list,
        description="Entities extracted from the message",
    )
    relationships: list[KGRelationshipOutput] = Field(
        default_factory=list,
        description="Relationships between extracted entities",
    )
