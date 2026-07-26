"""Comprehensive tests for KG modules.
Does NOT depend on conftest fixtures (pure logic tests where possible,
uses mock DB sessions where needed).
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta


# ═══════════════════════════════════════════════════════════════════
# Entity Types Tests
# ═══════════════════════════════════════════════════════════════════

class TestEntityTypes:
    """Verify the controlled vocabulary is correctly defined and validated."""

    def test_all_literal_members_valid(self):
        from kg.entity_types import KGEntityType, validate_entity_type
        for t in KGEntityType.__args__:
            assert validate_entity_type(t) == t

    def test_invalid_type_rejected(self):
        from kg.entity_types import validate_entity_type
        from exceptions import ValidationException
        with pytest.raises(ValidationException, match="Invalid KG entity type"):
            validate_entity_type("not_a_valid_type")

    def test_all_expected_types_present(self):
        from kg.entity_types import VALID_ENTITY_TYPES
        expected = {"person", "agent", "service", "tool", "project",
                    "concept", "location", "organization", "event", "unknown"}
        assert VALID_ENTITY_TYPES == frozenset(expected)

    def test_entity_type_count(self):
        from kg.entity_types import VALID_ENTITY_TYPES
        assert len(VALID_ENTITY_TYPES) == 10


# ═══════════════════════════════════════════════════════════════════
# Relationship Types Tests
# ═══════════════════════════════════════════════════════════════════

class TestRelationshipTypes:
    """Verify the relationship types vocabulary is correct."""

    def test_all_types_valid(self):
        from kg.relationship_types import VALID_RELATIONSHIP_TYPES, validate_relationship_type
        for t in VALID_RELATIONSHIP_TYPES:
            assert validate_relationship_type(t) == t

    def test_invalid_type_rejected(self):
        from kg.relationship_types import validate_relationship_type
        from exceptions import ValidationException
        with pytest.raises(ValidationException, match="Invalid KG relationship type"):
            validate_relationship_type("not_valid")

    def test_expected_types_present(self):
        from kg.relationship_types import VALID_RELATIONSHIP_TYPES
        core = {"depends_on", "manages", "configured_with", "deployed_at",
                "communicates_with", "part_of", "preceded_by", "caused",
                "mentioned_in", "related_to"}
        assert core.issubset(VALID_RELATIONSHIP_TYPES)

    def test_relationship_type_count(self):
        from kg.relationship_types import VALID_RELATIONSHIP_TYPES
        assert len(VALID_RELATIONSHIP_TYPES) == 10


# ═══════════════════════════════════════════════════════════════════
# Extraction Schema Tests
# ═══════════════════════════════════════════════════════════════════

class TestExtractionSchema:
    """Verify the KG extraction JSON Schema is valid."""

    def test_schema_is_valid_json(self):
        from kg.extraction_schema import KG_EXTRACTION_SCHEMA
        # Should serialize to valid JSON
        serialized = json.dumps(KG_EXTRACTION_SCHEMA)
        parsed = json.loads(serialized)
        assert parsed["type"] == "object"
        assert "entities" in parsed["properties"]
        assert "relationships" in parsed["properties"]

    def test_schema_has_required_fields(self):
        from kg.extraction_schema import KG_EXTRACTION_SCHEMA
        assert "required" in KG_EXTRACTION_SCHEMA
        assert set(KG_EXTRACTION_SCHEMA["required"]) == {"entities", "relationships"}

    def test_valid_extraction_passes_validation(self):
        from kg.extraction_schema import validate_extraction_output
        valid = {
            "entities": [{"name": "Redis", "type": "service"}],
            "relationships": [],
        }
        assert validate_extraction_output(valid) is True

    def test_extraction_with_relationships_valid(self):
        from kg.extraction_schema import validate_extraction_output
        valid = {
            "entities": [
                {"name": "Redis", "type": "service"},
                {"name": "Auth Service", "type": "service"},
            ],
            "relationships": [
                {"source": "Auth Service", "target": "Redis", "type": "depends_on"},
            ],
        }
        assert validate_extraction_output(valid) is True

    def test_invalid_entity_type_fails(self):
        from kg.extraction_schema import validate_extraction_output
        from exceptions import ValidationException
        invalid = {
            "entities": [{"name": "Bad Entity", "type": "invalid_type"}],
            "relationships": [],
        }
        with pytest.raises(ValidationException):
            validate_extraction_output(invalid)


# ═══════════════════════════════════════════════════════════════════
# Resolver Tests
# ═══════════════════════════════════════════════════════════════════

class TestResolver:
    """Test entity resolution logic (pure functions only, no DB)."""

    def test_merge_aliases_dedup(self):
        from kg.resolver import merge_aliases
        existing = ["redis", "redis-server"]
        new = ["Redis", "redis-cache"]
        merged = merge_aliases(existing, new)
        assert "redis" in merged
        assert "redis-cache" in merged
        assert len(merged) == 3  # Deduped

    def test_merge_aliases_no_duplicates(self):
        from kg.resolver import merge_aliases
        result = merge_aliases(["a", "b"], ["b", "c"])
        assert result == ["a", "b", "c"]

    def test_decay_confidence_recent(self):
        from kg.resolver import decay_confidence
        from kg.models import KGEntity
        entity = KGEntity(
            workspace_name="test", name="test", entity_type="service",
            last_seen_at=datetime.now(timezone.utc),
            confidence=1.0,
        )
        # Recent — should not decay
        assert decay_confidence(entity) == 1.0

    def test_decay_confidence_old(self):
        from kg.resolver import decay_confidence, CONFIDENCE_DECAY_DAYS
        from kg.models import KGEntity
        old_date = datetime.now(timezone.utc) - timedelta(days=CONFIDENCE_DECAY_DAYS * 2 + 1)
        entity = KGEntity(
            workspace_name="test", name="test", entity_type="service",
            last_seen_at=old_date,
            confidence=1.0,
        )
        # After 2+ periods, confidence should be <= 0.25
        assert decay_confidence(entity) <= 0.25

    def test_is_dormant_true(self):
        from kg.resolver import is_dormant, PRUNE_DAYS
        from kg.models import KGEntity
        old = datetime.now(timezone.utc) - timedelta(days=PRUNE_DAYS + 1)
        entity = KGEntity(
            workspace_name="test", name="test", entity_type="service",
            last_seen_at=old, confidence=0.05,
        )
        assert is_dormant(entity) is True

    def test_is_dormant_false_recent(self):
        from kg.resolver import is_dormant
        from kg.models import KGEntity
        entity = KGEntity(
            workspace_name="test", name="test", entity_type="service",
            last_seen_at=datetime.now(timezone.utc),
            confidence=1.0,
        )
        assert is_dormant(entity) is False


# ═══════════════════════════════════════════════════════════════════
# Tool Formatting Tests
# ═══════════════════════════════════════════════════════════════════

class TestKGToolFormatting:
    """Verify the kg_query tool formats output correctly."""

    def test_format_traverse_results(self):
        from kg.kg_query_tool import _format_traverse_results
        results = [
            {
                "entity": {"name": "PostgreSQL", "type": "service"},
                "relationship": {"type": "depends_on", "confidence": 0.9},
                "depth": 1,
            },
        ]
        output = _format_traverse_results(results, "Redis")
        assert "Knowledge Graph results for 'Redis'" in output
        assert "PostgreSQL" in output
        assert "depends_on" in output

    def test_format_path_results(self):
        from kg.kg_query_tool import _format_path_results
        path = [
            {"source": "Auth Service", "target": "Redis",
             "relationship_type": "depends_on", "confidence": 0.9},
        ]
        output = _format_path_results(path, "Auth Service", "Redis")
        assert "Path from 'Auth Service' to 'Redis'" in output
        assert "depends_on" in output
        assert "Total hops: 1" in output


# ═══════════════════════════════════════════════════════════════════
# Extraction Output Model Tests
# ═══════════════════════════════════════════════════════════════════

class TestExtractionOutputModel:
    """Verify the Pydantic output model validates correctly."""

    def test_valid_output(self):
        from kg.extraction_output import KGExtractionOutput, KGEntityOutput, KGRelationshipOutput
        output = KGExtractionOutput(
            entities=[KGEntityOutput(name="Redis", type="service")],
            relationships=[
                KGRelationshipOutput(source="Auth", target="Redis", type="depends_on")
            ],
        )
        assert len(output.entities) == 1
        assert len(output.relationships) == 1
        assert output.entities[0].name == "Redis"

    def test_empty_output(self):
        from kg.extraction_output import KGExtractionOutput
        output = KGExtractionOutput()
        assert output.entities == []
        assert output.relationships == []

    def test_model_dump(self):
        from kg.extraction_output import KGExtractionOutput
        output = KGExtractionOutput()
        dumped = output.model_dump()
        assert "entities" in dumped
        assert "relationships" in dumped


# ═══════════════════════════════════════════════════════════════════
# KG Query Tool Definition Tests
# ═══════════════════════════════════════════════════════════════════

class TestKGQueryToolDefinition:
    """Verify the tool definition conforms to expected schema."""

    def test_tool_definition_has_required_fields(self):
        from kg.kg_query_tool import KG_QUERY_TOOL_DEFINITION
        assert KG_QUERY_TOOL_DEFINITION["name"] == "kg_query"
        assert "description" in KG_QUERY_TOOL_DEFINITION
        assert "input_schema" in KG_QUERY_TOOL_DEFINITION

    def test_input_schema_valid(self):
        from kg.kg_query_tool import KG_QUERY_TOOL_DEFINITION
        schema = KG_QUERY_TOOL_DEFINITION["input_schema"]
        # Required params
        assert "entity" in schema["properties"]
        assert "query_type" in schema["properties"]
        assert "entity" in schema.get("required", [])
        assert "query_type" in schema.get("required", [])

    def test_query_type_enum_values(self):
        from kg.kg_query_tool import KG_QUERY_TOOL_DEFINITION
        query_type = KG_QUERY_TOOL_DEFINITION["input_schema"]["properties"]["query_type"]
        assert set(query_type["enum"]) == {"traverse", "find_path"}
