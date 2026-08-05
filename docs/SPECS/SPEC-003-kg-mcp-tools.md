# SPEC-003 v2.0: KG MCP Tool Wrappers for Dialectic Agent

## Status: Draft v2.0 (Post-Audit Revision)

## Audit Trail

### v1.0 → v2.0 Changes (from Full Audit Cycle)

| Issue | v1.0 Status | v2.0 Resolution |
|---|---|---|
| "NEW tools" incorrectly implied new endpoints | Claimed as "add 4 query tools" | ✅ Reworded: "Add MCP tool wrappers for existing REST endpoints" |
| Missing auto_link + update_entity tools | Not mentioned | ✅ Added as explicit scope-limitations OR planned tool |
| kg_mcp_tools.py module claimed | Assumed separate file | ✅ Extend existing kg_query_tool.py instead |
| Level gating too complex | Per-table gating table | ✅ Simplified to binary split (minimal vs all) with depth clamping in handlers |
| Extraction never wired | Implicitly assumed | ✅ Explicitly documented as pre-requisite: KG must be populated first |
| kg_traverse duplicates kg_query | Both wrap same traverse() | ✅ Clarified: dedicated BFS tool vs general-purpose query

## 1. Executive Summary

Add MCP tool wrappers for 4 existing KG REST endpoints to the Dialectic agent's toolset, enabling multi-hop graph reasoning alongside existing vector search. Tools are gated at the `minimal` level boundary (binary split: minimal excludes all KG tools; `low`+ includes them with depth clamping).

**Existing REST infrastructure (no new tables/routes needed):**
- `GET /v3/v3/workspaces/{w}/kg/entities` — `kg_search_entities` (routers/kg.py:73)
- `GET /v3/v3/workspaces/{w}/kg/peer-entities` — `kg_peer_entities` (routers/kg.py:182)
- `GET /v3/v3/workspaces/{w}/kg/traverse` — `kg_traverse` (routers/kg.py:33)
- `GET /v3/v3/workspaces/{w}/kg/subgraph` — `kg_subgraph` (routers/kg.py:163)

**Prerequisite:** KG must be populated via `POST /kg/auto-link` (triggers entity extraction from observations). Without extraction, all tools return empty results. Auto-extraction on message ingestion is NOT yet wired — this is deferred to a follow-up SPEC.

**Existing tool:** `kg_query` (wraps traverse + find_path) remains available.

## 2. Motivation

### 2.1 Problem

The Dialectic agent currently has only `kg_query` (traverse/find_path). Missing capabilities:
- Entity discovery (`search_entities`) — "What entities exist matching 'auth'?"
- Peer-centric view (`peer_entities`) — "What entities is Steven linked to?"
- Neighborhood extraction (`subgraph`) — "Give me the subgraph around Redis for context injection."

Without these, agents cannot efficiently discover or contextualize KG data.

### 2.2 Why MCP Tools (Not Direct REST)

- Dialectic agent already uses MCP tool pattern for `search_memory`, `get_peer_card`, etc.
- Tools enable structured I/O validation, telemetry, and reasoning-level gating.
- MCP is the contract for any external agent (Hermes, Claude Code, etc.) to access Honcho KG.

## 3. Design

### 3.1 Tool Definitions

```python
# src/kg/kg_mcp_tools.py

KG_ENTITY_SEARCH_TOOL = {
    "name": "kg_entity_search",
    "description": (
        "Search Knowledge Graph entities by name or alias (fuzzy pg_trgm match). "
        "Use to discover what entities exist before traversing. "
        "Returns: entity name, type, aliases, confidence, mention_count."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Entity name or alias to search (min 2 chars)"},
            "entity_type": {"type": "string", "description": "Filter by entity type (person, service, tool, project, concept, location, organization, event)"},
            "min_confidence": {"type": "number", "description": "Minimum confidence threshold (0-1)", "default": 0.3},
            "limit": {"type": "integer", "description": "Max results", "default": 20, "maximum": 100},
        },
        "required": ["query"],
    },
}

KG_PEER_ENTITIES_TOOL = {
    "name": "kg_peer_entities",
    "description": (
        "Get all entities linked to a specific peer. "
        "Shows what entities this peer has mentioned or is associated with. "
        "Returns: entity name, type, relationship_type, confidence."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "peer_name": {"type": "string", "description": "Peer name to get entities for"},
            "relationship_types": {"type": "string", "description": "Comma-separated relationship types to filter"},
            "limit": {"type": "integer", "default": 50, "maximum": 200},
        },
        "required": ["peer_name"],
    },
}

KG_TRAVERSE_TOOL = {
    "name": "kg_traverse",
    "description": (
        "Traverse the Knowledge Graph from a starting entity. "
        "Returns multi-hop relationships up to max_depth. "
        "Use for 'what connects to X?' questions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entity": {"type": "string", "description": "Starting entity name"},
            "max_depth": {"type": "integer", "description": "Traversal depth (1-6)", "default": 2, "minimum": 1, "maximum": 6},
            "relationship_types": {"type": "string", "description": "Comma-separated types (depends_on, manages, configured_with, deployed_at, communicates_with, part_of, preceded_by, caused, mentioned_in, related_to)"},
            "entity_types": {"type": "string", "description": "Comma-separated entity types to filter"},
            "min_confidence": {"type": "number", "default": 0.5},
            "limit": {"type": "integer", "default": 50, "maximum": 200},
        },
        "required": ["entity"],
    },
}

KG_SUBGRAPH_TOOL = {
    "name": "kg_subgraph",
    "description": (
        "Extract a neighborhood subgraph around an entity for context injection. "
        "Returns entities + relationships as a graph structure suitable for LLM context. "
        "Use when you need the full neighborhood, not just a traversal path."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entity": {"type": "string", "description": "Center entity"},
            "depth": {"type": "integer", "description": "Neighborhood depth (1-3)", "default": 1, "minimum": 1, "maximum": 3},
            "limit": {"type": "integer", "default": 100, "maximum": 500},
        },
        "required": ["entity"],
    },
}
```

### 3.2 Reasoning-Level Gating

The current level gating is a **binary split** between `minimal` and everything else:

| Tool | minimal (2 tools) | low+ (11+ tools) |
|---|---|---|
| `kg_entity_search` | ❌ | ✅ |
| `kg_peer_entities` | ❌ | ✅ |
| `kg_traverse` | ❌ | ✅ (depth≤6 via API validator) |
| `kg_subgraph` | ❌ | ✅ (depth≤3 via API validator) |

**Reduction from v1.0:** Per-table gating at every level was removed because:
1. Current implementation (in `dialectic/core.py:443-445`) only has a binary split: `DIALECTIC_TOOLS_MINIMAL` vs `DIALECTIC_TOOLS`
2. Adding per-level gating would require refactoring tool selection in `agent_tools.py` and/or `tool_loop.py` — deferred
3. Depth limits are enforced by both the REST API router (`ge`/`le` on Query params) and the graph engine (`MAX_TRAVERSE_DEPTH=10`, `MAX_SUBGRAPH_DEPTH=5`)

**Depth clamping in handlers:**
- `kg_traverse`: handler clamps `max_depth = min(requested, 6)`
- `kg_subgraph`: handler clamps `depth = min(requested, 3)`
- Both can be tightened later without spec changes

**Rationale:** Graph queries are expensive (DB traversal) and semantically complex. At `minimal`/`low`, agents should use vector search (`search_memory`) for simple lookups. `medium`+ enables graph reasoning when the question demands it.

### 3.3 Tool Handler Pattern

```python
# src/kg/kg_mcp_tools.py

async def handle_kg_entity_search(ctx, tool_input):
    workspace_name = ctx.workspace_name
    query = tool_input.get("query", "")
    entity_type = tool_input.get("entity_type")
    min_confidence = tool_input.get("min_confidence", 0.3)
    limit = min(tool_input.get("limit", 20), 100)
    
    async with tracked_db("kg_entity_search", read_only=True) as db:
        results = await search_entities(
            db, workspace_name, query,
            entity_type=entity_type,
            min_confidence=min_confidence,
            limit=limit,
        )
    return format_entity_search_results(results)

async def handle_kg_peer_entities(ctx, tool_input):
    workspace_name = ctx.workspace_name
    peer_name = tool_input.get("peer_name", "")
    relationship_types = tool_input.get("relationship_types")
    if isinstance(relationship_types, str) and relationship_types:
        relationship_types = [r.strip() for r in relationship_types.split(",")]
    limit = min(tool_input.get("limit", 50), 200)
    
    async with tracked_db("kg_peer_entities", read_only=True) as db:
        results = await get_peer_entities(
            db, workspace_name, peer_name,
            relationship_types=relationship_types,
            limit=limit,
        )
    return format_peer_entities_results(results)

# ... similar for traverse, subgraph
```

### 3.4 Tool Source Module

Tool definitions and handlers live in **`src/kg/kg_query_tool.py`** (existing file, extended). No new module:

```python
# src/kg/kg_query_tool.py — extended with 4 new tool definitions + handlers
#
# Existing: kg_query tool definition + handle_kg_query()
# Added:    kg_entity_search, kg_peer_entities, kg_traverse, kg_subgraph
```

This avoids file proliferation and keeps all KG agent tools in one place. The existing tool definitions in `agent_tools.py` reference these via import:
```python
# agent_tools.py already imports from kg_query_tool:
from src.kg.kg_query_tool import handle_kg_query

# After extension:
from src.kg.kg_query_tool import (
    handle_kg_query,
    handle_kg_entity_search,
    handle_kg_peer_entities,
    handle_kg_traverse,
    handle_kg_subgraph,
)

### 3.5 Runtime Level Filtering

Level gating uses the existing binary split in `dialectic/core.py:443-445` — no new filtering logic needed in `tool_loop.py`:

```python
# dialectic/core.py already implements:
tools = (
    DIALECTIC_TOOLS_MINIMAL       # minimal: search_memory + search_messages
    if self.reasoning_level == "minimal"
    else DIALECTIC_TOOLS           # low+: all tools including KG
)

# Add the 4 new KG tool names to DIALECTIC_TOOLS in agent_tools.py
# DIALECTIC_TOOLS_MINIMAL remains unchanged (excludes KG tools)
```

**Depth clamping** is handled inside each tool handler (not in tool_loop):
- `kg_traverse`: `max_depth = min(parsed_depth, 6)`
- `kg_subgraph`: `depth = min(parsed_depth, 3)`
- `kg_entity_search`, `kg_peer_entities`: no depth parameter needed

**Empty-KG fast path:** each handler checks if `kg_entities` table has any rows before querying. If empty, returns immediate hint: "KG is empty — run auto-link first" — prevents wasted iterations.

## 4. Dependencies

- SPEC-001 (KG data model) — ✅ deployed
- REST endpoints — ✅ deployed
- `kg_query` tool — ✅ exists
- Dialectic tool registration pattern — ✅ established

## 5. Risks

| Risk | Mitigation |
|---|---|
| Tool overload at low+: 15 tools in DIALECTIC_TOOLS | Binary gating: minimal (2 tools) has zero KG; low+ has all (plus existing). Depth clamping prevents runaway traversals |
| Traversal latency | API validator caps max_depth ≤ 10; router enforces `le(10)`; graph engine sets `MAX_TRAVERSE_DEPTH=10` |
| Entity name ambiguity | Fuzzy search returns multiple candidates; agent disambiguates |
| Empty KG wastes tool calls | All handlers check if `kg_entities` table exists and has rows before querying. If empty: immediate hint, no DB call |
| Extraction not wired to Deriver | Deferred to follow-up SPEC. Manual `POST /kg/auto-link` triggers extraction from all observations |
| kg_traverse duplicates kg_query | Acceptable: kg_query is general-purpose ("find path"); kg_traverse is focused BFS walk. Both clarify agent intent |

## 6. Success Metrics

| Metric | Target |
|---|---|
| Tool call success rate | > 95% |
| Traversal latency (depth 2, cache hit) | < 100ms p95 |
| Entity search relevance | > 85% manual eval |
| Tool choice accuracy (medium level) | > 80% correct KG tool for graph questions |