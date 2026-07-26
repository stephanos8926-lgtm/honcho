"""Relationship types registry for the Knowledge Graph overlay.

Defines the controlled vocabulary of relationship types that can be
extracted from messages and stored in the KG.

See SPEC-001 v3.0 §3.4 for design rationale.
"""

from src.exceptions import ValidationException

VALID_RELATIONSHIP_TYPES: frozenset[str] = frozenset({
    "depends_on",         # A depends on B (service dependency, import)
    "manages",            # A manages B (ownership, admin)
    "configured_with",    # A is configured with B (settings, environment)
    "deployed_at",        # A is deployed at B (hosting, location)
    "communicates_with",  # A communicates with B (API, network)
    "part_of",            # A is part of B (hierarchy, composition)
    "preceded_by",        # A happened before B (temporal ordering)
    "caused",             # A caused B (causal relationship)
    "mentioned_in",       # A was mentioned in context of B (association)
    "related_to",         # Generic relationship (fallback)
})


def validate_relationship_type(t: str) -> str:
    """Validate and return a relationship type string.
    
    Raises ValidationException if the type is not in the controlled vocabulary.
    """
    if t not in VALID_RELATIONSHIP_TYPES:
        raise ValidationException(
            f"Invalid KG relationship type: '{t}'. "
            f"Must be one of: {', '.join(sorted(VALID_RELATIONSHIP_TYPES))}"
        )
    return t
