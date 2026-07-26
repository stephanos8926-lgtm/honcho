"""KG extraction orchestrator — runs alongside the Deriver's observation extraction.

Extracts entities and relationships from messages using a structured-output
LLM call, then resolves duplicates, creates relationships, and links to peers.

Runs as a SEPARATE async step (not inside process_representation_batch) to
avoid tight coupling with the observation extraction logic.

See SPEC-001 v3.0 §3.2 and §3.7 for design.
"""

import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.kg.extraction_schema import (
    KG_EXTRACTION_SCHEMA,
    validate_extraction_output,
)
from src.kg.resolver import resolve_entity
from src.kg.relationship_manager import create_or_update_relationship

logger = logging.getLogger(__name__)

# Extraction timeout per message
EXTRACTION_TIMEOUT_SECONDS = 15
MAX_RETRIES = 2


async def extract_kg_from_message(
    db: AsyncSession,
    llm_client: Any,
    workspace_name: str,
    message_content: str,
    observation_id: str | None = None,
) -> dict:
    """Extract entities and relationships from a single message.
    
    This is the main entry point for KG extraction. Called from the Deriver
    pipeline after observation extraction completes.
    
    Args:
        db: Database session
        llm_client: LLM client for structured-output extraction
        workspace_name: Name of the workspace
        message_content: The message text to extract from
        observation_id: Optional observation ID for provenance tracking
    
    Returns:
        dict with keys:
            entities_count: number of entities extracted
            relationships_count: number of relationships extracted
            duration_ms: time spent in milliseconds
            success: whether extraction completed
    
    Best-effort: returns empty results on failure (does not raise).
    """
    start = time.perf_counter()

    try:
        # 1. Call LLM for entity/relationship extraction
        extraction = await _call_kg_llm(
            llm_client, message_content, KG_EXTRACTION_SCHEMA
        )

        # 2. Validate output against type registries
        validate_extraction_output(extraction)

        # 3. Resolve entities (dedup + create)
        entity_cache: dict[str, str] = {}
        for entity_data in extraction.get("entities", []):
            entity = await resolve_entity(
                db,
                workspace_name,
                name=entity_data["name"],
                entity_type=entity_data.get("type", "unknown"),
                aliases=entity_data.get("aliases"),
            )
            entity_cache[entity_data["name"]] = entity.id

        # 4. Create/update relationships
        for rel_data in extraction.get("relationships", []):
            source_id = entity_cache.get(rel_data.get("source", ""))
            target_id = entity_cache.get(rel_data.get("target", ""))

            if not source_id or not target_id:
                logger.warning(
                    "KG extraction: skipping relationship with unresolvable "
                    "entities: source=%s target=%s type=%s",
                    rel_data.get("source"),
                    rel_data.get("target"),
                    rel_data.get("type"),
                )
                continue

            await create_or_update_relationship(
                db,
                workspace_name,
                source_entity_id=source_id,
                target_entity_id=target_id,
                rel_type=rel_data["type"],
                properties=rel_data.get("properties"),
                observation_id=observation_id,
            )

        duration_ms = int((time.perf_counter() - start) * 1000)
        return {
            "entities_count": len(extraction.get("entities", [])),
            "relationships_count": len(extraction.get("relationships", [])),
            "duration_ms": duration_ms,
            "success": True,
        }

    except Exception as e:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.warning(
            "KG extraction failed for message (best-effort, skipping): %s "
            "(duration: %dms)",
            e,
            duration_ms,
        )
        return {
            "entities_count": 0,
            "relationships_count": 0,
            "duration_ms": duration_ms,
            "success": False,
            "error": str(e),
        }


async def _call_kg_llm(
    llm_client: Any,
    message_content: str,
    schema: dict,
) -> dict:
    """Call the LLM for KG extraction with structured output.
    
    Uses the same LLM client as the Deriver but with a custom extraction
    schema. Timeout and retry are handled by the caller.
    
    Returns parsed JSON matching the schema.
    
    Raises:
        TimeoutError: If the LLM call exceeds EXTRACTION_TIMEOUT_SECONDS
        ValueError: If the LLM response doesn't match the schema
    """
    # TODO: Implement actual LLM call using Honcho's LLM client
    # For now, this is a placeholder that will be filled in during
    # Phase 2 integration with the Deriver pipeline.
    #
    # The call pattern will follow src/llm/ conventions:
    #   response = await llm_client.chat.completions.create(
    #       model=settings.DERIVER.MODEL_CONFIG.model,
    #       messages=[{"role": "user", "content": prompt}],
    #       response_format={"type": "json_object", "schema": schema},
    #       timeout=EXTRACTION_TIMEOUT_SECONDS,
    #   )
    raise NotImplementedError(
        "LLM extraction not yet integrated — see Phase 2 of KG implementation plan"
    )
