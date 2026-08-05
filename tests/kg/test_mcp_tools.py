"""Tests for KG MCP tool wrappers (SPEC-003).

Does NOT depend on conftest fixtures. Uses mock DB sessions matching
the pattern from test_comprehensive.py.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ═══════════════════════════════════════════════════════════════════
# Mock helpers
# ═══════════════════════════════════════════════════════════════════

def make_mock_ctx(workspace_name="test-workspace"):
    """Create a mock ToolContext for testing handlers."""
    ctx = MagicMock()
    ctx.workspace_name = workspace_name
    return ctx


def make_mock_entity(**kwargs):
    """Create a mock KGEntity with all required fields."""
    defaults = {
        "id": "test-id-123",
        "workspace_name": "test-workspace",
        "name": "Test Entity",
        "aliases": ["test", "te"],
        "entity_type": "service",
        "confidence": 0.95,
        "peer_name": None,
        "mention_count": 5,
        "first_seen_at": None,
        "last_seen_at": None,
    }
    defaults.update(kwargs)
    return MagicMock(**defaults)


def make_mock_db(scalar_return=5, execute_return=None):
    """Factory for a mock DB session returned by tracked_db.

    Returns an async context manager that yields a mock DB session
    with configurable scalar() and execute() return values.

    Notes:
        - db must be AsyncMock (scalar/execute are async)
        - BUT db.execute() returns a sync Result object
        - SO execute.return_value must be MagicMock (not AsyncMock)
    """
    mock_db = AsyncMock()
    mock_db.scalar.return_value = scalar_return
    if execute_return is not None:
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = execute_return
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

    # Create an async context manager that returns mock_db
    cm = MagicMock()
    cm.__aenter__.return_value = mock_db
    cm.__aexit__.return_value = None
    return cm


def _kg_with_patched_db(cm):
    """Return the kg.kg_query_tool module with tracked_db replaced.

    Uses patch.object to ensure the module-level name is replaced
    in the handler's runtime namespace.
    """
    import kg.kg_query_tool
    if cm is not None:
        patcher = patch.object(kg.kg_query_tool, "tracked_db", cm)
        patcher.start()
    return kg.kg_query_tool


# ═══════════════════════════════════════════════════════════════════
# kg_entity_search tests
# ═══════════════════════════════════════════════════════════════════

class TestKgEntitySearch:

    @pytest.mark.asyncio
    async def test_finds_by_name_ilike(self):
        """Entity search finds by name ILIKE match."""
        mock_entity = make_mock_entity(name="AuthService", entity_type="service")
        cm = make_mock_db(scalar_return=5, execute_return=[mock_entity])

        mod = _kg_with_patched_db(cm)
        result = await mod.handle_kg_entity_search(
            make_mock_ctx(),
            {"query": "Auth", "limit": 10},
        )

        assert "AuthService" in result
        assert "service" in result
        assert "0.95" in result

    @pytest.mark.asyncio
    async def test_empty_kg_returns_hint(self):
        """Empty KG returns hint to run auto-link instead of searching."""
        cm = make_mock_db(scalar_return=0)

        mod = _kg_with_patched_db(cm)
        result = await mod.handle_kg_entity_search(
            make_mock_ctx(),
            {"query": "Auth"},
        )

        assert "empty" in result.lower()
        assert "auto-link" in result

    @pytest.mark.asyncio
    async def test_rejects_short_query(self):
        """Query shorter than 2 chars returns error."""
        import kg.kg_query_tool
        mod = kg.kg_query_tool

        result = await mod.handle_kg_entity_search(
            make_mock_ctx(),
            {"query": "A"},
        )

        assert "Error" in result
        assert "2 characters" in result

    @pytest.mark.asyncio
    async def test_no_results_returns_empty_message(self):
        """No matching entities returns helpful message."""
        cm = make_mock_db(scalar_return=5, execute_return=[])

        mod = _kg_with_patched_db(cm)
        result = await mod.handle_kg_entity_search(
            make_mock_ctx(),
            {"query": "NonexistentThing"},
        )

        assert "No entities found" in result


# ═══════════════════════════════════════════════════════════════════
# kg_peer_entities tests
# ═══════════════════════════════════════════════════════════════════

class TestKgPeerEntities:

    @pytest.mark.asyncio
    async def test_finds_entities_for_peer(self):
        """Peer entities returns entities linked to the named peer."""
        mock_entity = make_mock_entity(name="Gateway", peer_name="sysop")
        cm = make_mock_db(scalar_return=5, execute_return=[mock_entity])

        mod = _kg_with_patched_db(cm)
        result = await mod.handle_kg_peer_entities(
            make_mock_ctx(),
            {"peer_name": "sysop"},
        )

        assert "Gateway" in result

    @pytest.mark.asyncio
    async def test_empty_kg_for_peer(self):
        """Empty KG returns hint for peer entities too."""
        cm = make_mock_db(scalar_return=0)

        mod = _kg_with_patched_db(cm)
        result = await mod.handle_kg_peer_entities(
            make_mock_ctx(),
            {"peer_name": "unknown"},
        )

        assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_requires_peer_name(self):
        """Missing peer_name returns error."""
        import kg.kg_query_tool
        mod = kg.kg_query_tool

        result = await mod.handle_kg_peer_entities(
            make_mock_ctx(),
            {},
        )

        assert "Error" in result
        assert "peer_name" in result.lower()


# ═══════════════════════════════════════════════════════════════════
# kg_traverse tests
# ═══════════════════════════════════════════════════════════════════

class TestKgTraverse:

    @pytest.mark.asyncio
    async def test_traverse_returns_results(self):
        """Traverse returns formatted relationship results."""
        cm = make_mock_db(scalar_return=5, execute_return=[])
        mock_graph_result = [{
            "entity": {"name": "ConnectedService", "type": "service", "confidence": 0.95},
            "relationship": {"type": "depends_on", "confidence": 0.85, "properties": {},
                             "first_seen_at": None, "last_seen_at": None},
            "depth": 1,
        }]

        with patch("src.kg.graph.traverse", AsyncMock(return_value=mock_graph_result)):
            mod = _kg_with_patched_db(cm)
            result = await mod.handle_kg_traverse(
                make_mock_ctx(),
                {"entity": "AuthService", "max_depth": 2},
            )

        assert "AuthService" in result
        assert "ConnectedService" in result
        assert "depends_on" in result

    @pytest.mark.asyncio
    async def test_clamps_max_depth(self):
        """Handler clamps max_depth > 6 down to 6."""
        mock_traverse = AsyncMock(return_value=[{
            "entity": {"name": "Test", "type": "service", "confidence": 1.0},
            "relationship": {"type": "related_to", "confidence": 1.0, "properties": {},
                             "first_seen_at": None, "last_seen_at": None},
            "depth": 1,
        }])
        cm = make_mock_db(scalar_return=5, execute_return=[])

        with patch("src.kg.graph.traverse", mock_traverse):
            mod = _kg_with_patched_db(cm)
            await mod.handle_kg_traverse(
                make_mock_ctx(),
                {"entity": "Test", "max_depth": 99},
            )

        call_kwargs = mock_traverse.call_args[1]
        assert call_kwargs["max_depth"] == 6, \
            f"Expected max_depth=6, got {call_kwargs['max_depth']}"

    @pytest.mark.asyncio
    async def test_requires_entity(self):
        """Missing entity returns error."""
        import kg.kg_query_tool
        mod = kg.kg_query_tool

        result = await mod.handle_kg_traverse(
            make_mock_ctx(),
            {},
        )

        assert "Error" in result


# ═══════════════════════════════════════════════════════════════════
# kg_subgraph tests
# ═══════════════════════════════════════════════════════════════════

class TestKgSubgraph:

    @pytest.mark.asyncio
    async def test_subgraph_returns_formatted_results(self):
        """Subgraph returns structured entity/relationship output."""
        cm = make_mock_db(scalar_return=5)
        mock_subgraph_result = {
            "entities": [{"name": "AuthService", "type": "service"}],
            "relationships": [
                {"source": "AuthService", "target": "DB", "type": "depends_on"},
            ],
        }

        with patch("src.kg.graph.subgraph", AsyncMock(return_value=mock_subgraph_result)):
            mod = _kg_with_patched_db(cm)
            result = await mod.handle_kg_subgraph(
                make_mock_ctx(),
                {"entity": "AuthService", "depth": 1},
            )

        assert "AuthService" in result
        assert "depends_on" in result
        assert "DB" in result

    @pytest.mark.asyncio
    async def test_clamps_depth(self):
        """Handler clamps depth > 3 down to 3."""
        mock_subgraph = AsyncMock(return_value={
            "entities": [{"name": "Test", "type": "service"}],
            "relationships": [],
        })
        cm = make_mock_db(scalar_return=5)

        with patch("src.kg.graph.subgraph", mock_subgraph):
            mod = _kg_with_patched_db(cm)
            await mod.handle_kg_subgraph(
                make_mock_ctx(),
                {"entity": "Test", "depth": 99},
            )

        call_kwargs = mock_subgraph.call_args[1]
        assert call_kwargs["depth"] == 3, \
            f"Expected depth=3, got {call_kwargs['depth']}"