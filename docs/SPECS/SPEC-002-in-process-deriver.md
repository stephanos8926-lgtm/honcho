# SPEC-002: In-Process Deriver / Single-Container Mode

## Status: Draft

## 1. Executive Summary

Add an in-process deriver mode that runs the Deriver worker inline within the API
process, eliminating the need for a separate worker process, Redis, and background
queue infrastructure. This makes Honcho deployable as a single container with only
PostgreSQL as an external dependency.

## 2. Motivation

### 2.1 Market Gap

The single most common complaint across Honcho's GitHub issues (#494, #789, #822)
and community discussions is the complexity of self-hosting:

**Required infrastructure (current):**
- PostgreSQL + pgvector
- Redis (for the deriver queue)
- API server process
- Deriver worker process (separate)
- All connected via a shared queue

**Required infrastructure (with this SPEC):**
- PostgreSQL + pgvector
- Single API+Deriver process

This directly addresses pain point #3 from our market research (see
SPEC-001 §2.1).

### 2.2 Competitive Landscape

| System | External Dependencies | Single-Binary? |
|---|---|---|
| Honcho (current) | PG + Redis + API + Deriver | ❌ |
| Hindsight | PG (bundled) | ✅ Single container |
| Mem0 | Qdrant/PG | ✅ Single process (library mode) |
| ByteRover | None | ✅ Single binary |
| Holographic | None | ✅ Native |

### 2.3 Why This Matters for RapidWebs

Our deployment is on an 8GB Hetzner VPS with three Incus containers. Adding
Redis + a separate Deriver worker for a single-user Honcho instance is
disproportionate overhead. An in-process mode would:
- Reduce our Honcho deployment to one container
- Eliminate Redis as an operational dependency
- Reduce memory footprint by ~200MB (no separate Deriver process)
- Simplify backup/restore (single PG dump)

## 3. Design

### 3.1 Configuration

```python
# src/config.py - new settings on DeriverSettings
IN_PROCESS_MODE: bool = False
# When True:
# - Deriver runs inside the API process
# - Queue is in-memory (not Redis)
# - No Redis connection required
```

The in-process mode is opt-in:

```env
DERIVER_IN_PROCESS_MODE=true
# Redis settings become optional when IN_PROCESS_MODE=true
```

### 3.2 In-Memory Queue

Replace Redis-backed queue with an asyncio.Queue:

```python
class InMemoryQueue:
    """Drop-in replacement for Redis-backed queue, used when
    DERIVER_IN_PROCESS_MODE=true. Uses asyncio.Queue internally.
    
    Does NOT persist across process restarts (acceptable for in-process
    mode since the Deriver re-processes pending work on restart).
    """
    
    def __init__(self):
        self._queue: asyncio.Queue[WorkUnit] = asyncio.Queue()
        self._pending: dict[str, WorkUnit] = {}
    
    async def enqueue(self, unit: WorkUnit) -> None:
        await self._queue.put(unit)
        self._pending[unit.id] = unit
    
    async def claim(self) -> WorkUnit | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            return None
    
    async def complete(self, unit_id: str) -> None:
        self._pending.pop(unit_id, None)
    
    async def fail(self, unit_id: str) -> None:
        # Re-enqueue on failure for retry
        if unit_id in self._pending:
            await self._queue.put(self._pending[unit_id])
```

### 3.3 API Lifespan Integration

```python
# src/main.py - modified lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing startup ...
    
    if settings.DERIVER.IN_PROCESS_MODE:
        # Start in-process deriver as a background task
        deriver_task = asyncio.create_task(
            run_in_process_deriver(app.state)
        )
        logger.info("In-process deriver started")
    
    yield
    
    if settings.DERIVER.IN_PROCESS_MODE:
        deriver_task.cancel()
        try:
            await deriver_task
        except asyncio.CancelledError:
            pass
```

### 3.4 Backpressure and Rate Limiting

The in-process deriver must not block API responses. Design:

```python
async def run_in_process_deriver(state: AppState):
    """Background task running deriver work units."""
    while True:
        try:
            unit = await state.queue.claim()
            if unit is None:
                await asyncio.sleep(0.5)  # Polling interval
                continue
            
            # Process with a timeout to prevent unbounded blocking
            async with asyncio.timeout(30.0):
                await process_work_unit(state, unit)
            
            await state.queue.complete(unit.id)
        
        except asyncio.TimeoutError:
            logger.warning(f"Work unit {unit.id} timed out, re-queuing")
            await state.queue.fail(unit.id)
        
        except Exception as e:
            logger.error(f"Work unit {unit.id} failed: {e}")
            await state.queue.fail(unit.id)
```

### 3.5 Graceful Degradation

When `IN_PROCESS_MODE=true` but Redis is also configured:
- Warn at startup: "Both IN_PROCESS_MODE=true and Redis configured.
  Using in-process mode; Redis will be ignored."
- No Redis connection is established (saves resources).

When `IN_PROCESS_MODE=false` (default):
- Current behavior unchanged. Queue backed by Redis.
- Full backward compatibility.

### 3.6 Reconciliation

The Reconciler runs as an in-process periodic task regardless of mode:

```python
# Already in the existing Deriver — runs inside the deriver process.
# In in-process mode, it runs inside the API process instead.
# Behavior is identical.
```

## 4. Implementation Plan

### Phase 1: Queue Abstraction (2-3 days)
- Extract `QueueInterface` protocol from existing Redis queue
- Implement `InMemoryQueue` with same interface
- Add `IN_PROCESS_MODE` config flag

### Phase 2: Process Integration (2-3 days)
- Add background task to API lifespan
- Wire queue selection based on config flag
- Add startup/shutdown lifecycle management

### Phase 3: Testing + Documentation (2-3 days)
- Unit tests for InMemoryQueue
- Integration test: API + in-process deriver end-to-end
- Document single-container deployment

### Total: ~6-9 days

## 5. Dependencies

- PostgreSQL + pgvector — still required (core dependency)
- No Redis required when IN_PROCESS_MODE=true
- No new infrastructure

## 6. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Deriver blocks API responses | Dedicated asyncio task with timeout; backpressure via queue |
| Memory growth from in-memory queue | Max queue size (configurable, default 1000); oldest-dropped on overflow |
| State loss on process restart | Acceptable: work units re-derive from messages on restart |
| CPU contention between API and Deriver | Single-core deployment fine (Deriver is I/O-bound on LLM calls); multi-core available for production |

## 7. Success Metrics

- Single-container Honcho boots and serves in < 30s
- Deriver processes work units within 5s of API receiving a message
- API response latency unaffected by Deriver processing (p95 < 500ms)
- Memory overhead of in-process Deriver < 50MB
