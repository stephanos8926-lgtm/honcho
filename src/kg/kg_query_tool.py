"""KG query tool for the Dialectic agent.

Registers the `kg_query` tool that allows the Dialectic to traverse the
knowledge graph and answer entity-relationship questions.

See SPEC-001 v3.0 §3.6 for design.
"""

from typing import Any

from src.kg.graph import find_path, traverse
from src.dependencies import tracked_db


KG_QUERY_TOOL_DEFINITION: dict[str, Any] = {
    "name": "kg_query",
    "description": (
        "Query the Knowledge Graph to find relationships between entities "
        "(services, tools, people, projects, concepts). Use this when the user "
        "asks about how things connect, depend on each other, or relate. "
        "Examples: 'What depends on Redis?', 'How does the auth system connect "
        "to the database?', 'What services were deployed before the API?'"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entity": {
                "type": "string",
                "description": "The starting entity name to query about (e.g., 'Redis', 'Auth Service', 'PostgreSQL')",
            },
            "query_type": {
                "type": "string",
                "enum": ["traverse", "find_path"],
                "description": "'traverse' to explore what's connected to this entity, 'find_path' to find a connection between two entities",
            },
            "target_entity": {
                "type": "string",
                "description": "Required when query_type='find_path'. The target entity to find a path to.",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum traversal depth (default: 3, max: 6)",
                "default": 3,
            },
            "relationship_types": {
                "type": "string",
                "description": "Optional comma-separated list of relationship types to filter by (e.g., 'depends_on,manages')",
            },
        },
        "required": ["entity", "query_type"],
    },
}


async def handle_kg_query(
    ctx: Any,
    tool_input: dict[str, Any],
) -> str:
    """Execute a KG query and return formatted results.
    
    Called by the Dialectic agent through the tool executor.
    ctx: ToolContext from agent_tools.py (lazy-imported to avoid circular dep).
    """
    workspace_name = ctx.workspace_name
    entity = tool_input.get("entity", "")
    query_type = tool_input.get("query_type", "traverse")
    target = tool_input.get("target_entity", "")
    max_depth = min(tool_input.get("max_depth", 3), 6)
    rel_types = tool_input.get("relationship_types", None)
    if isinstance(rel_types, str) and rel_types:
        rel_types = [r.strip() for r in rel_types.split(",")]
    else:
        rel_types = None

    if not entity:
        return "Error: 'entity' is required."

    async with tracked_db("kg_query", read_only=True) as db:
        if query_type == "find_path":
            if not target:
                return "Error: 'target_entity' is required for find_path queries."
            results = await find_path(
                db, workspace_name, entity, target,
                max_depth=max_depth,
                relationship_types=rel_types,
            )
            if not results:
                return (
                    f"No path found between '{entity}' and '{target}' "
                    f"within {max_depth} hops."
                )
            return _format_path_results(results, entity, target)

        # Default: traverse
        results = await traverse(
            db, workspace_name, entity,
            max_depth=max_depth,
            relationship_types=rel_types,
            limit=50,
        )
        if not results:
            return (
                f"No relationships found for '{entity}' "
                f"within {max_depth} hops."
            )
        return _format_traverse_results(results, entity)


def _format_traverse_results(results: list[dict], root: str) -> str:
    """Format traverse results for LLM consumption."""
    lines = [f"Knowledge Graph results for '{root}':"]
    for r in results:
        ent = r.get("entity", {})
        rel = r.get("relationship", {})
        depth = r.get("depth", "?")
        lines.append(
            f"  [{depth}] {ent.get('name', '?')} "
            f"({ent.get('type', '?')}) "
            f"──{rel.get('type', '?')}→ "
            f"confidence: {rel.get('confidence', '?')}"
        )
    return "\n".join(lines)


def _format_path_results(path: list[dict], source: str, target: str) -> str:
    """Format path results for LLM consumption."""
    lines = [f"Path from '{source}' to '{target}':"]
    for hop in path:
        lines.append(
            f"  {hop.get('source', '?')} "
            f"──{hop.get('relationship_type', '?')}→ "
            f"{hop.get('target', '?')} "
            f"(confidence: {hop.get('confidence', '?')})"
        )
    lines.append(f"Total hops: {len(path)}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# kg_entity_search — fuzzy entity lookup by name/alias
# ═══════════════════════════════════════════════════════════════════

KG_ENTITY_SEARCH_TOOL: dict[str, Any] = {
    "name": "kg_entity_search",
    "description": (
        "Search Knowledge Graph entities by name or alias (fuzzy match). "
        "Use to discover what entities exist before traversing the graph. "
        "Returns entity name, type, aliases, confidence, mention_count."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Entity name or alias to search (min 2 chars)",
            },
            "entity_type": {
                "type": "string",
                "description": "Filter by type (person, service, tool, project, concept, location, organization, event)",
            },
            "min_confidence": {
                "type": "number",
                "description": "Minimum confidence (0-1)",
                "default": 0.3,
            },
            "limit": {
                "type": "integer",
                "description": "Max results",
                "default": 20,
                "maximum": 100,
            },
        },
        "required": ["query"],
    },
}


async def _kg_count_entities(db) -> int:
    """Quick check: how many entities are in the KG for this workspace?"""
    from sqlalchemy import func, select
    from src.kg.models import KGEntity
    count = await db.scalar(select(func.count(KGEntity.id)))
    return count or 0


async def handle_kg_entity_search(
    ctx: Any,
    tool_input: dict[str, Any],
) -> str:
    """Fuzzy entity search by name or alias."""
    workspace_name = ctx.workspace_name
    query = tool_input.get("query", "")
    entity_type = tool_input.get("entity_type")
    min_confidence = tool_input.get("min_confidence", 0.3)
    limit = min(tool_input.get("limit", 20), 100)

    if not query or len(query) < 2:
        return "Error: query must be at least 2 characters."

    async with tracked_db("kg_entity_search", read_only=True) as db:
        entity_count = await _kg_count_entities(db)
        if entity_count == 0:
            return (
                "Knowledge Graph is empty — no entities have been extracted yet. "
                "Run `POST /v3/v3/workspaces/{ws}/kg/auto-link` first "
                "to extract entities from existing observations."
            )

        from sqlalchemy import or_, select
        from src.kg.models import KGEntity

        stmt = select(KGEntity).where(
            KGEntity.workspace_name == workspace_name,
        )
        stmt = stmt.where(
            or_(
                KGEntity.name.ilike(f"%{query}%"),
                KGEntity.aliases.contains([query]),
            )
        )
        if entity_type:
            stmt = stmt.where(KGEntity.entity_type == entity_type)
        if min_confidence > 0:
            stmt = stmt.where(KGEntity.confidence >= min_confidence)
        # Exclude dormant entities (confidence < 0.1, unseen > 90 days)
        stmt = stmt.where(KGEntity.confidence >= 0.1)

        stmt = stmt.order_by(KGEntity.confidence.desc()).limit(limit)
        result = await db.execute(stmt)
        entities = result.scalars().all()

    if not entities:
        return f"No entities found matching '{query}'."

    lines = [f"Entities matching '{query}':"]
    for e in entities:
        alias_str = ", ".join(e.aliases[:3]) if e.aliases else ""
        peer_str = f" (peer: {e.peer_name})" if e.peer_name else ""
        parts = [
            f"  • {e.name} [{e.entity_type}]",
            f"confidence={e.confidence:.2f}{peer_str}",
            f"mentions={e.mention_count}",
        ]
        if alias_str:
            parts.append(f"aliases=[{alias_str}]")
        lines.append(" | ".join(parts))
    lines.append(f"Total: {len(entities)}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# kg_peer_entities — entities linked to a specific peer
# ═══════════════════════════════════════════════════════════════════

KG_PEER_ENTITIES_TOOL: dict[str, Any] = {
    "name": "kg_peer_entities",
    "description": (
        "Get all Knowledge Graph entities linked to a specific peer. "
        "Shows what entities this peer has mentioned or is associated with. "
        "Returns entity name, type, relationship type, confidence."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "peer_name": {
                "type": "string",
                "description": "Peer name to get entities for (e.g., 'sysop', 'hermes')",
            },
            "relationship_types": {
                "type": "string",
                "description": "Comma-separated relationship types to filter (e.g., 'manages,depends_on')",
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "maximum": 200,
            },
        },
        "required": ["peer_name"],
    },
}


async def handle_kg_peer_entities(
    ctx: Any,
    tool_input: dict[str, Any],
) -> str:
    """Get entities linked to a specific peer."""
    workspace_name = ctx.workspace_name
    peer_name = tool_input.get("peer_name", "")
    rel_types_raw = tool_input.get("relationship_types")
    limit = min(tool_input.get("limit", 50), 200)

    if not peer_name:
        return "Error: 'peer_name' is required."

    rel_types: list[str] | None = None
    if isinstance(rel_types_raw, str) and rel_types_raw.strip():
        rel_types = [r.strip() for r in rel_types_raw.split(",")]

    async with tracked_db("kg_peer_entities", read_only=True) as db:
        entity_count = await _kg_count_entities(db)
        if entity_count == 0:
            return (
                "Knowledge Graph is empty — run auto-link first "
                "to extract entities from observations."
            )

        from sqlalchemy import select
        from src.kg.models import KGEntity, KGRelationship

        stmt = select(KGEntity).where(
            KGEntity.workspace_name == workspace_name,
        )

        if rel_types:
            stmt = (
                select(KGEntity)
                .join(
                    KGRelationship,
                    KGEntity.id == KGRelationship.target_entity_id,
                )
                .where(
                    KGEntity.workspace_name == workspace_name,
                    KGRelationship.relationship_type.in_(rel_types),
                )
            )
        else:
            stmt = stmt.where(KGEntity.peer_name == peer_name)

        stmt = stmt.limit(limit)
        result = await db.execute(stmt)
        entities = result.scalars().all()

    if not entities:
        return f"No entities found for peer '{peer_name}'."

    lines = [f"Entities linked to '{peer_name}':"]
    for e in entities:
        lines.append(
            f"  • {e.name} [{e.entity_type}] "
            f"confidence={e.confidence:.2f}"
        )
    lines.append(f"Total: {len(entities)}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# kg_traverse — BFS graph walk from an entity
# ═══════════════════════════════════════════════════════════════════

KG_TRAVERSE_TOOL: dict[str, Any] = {
    "name": "kg_traverse",
    "description": (
        "Traverse the Knowledge Graph from a starting entity using "
        "breadth-first search. Use when the user asks 'what connects to X?' "
        "or 'how does X relate to Y?'. Max depth clamped to 6."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entity": {
                "type": "string",
                "description": "Starting entity name or alias",
            },
            "max_depth": {
                "type": "integer",
                "description": "Max traversal hops (1-6)",
                "default": 2,
                "minimum": 1,
                "maximum": 6,
            },
            "relationship_types": {
                "type": "string",
                "description": "Comma-separated relationship types to filter (e.g., 'depends_on,manages')",
            },
            "entity_types": {
                "type": "string",
                "description": "Comma-separated entity types to filter (e.g., 'service,tool')",
            },
            "min_confidence": {
                "type": "number",
                "default": 0.5,
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "maximum": 200,
            },
        },
        "required": ["entity"],
    },
}


async def handle_kg_traverse(
    ctx: Any,
    tool_input: dict[str, Any],
) -> str:
    """BFS traversal from a starting entity."""
    workspace_name = ctx.workspace_name
    entity = tool_input.get("entity", "")
    max_depth = min(tool_input.get("max_depth", 2), 6)
    rel_types_raw = tool_input.get("relationship_types")
    ent_types_raw = tool_input.get("entity_types")
    min_confidence = tool_input.get("min_confidence", 0.5)
    limit = min(tool_input.get("limit", 50), 200)

    if not entity:
        return "Error: 'entity' is required."

    rel_types: list[str] | None = None
    if isinstance(rel_types_raw, str) and rel_types_raw.strip():
        rel_types = [r.strip() for r in rel_types_raw.split(",")]
    ent_types: list[str] | None = None
    if isinstance(ent_types_raw, str) and ent_types_raw.strip():
        ent_types = [r.strip() for r in ent_types_raw.split(",")]

    async with tracked_db("kg_traverse", read_only=True) as db:
        if await _kg_count_entities(db) == 0:
            return "KG is empty — run auto-link first."

        from src.kg.graph import traverse

        results = await traverse(
            db,
            workspace_name,
            entity,
            max_depth=max_depth,
            relationship_types=rel_types,
            entity_types=ent_types,
            min_confidence=min_confidence,
            limit=limit,
        )

    if not results:
        return (
            f"No relationships found for '{entity}' "
            f"within {max_depth} hops."
        )

    return _format_traverse_results(results, entity)


# ═══════════════════════════════════════════════════════════════════
# kg_subgraph — neighborhood subgraph extraction
# ═══════════════════════════════════════════════════════════════════

KG_SUBGRAPH_TOOL: dict[str, Any] = {
    "name": "kg_subgraph",
    "description": (
        "Extract a neighborhood subgraph around an entity for context "
        "injection or visualization. Returns entities + relationships "
        "as a structured graph. Depth clamped to 3."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entity": {
                "type": "string",
                "description": "Center entity name or alias",
            },
            "depth": {
                "type": "integer",
                "description": "Neighborhood depth (1-3)",
                "default": 1,
                "minimum": 1,
                "maximum": 3,
            },
            "limit": {
                "type": "integer",
                "default": 100,
                "maximum": 200,
            },
        },
        "required": ["entity"],
    },
}


async def handle_kg_subgraph(
    ctx: Any,
    tool_input: dict[str, Any],
) -> str:
    """Neighborhood subgraph extraction."""
    workspace_name = ctx.workspace_name
    entity = tool_input.get("entity", "")
    depth = min(tool_input.get("depth", 1), 3)
    limit = min(tool_input.get("limit", 100), 200)

    if not entity:
        return "Error: 'entity' is required."

    async with tracked_db("kg_subgraph", read_only=True) as db:
        if await _kg_count_entities(db) == 0:
            return "KG is empty — run auto-link first."

        from src.kg.graph import subgraph

        result = await subgraph(
            db,
            workspace_name,
            entity,
            depth=depth,
            limit=limit,
        )

    entities = result.get("entities", [])
    relationships = result.get("relationships", [])

    if not entities:
        return f"No entities found at center '{entity}'."

    lines = [f"Subgraph centered on '{entity}' (depth ≤{depth}):"]
    lines.append(f"Entities ({len(entities)}):")
    for e in entities[:10]:  # show top 10 to avoid overflow
        lines.append(f"  • {e.get('name', '?')} [{e.get('type', '?')}]")
    if len(entities) > 10:
        lines.append(f"  ... and {len(entities) - 10} more")

    lines.append(f"Relationships ({len(relationships)}):")
    for r in relationships[:10]:
        lines.append(
            f"  {r.get('source', '?')} "
            f"──{r.get('type', '?')}→ "
            f"{r.get('target', '?')}"
        )
    if len(relationships) > 10:
        lines.append(f"  ... and {len(relationships) - 10} more")

    return "\n".join(lines)
