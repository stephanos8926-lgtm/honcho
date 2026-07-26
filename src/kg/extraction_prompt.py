"""Prompt template for KG entity/relationship extraction."""

KG_EXTRACTION_PROMPT = """You are a knowledge graph extraction system. Your task is to extract entities and relationships from the message below.

Extract:
1. **Entities** — Named things mentioned in the message. These can be:
   - People and agents (person, agent)
   - Software services, tools, libraries (service, tool)
   - Projects, codebases (project)
   - Abstract concepts, ideas (concept)
   - Locations, servers, hosting (location)
   - Organizations, teams, groups (organization)
   - Events, deployments, milestones (event)

2. **Relationships** — How entities relate to each other. Use these relationship types:
   - depends_on: A depends on B (service dependency)
   - manages: A manages, owns, or administers B
   - configured_with: A is configured or deployed with B
   - deployed_at: A is deployed at or hosted on B
   - communicates_with: A communicates with B via API or network
   - part_of: A is a component or member of B
   - preceded_by: A happened before B (temporal ordering)
   - caused: A caused or triggered B
   - mentioned_in: A was mentioned in the context of B
   - related_to: Generic relationship (use sparingly)

Rules:
- Only extract entities and relationships that are EXPLICITLY stated or clearly implied
- Use canonical names for entities (prefer full names over abbreviations)
- Include alternative names as aliases
- If unsure about a relationship type, use "related_to"
- If unsure about an entity type, use "unknown"

Message:
{message}

Respond with a JSON object containing "entities" and "relationships" arrays.
"""
