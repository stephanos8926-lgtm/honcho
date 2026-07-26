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

from src.config import settings
from src.kg.extraction_output import KGExtractionOutput
from src.kg.extraction_prompt import KG_EXTRACTION_PROMPT
from src.kg.extraction_schema import validate_extraction_output
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
    prompt_template: str = KG_EXTRACTION_PROMPT,
) -> dict:
    """Call the LLM for KG extraction with structured output.
    
    Uses Honcho's honcho_llm_call with KGExtractionOutput as the response_model.
    This enforces the controlled vocabulary at the LLM API level via structured
    output / response_format.
    
    Args:
        llm_client: Honcho LLM client (not used directly; honcho_llm_call uses
                    the configured model internally)
        message_content: The message text to extract entities/relationships from
        prompt_template: The prompt template (overridable for customization)
    
    Returns:
        dict matching KGExtractionOutput schema with entities and relationships
    
    Raises:
        TimeoutError: If the LLM call exceeds EXTRACTION_TIMEOUT_SECONDS
        ValueError: If the response doesn't match the expected schema
    """
    from src.llm import honcho_llm_call
    from src.llm.types import LLMTelemetryContext
    from src.telemetry.events.llm import CallPurpose
    from src.deriver.deriver import _get_deriver_model_config

    # Build the prompt
    prompt = prompt_template.format(message=message_content)

    # Get the deriver's model config (KG extraction uses the same model)
    model_config = _get_deriver_model_config()
    max_tokens = model_config.max_output_tokens or 4096

    try:
        response = await honcho_llm_call(
            model_config=model_config,
            prompt=prompt,
            max_tokens=max_tokens,
            response_model=KGExtractionOutput,
            json_mode=True,
            max_input_tokens=min(
                len(message_content.split()),
                settings.DERIVER.MAX_INPUT_TOKENS,
            ),
            enable_retry=True,
            retry_attempts=MAX_RETRIES,
            trace_name="kg_extraction",
            telemetry=LLMTelemetryContext(
                workspace_name="",
                call_purpose=CallPurpose.DERIVER_REPRESENTATION.value,
                parent_category="kg_extraction",
                observed="",
                track_name="KG Extraction",
                trace_id="",
                span_id="",
            ),
        )

        # Convert Pydantic model to dict
        return response.model_dump()

    except Exception as e:
        logger.warning("KG extraction LLM call failed: %s", e)
        raise
