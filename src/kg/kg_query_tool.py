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
