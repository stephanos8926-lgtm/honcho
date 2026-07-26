"""Standalone tests for KG entity and relationship type registries."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

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
from src.exceptions import ValidationException


class TestEntityTypesStandalone:
    def test_all_valid(self):
        for t in KGEntityType.__args__:
            assert validate_entity_type(t) == t

    def test_invalid_raises(self):
        with pytest.raises(ValidationException, match="Invalid KG entity type"):
            validate_entity_type("banana")

    def test_core_types_present(self):
        assert "person" in VALID_ENTITY_TYPES
        assert "service" in VALID_ENTITY_TYPES
        assert "agent" in VALID_ENTITY_TYPES


class TestRelationshipTypesStandalone:
    def test_all_valid(self):
        for t in VALID_RELATIONSHIP_TYPES:
            assert validate_relationship_type(t) == t

    def test_invalid_raises(self):
        with pytest.raises(ValidationException, match="Invalid KG relationship type"):
            validate_relationship_type("foobar")

    def test_core_types_present(self):
        assert "depends_on" in VALID_RELATIONSHIP_TYPES
        assert "manages" in VALID_RELATIONSHIP_TYPES
