"""Structured-output JSON Schema for KG entity/relationship extraction.

This schema is sent to the LLM as a response_format or tool_use definition
when extracting entities and relationships from messages. It mirrors the
controlled vocabularies in entity_types.py and relationship_types.py.
"""

KG_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Canonical entity name"},
                    "type": {
                        "type": "string",
                        "enum": [
                            "person", "agent", "service", "tool", "project",
                            "concept", "location", "organization", "event", "unknown",
                        ],
                        "description": "Entity type from the KG controlled vocabulary",
                    },
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Alternative names or references for this entity",
                    },
                },
                "required": ["name", "type"],
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Name of the source entity (must match an entity name above)",
                    },
                    "target": {
                        "type": "string",
                        "description": "Name of the target entity (must match an entity name above)",
                    },
                    "type": {
                        "type": "string",
                        "enum": [
                            "depends_on", "manages", "configured_with", "deployed_at",
                            "communicates_with", "part_of", "preceded_by", "caused",
                            "mentioned_in", "related_to",
                        ],
                        "description": "Relationship type from the KG controlled vocabulary",
                    },
                    "properties": {
                        "type": "object",
                        "description": "Optional key-value pairs (e.g., port number, version, URL)",
                        "additionalProperties": True,
                    },
                },
                "required": ["source", "target", "type"],
            },
        },
    },
    "required": ["entities", "relationships"],
}


def validate_extraction_output(output: dict) -> bool:
    """Validate extracted KG data against the type registries.
    
    Raises ValidationException if any entity type or relationship type
    is not in the controlled vocabulary.
    """
    from src.exceptions import ValidationException
    from src.kg.entity_types import validate_entity_type
    from src.kg.relationship_types import validate_relationship_type

    for entity in output.get("entities", []):
        try:
            validate_entity_type(entity.get("type", ""))
        except ValidationException as e:
            raise ValidationException(
                f"Entity '{entity.get('name', '?')}': {e}"
            ) from e

    for rel in output.get("relationships", []):
        try:
            validate_relationship_type(rel.get("type", ""))
        except ValidationException as e:
            raise ValidationException(
                f"Relationship '{rel.get('source', '?')}→{rel.get('target', '?')}': {e}"
            ) from e

    return True
