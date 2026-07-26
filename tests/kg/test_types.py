"""Tests for KG entity and relationship type registries."""

from __future__ import annotations

import pytest
from src.kg.entity_types import (
    KGEntityType,
    VALID_ENTITY_TYPES,
    validate_entity_type,
)
from src.kg.relationship_types import (
    VALID_RELATIONSHIP_TYPES,
    validate_relationship_type,
)


class TestEntityTypes:
    def test_all_entity_types_are_valid(self):
        """Every type in the Literal should pass validation."""
        for t in KGEntityType.__args__:
            result = validate_entity_type(t)
            assert result == t

    def test_invalid_entity_type_raises(self):
        """A type not in the registry should raise."""
        from src.exceptions import ValidationException
        with pytest.raises(ValidationException, match="Invalid KG entity type"):
            validate_entity_type("banana")

    def test_empty_string_raises(self):
        from src.exceptions import ValidationException
        with pytest.raises(ValidationException):
            validate_entity_type("")

    def test_entity_types_contain_expected_values(self):
        assert "person" in VALID_ENTITY_TYPES
        assert "service" in VALID_ENTITY_TYPES
        assert "agent" in VALID_ENTITY_TYPES
        assert "unknown" in VALID_ENTITY_TYPES

    def test_entity_types_are_frozenset(self):
        assert isinstance(VALID_ENTITY_TYPES, frozenset)


class TestRelationshipTypes:
    def test_all_relationship_types_are_valid(self):
        for t in VALID_RELATIONSHIP_TYPES:
            result = validate_relationship_type(t)
            assert result == t

    def test_invalid_relationship_type_raises(self):
        from src.exceptions import ValidationException
        with pytest.raises(ValidationException, match="Invalid KG relationship type"):
            validate_relationship_type("foobar")

    def test_relationship_types_contain_expected_values(self):
        assert "depends_on" in VALID_RELATIONSHIP_TYPES
        assert "manages" in VALID_RELATIONSHIP_TYPES
        assert "related_to" in VALID_RELATIONSHIP_TYPES

    def test_relationship_types_are_frozenset(self):
        assert isinstance(VALID_RELATIONSHIP_TYPES, frozenset)
