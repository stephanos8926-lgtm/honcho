# ADR-003 v2.0: KG Tool Gating — Binary Split (minimal vs low+)

## Status: Draft v2.0 (Post-Audit Revision)

## Context

SPEC-003 adds 4 KG tool wrappers to the Dialectic agent: `kg_entity_search`, `kg_peer_entities`, `kg_traverse`, `kg_subgraph`. The Dialectic already has `kg_query` (traverse + find_path).

The Dialectic agent supports 5 reasoning levels with escalating tool iterations:
- `minimal`: 1 iteration, 2 tools (search_memory + search_messages)
- `low`: 5 iterations, full toolset
- `medium`: 2 iterations, full toolset
- `high`: 4 iterations, full toolset
- `max`: 10 iterations, full toolset

Current implementation (in `dialectic/core.py:443-445`) only distinguishes `minimal` from everything else via `DIALECTIC_TOOLS_MINIMAL` vs `DIALECTIC_TOOLS`. There is no per-level filtering mechanism.

## Decision

**Binary split at the `minimal` level boundary.** All 4 KG tools are gated:

| Tool | minimal (2 tools) | low+ (15 tools) |
|---|---|---|
| `kg_entity_search` | ❌ | ✅ |
| `kg_peer_entities` | ❌ | ✅ |
| `kg_traverse` | ❌ | ✅ (depth clamped to ≤6 in handler) |
| `kg_subgraph` | ❌ | ✅ (depth clamped to ≤3 in handler) |

Existing `kg_query` follows same pattern (available at low+).

## Rationale

### 1. Tool choice complexity
At `low`+, the Dialectic has 11 existing tools (`search_memory`, `search_messages`, `get_observation_context`, `grep_messages`, `get_messages_by_date_range`, `search_messages_temporal`, `get_reasoning_chain`, `kg_query`, plus commented-out `create_observations_deductive`). Adding 4 KG tools brings it to 15. This is fine at `low`+ where agents have 5-10 iterations. At `minimal` (1 iteration), no KG tools is correct.

### 2. Implementation reality
Per-level gating (e.g. `kg_traverse` only at `medium`+) would require refactoring tool selection in `agent_tools.py` and/or `tool_loop.py`. The cost/benefit doesn't justify it:
- `kg_query` was already available at `low`+ (no complaints)
- Depth clamping (≤6 traverse, ≤3 subgraph) prevents worst-case latency at any level
- `low` still has 5 iterations — enough for one or two focused KG calls

### 3. Depth limits are already enforced
The REST API routers enforce `max_depth ≤ 10` via `Query(ge=1, le=10)`. The graph engine sets `MAX_TRAVERSE_DEPTH = 10` and `MAX_SUBGRAPH_DEPTH = 5`. Handlers clamp further (traverse ≤6, subgraph ≤3) as a safety net.

### 4. Wake-up cost
KG tools on an empty graph return immediately (handler checks `SELECT COUNT(*)` first). No DB traversal or LLM cost for empty results.

## Consequences

### Positive
- Uses existing binary-split mechanism — no tool_loop.py refactoring needed
- Depth limits already enforced at 3 layers (handler → router validator → graph engine)
- Empty-KG fast path prevents wasted tool calls
- All tools available at `low`+ — no agent is artificially constrained

### Negative
- Per-level nuance lost: `low` agents get depth-6 traverse capability (appropriately?)
- `kg_query` coverage overlap with `kg_traverse` persists (intentional — different query types)

## Compliance

- Tool definitions + handlers: `src/kg/kg_query_tool.py` (extended)
- TOOLS dict + DIALECTIC_TOOLS: `src/utils/agent_tools.py`
- Level gating: `src/dialectic/core.py:443-445` (unchanged binary split)
- Depth clamping: tool handlers
- Telemetry: tool_call events include reasoning_level

## References
- SPEC-003 v2.0 (KG MCP Tool Wrappers)
- ADR-001 v3.0 (KG Overlay)
- Dialectic tool selection: `src/dialectic/core.py:443-445`