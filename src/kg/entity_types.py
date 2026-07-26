"""Entity types registry for the Knowledge Graph overlay.

Defines the controlled vocabulary of entity types that can be extracted
from messages and stored in the KG. Types are validated at runtime and
must match the KG extraction LLM schema.

See SPEC-001 v3.0 §3.3 for design rationale.
"""

from typing import Literal

from src.exceptions import ValidationException

KGEntityType = Literal[
    "person",       # Human individual
    "agent",        # AI agent
    "service",      # Software service (Redis, PostgreSQL, etc.)
    "tool",         # CLI tool, library, framework
    "project",      # Software project, codebase
    "concept",      # Abstract concept (authentication, caching)
    "location",     # Physical or network location
    "organization", # Company, team, group
    "event",        # Deployments, incidents, milestones
    "unknown",      # Fallback for unclassified entities
]

VALID_ENTITY_TYPES: frozenset[str] = frozenset(KGEntityType.__args__)


def validate_entity_type(t: str) -> KGEntityType:
    """Validate and return an entity type string.
    
    Raises ValidationException if the type is not in the controlled vocabulary.
    """
    if t not in VALID_ENTITY_TYPES:
        raise ValidationException(
            f"Invalid KG entity type: '{t}'. "
            f"Must be one of: {', '.join(sorted(VALID_ENTITY_TYPES))}"
        )
    return t  # type: ignore[return-value]
