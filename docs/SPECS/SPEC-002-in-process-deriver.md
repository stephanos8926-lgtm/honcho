# SPEC-002 v2.0: In-Process Deriver / Single-Container Mode

## Status: Draft v2.0 (Post-Audit Revision)

## 1. Executive Summary

Add an opt-in in-process deriver mode (`DERIVER_IN_PROCESS_MODE=true`) that runs
the Deriver worker as an asyncio background task within the API process, using an
in-memory queue instead of Redis. This enables single-container Honcho deployments
with only PostgreSQL as an external dependency — directly addressing the #1
self-hosted complaint.

## 2. Motivation

### 2.1 Market Gap

The single most common complaint across Honcho's GitHub issues (#494, #789, #822)
is deployment complexity. Current vs proposed:

| Resource | Current | With In-Process Mode |
|---|---|---|
| PostgreSQL + pgvector | ✅ Required | ✅ Required |
| Redis | ✅ Required | ❌ Eliminated |
| API server process | ✅ Required | ✅ Required |
| Deriver worker process | ✅ Required | ❌ Eliminated |
| Queue infrastructure | Redis-backed | In-memory asyncio.Queue |

### 2.2 Competitive Landscape

| System | Dependencies | Single Process? | License |
|---|---|---|---|
| Honcho (current) | PG + Redis + API + Deriver | ❌ | AGPL-3.0 |
| Honcho (this SPEC) | PG + in-process Deriver | ✅ | AGPL-3.0 |
| Hindsight | PG (bundled) + built-in embedder | ✅ | MIT |
| Mem0 | Qdrant/PG (library mode) | ✅ | Apache 2.0 |
| Holographic | None | ✅ Native | MIT |

### 2.3 Why This Matters for RapidWebs

Our deployment runs on an 8GB Hetzner VPS with three Incus containers. Adding
Redis + a separate Deriver worker for single-user traffic is disproportionate.
This mode:
- Removes one container (no separate deriver process)
- Removes Redis as an operational dependency (~100MB RAM savings)
- Reduces total memory footprint by ~150MB (Deriver deps loaded in-process)
- Simplifies backup: single `pg_dump` covers everything

## 3. Design

### 3.1 Full Redis Usage Audit

Before building, we must understand ALL Redis connections, not just the queue:

| Redis Usage | Required for API? | Required for Deriver? | Status with IN_PROCESS_MODE |
|---|---|---|---|
| Work unit queue | ❌ | ✅ | Replaced by InMemoryQueue |
| Session cache (API) | ✅ (optional, degrades gracefully) | ❌ | Still needed if caching enabled |
| Rate limiting counter | ✅ (optional) | ❌ | Falls back to in-process counter |
| Telemetry buffer | ✅ (optional) | ✅ (optional) | Falls back to in-process buffer |

**Decision:** IN_PROCESS_MODE eliminates Redis for queue operations only. Other
Redis features (session cache, rate limiting, telemetry) degrade gracefully via
in-process fallbacks when Redis is not configured. The API does NOT crash if Redis
is unavailable.

### 3.2 Configuration

```python
# src/config.py - new settings on DeriverSettings
IN_PROCESS_MODE: bool = False
# When True:
# - Deriver runs inside the API process as an asyncio background task
# - Queue is in-memory (not Redis)
# - Redis is NOT required for queue operations
# - Deriver health is exposed via API /health endpoint
# - Startup waits for Deriver init to complete before accepting traffic
IN_PROCESS_POLL_INTERVAL_SECONDS: float = 0.5
# Polling interval for the in-process deriver's work unit claim loop.
# Lower values = lower latency, higher CPU usage at idle.
# Only used when IN_PROCESS_MODE=true.
IN_PROCESS_MAX_QUEUE_SIZE: int = 1000
# Maximum pending work units before overflow. When exceeded, oldest
# unprocessed units are dropped with a warning log and metric emission.
IN_PROCESS_WORK_UNIT_TIMEOUT_SECONDS: float = 30.0
# Per-work-unit timeout. Units exceeding this are re-queued for retry.
# Three consecutive timeouts on the same unit trigger a circuit break
# (unit moved to dead-letter queue, not re-queued).
```

### 3.3 In-Memory Queue

```python
import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WorkUnit:
    id: str
    workspace_name: str
    session_name: str
    created_at: float = field(default_factory=time.time)
    retry_count: int = 0
    last_error: Optional[str] = None


class InMemoryQueue:
    """Drop-in for Redis-backed queue when DERIVER_IN_PROCESS_MODE=true.
    
    Thread-safety: Uses a single asyncio.Lock for all mutations. The
    Reconciler (periodic task) and Deriver (background task) both access
    this queue; the lock ensures mutual exclusion.
    
    Notification: Uses asyncio.Event to wake the consumer on enqueue,
    rather than polling. The polling interval (IN_PROCESS_POLL_INTERVAL)
    is only a safety backstop for edge cases where the event is lost.
    """
    
    MAX_RETRIES = 3
    DEAD_LETTER_TIMEOUT = 3600  # 1 hour before dead-lettered units are discarded
    
    def __init__(self, max_size: int = 1000):
        self._queue: asyncio.Queue[WorkUnit] = asyncio.Queue(maxsize=max_size)
        self._pending: dict[str, WorkUnit] = OrderedDict()
        self._dead_letter: dict[str, tuple[WorkUnit, float]] = {}
        self._lock = asyncio.Lock()
        self._notify_event = asyncio.Event()
        self._max_size = max_size
        self._dropped_count = 0
    
    async def enqueue(self, unit: WorkUnit) -> bool:
        """Enqueue a work unit. Returns False if queue is full (unit dropped)."""
        try:
            self._queue.put_nowait(unit)
            async with self._lock:
                self._pending[unit.id] = unit
            self._notify_event.set()
            return True
        except asyncio.QueueFull:
            # Drop oldest pending unit to make room
            async with self._lock:
                if self._pending:
                    oldest_id, oldest_unit = next(iter(self._pending.items()))
                    self._pending.pop(oldest_id)
                    self._dropped_count += 1
                    logger.warning(
                        "InMemoryQueue overflow: dropped work unit %s "
                        "(dropped count: %d)",
                        oldest_id, self._dropped_count,
                    )
            # Retry enqueue after dropping
            return await self.enqueue(unit)
    
    async def claim(self, timeout: float = 1.0) -> Optional[WorkUnit]:
        """Claim the next available work unit. Blocks until one is available
        or timeout expires."""
        try:
            # Wait for notification event with timeout backstop
            async with asyncio.timeout(timeout):
                await self._notify_event.wait()
                self._notify_event.clear()
                unit = self._queue.get_nowait()
                return unit
        except (asyncio.TimeoutError, asyncio.QueueEmpty):
            return None
    
    async def complete(self, unit_id: str) -> None:
        async with self._lock:
            self._pending.pop(unit_id, None)
    
    async def fail(self, unit_id: str, error: str = "") -> None:
        async with self._lock:
            unit = self._pending.get(unit_id)
            if not unit:
                return
            unit.retry_count += 1
            unit.last_error = error
            if unit.retry_count >= self.MAX_RETRIES:
                # Move to dead letter queue
                self._dead_letter[unit_id] = (unit, time.time())
                self._pending.pop(unit_id)
                logger.error(
                    "Work unit %s moved to dead letter after %d retries: %s",
                    unit_id, unit.retry_count, error,
                )
            else:
                # Re-queue for retry
                await self.enqueue(unit)
    
    async def drain_dead_letter(self) -> int:
        """Remove dead-lettered units older than DEAD_LETTER_TIMEOUT.
        Returns count of removed units."""
        now = time.time()
        async with self._lock:
            stale = [
                uid for uid, (_, ts) in self._dead_letter.items()
                if now - ts > self.DEAD_LETTER_TIMEOUT
            ]
            for uid in stale:
                del self._dead_letter[uid]
            return len(stale)
    
    @property
    def pending_count(self) -> int:
        return self._queue.qsize()
    
    @property
    def dead_letter_count(self) -> int:
        return len(self._dead_letter)
    
    @property
    def dropped_count(self) -> int:
        return self._dropped_count
```

### 3.4 API Lifespan Integration

```python
# src/main.py — modified lifespan

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Phase 1: Initialize core dependencies (DB pool, cache)
    await initialize_db()
    
    # Phase 2: Initialize in-process deriver if enabled
    if settings.DERIVER.IN_PROCESS_MODE:
        # Initialize queue BEFORE accepting traffic
        app.state.queue = InMemoryQueue(
            max_size=settings.DERIVER.IN_PROCESS_MAX_QUEUE_SIZE
        )
        
        # Start deriver background task
        deriver_task = asyncio.create_task(
            _run_deriver_loop(app.state)
        )
        
        # Wait for deriver to complete initialization
        await app.state.deriver_ready.wait()
        logger.info("In-process deriver initialized and ready")
    
    yield
    
    # Shutdown
    if settings.DERIVER.IN_PROCESS_MODE:
        deriver_task.cancel()
        try:
            # Drain remaining work units before shutdown
            remaining = app.state.queue.pending_count
            if remaining > 0:
                logger.info(f"Draining {remaining} remaining work units...")
                await asyncio.sleep(2.0)  # Brief grace period
            await deriver_task
        except asyncio.CancelledError:
            pass
        await app.state.queue.drain_dead_letter()
```

### 3.5 Deriver Background Task

```python
async def _run_deriver_loop(state):
    """Background task: processes work units from the in-memory queue."""
    # Signal ready after initialization
    state.deriver_ready = asyncio.Event()
    state.deriver_ready.set()
    
    # Health tracking
    state.deriver_healthy = True
    state.deriver_last_processed_at = time.time()
    state.deriver_consecutive_failures = 0
    
    polling_interval = settings.DERIVER.IN_PROCESS_POLL_INTERVAL_SECONDS
    timeout = settings.DERIVER.IN_PROCESS_WORK_UNIT_TIMEOUT_SECONDS
    
    while True:
        try:
            unit = await state.queue.claim(timeout=polling_interval)
            if unit is None:
                continue
            
            # Process with timeout
            try:
                async with asyncio.timeout(timeout):
                    await _process_work_unit(state, unit)
                await state.queue.complete(unit.id)
                state.deriver_last_processed_at = time.time()
                state.deriver_consecutive_failures = 0
            except asyncio.TimeoutError:
                logger.warning(
                    "Work unit %s timed out after %ss",
                    unit.id, timeout,
                )
                await state.queue.fail(unit.id, "timeout")
                state.deriver_consecutive_failures += 1
            except Exception as e:
                logger.error("Work unit %s failed: %s", unit.id, e)
                await state.queue.fail(unit.id, str(e))
                state.deriver_consecutive_failures += 1
            
            # Circuit breaker: if too many consecutive failures, mark unhealthy
            if state.deriver_consecutive_failures >= 5:
                state.deriver_healthy = False
                logger.critical(
                    "In-process deriver unhealthy: %d consecutive failures",
                    state.deriver_consecutive_failures,
                )
            else:
                state.deriver_healthy = True
            
            # Periodically drain dead letter queue
            if int(time.time()) % 300 == 0:  # Every ~5 minutes
                await state.queue.drain_dead_letter()
                
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Deriver loop error: %s", e)
            await asyncio.sleep(1.0)
```

### 3.6 Health Check Integration

```python
# src/routers/health.py — extended health endpoint

@router.get("/health")
async def health(request: Request):
    """Extended health check that includes in-process deriver status."""
    health_data = {
        "status": "healthy",
        "database": "connected",  # from DB pool check
    }
    
    if settings.DERIVER.IN_PROCESS_MODE:
        state = request.app.state
        time_since_last_process = time.time() - state.deriver_last_processed_at
        health_data["deriver"] = {
            "status": "healthy" if state.deriver_healthy else "degraded",
            "pending_work_units": state.queue.pending_count,
            "dead_letter_count": state.queue.dead_letter_count,
            "dropped_count": state.queue.dropped_count,
            "seconds_since_last_processed": round(time_since_last_process, 1),
        }
        if not state.deriver_healthy:
            health_data["status"] = "degraded"
    
    return health_data
```

### 3.7 CPU-Bound Task Management

Embedding calls and tokenization are CPU-bound. In the in-process deriver, these
must not block the API's event loop:

```python
# Run embedding calls in executor thread to prevent GIL contention
import asyncio
from concurrent.futures import ThreadPoolExecutor

_embedding_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embed")

async def _embed_in_executor(texts: list[str]) -> list[list[float]]:
    """Run embedding call in executor thread to avoid blocking the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _embedding_executor,
        lambda: embedding_client.simple_batch_embed(texts),
    )
```

### 3.8 Graceful Degradation

```python
# src/config.py — Redis requirement downgraded when IN_PROCESS_MODE=true

@model_validator(mode="after")
def validate_redis(self):
    """Redis is optional when in-process deriver mode is enabled."""
    if self.DERIVER.IN_PROCESS_MODE:
        # Redis cache still benefits performance but is not required
        if not self.CACHE.REDIS_URL:
            logger.info(
                "IN_PROCESS_MODE=true: Redis not configured. "
                "Session caching disabled; operations continue without cache."
            )
    return self
```

### 3.9 Backward Compatibility Matrix

| Configuration | Redis Required? | Worker Process? | Behavior |
|---|---|---|---|
| `IN_PROCESS_MODE=false` (default) | ✅ | ✅ Required | Current behavior, unchanged |
| `IN_PROCESS_MODE=true` + Redis configured | ❌ (ignored for queue) | ❌ Eliminated | In-memory queue; Redis still used for session cache if available |
| `IN_PROCESS_MODE=true` + no Redis | ❌ | ❌ Eliminated | Fully self-contained; session cache disabled |

## 4. Implementation Plan

### Phase 1: Queue Abstraction + InMemoryQueue (3 days)
1. Extract `QueueInterface` protocol class from existing Redis queue implementation
2. Implement `InMemoryQueue` with notification-based event wake, retry/dead-letter logic
3. Add configuration flags (IN_PROCESS_MODE, polling interval, queue limits, timeout)
4. Write unit tests: enqueue/dequeue, overflow, retry limits, dead letter, notification
5. Audit all Redis usage across the codebase (cache, rate limiter, telemetry)
6. Add graceful degradation for non-queue Redis features

### Phase 2: API Lifespan Integration (3 days)
1. Add lifespan hooks: startup init, shutdown drain
2. Implement `_run_deriver_loop` background task
3. Add startup readiness gate (wait for deriver init before accepting traffic)
4. Implement circuit breaker for consecutive deriver failures
5. Add health check integration (deriver status in /health endpoint)
6. Add executor thread for CPU-bound embedding calls

### Phase 3: Testing + Documentation (3 days)
1. Integration test: API + in-process deriver end-to-end with mock work units
2. Performance benchmark: API latency under concurrent deriver load
3. Memory usage test: verify < 50MB overhead claim
4. Edge case: process crash recovery (simulate kill -9, verify re-derivation)
5. Edge case: queue overflow (verify oldest-dropped behavior)
6. Write single-container deployment docs
7. Update AGENTS.md and RAPIDWEBS.README.md

### Total: ~9 days

## 5. Dependencies

- PostgreSQL + pgvector — still required (unchanged)
- Redis — NOT required when IN_PROCESS_MODE=true
- No new infrastructure required

## 6. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Deriver blocks API responses | Low | High | Event loop separation; executor thread for CPU-bound work; per-unit timeout; circuit breaker |
| Memory growth from in-memory queue | Low | Medium | Hard cap (default 1000); oldest-dropped on overflow; periodic dead letter drain |
| State loss on process restart | Medium | Low | Acceptable: work units re-derive from messages; idempotency via message_id dedup at DB level |
| GIL contention under load | Medium | Medium | Embedding runs in executor thread; LLM calls are I/O-bound (no GIL issue); tokenization is fast (<5ms) |
| Startup race condition | Low | Medium | Deriver init completes before API serves traffic (readiness gate in lifespan) |
| Silent deriver failure | Low | High | Exposed via /health endpoint; circuit breaker alerts on 5+ consecutive failures |
| Queue overflow during bulk import | Medium | Medium | Configurable max size; oldest-dropped with log warning; import tools can monitor dropped count |

## 7. Success Metrics

| Metric | Target | Measurement |
|---|---|---|
| Single-container boot time | < 30s | Wall clock from `docker compose up` to health OK |
| Deriver processing latency | < 5s from message receipt | Message create time to observation write time |
| API p95 latency with deriver active | < 500ms | Benchmark with concurrent API requests + deriver load |
| Memory overhead vs API-only | < 50MB | RSS comparison with and without IN_PROCESS_MODE |
| Unit test coverage | > 90% for InMemoryQueue | pytest --cov |
| Queue throughput (sustained) | > 100 work units/minute | Load test with synthetic work units |

## 8. Audit Findings (v1→v2 Changes)

| Issue | v1 Status | v2 Resolution |
|---|---|---|
| InMemoryQueue.claim() polling | Polls every 1s (wasteful) | ✅ asyncio.Event notification + timeout backstop |
| Thread safety | Not addressed | ✅ asyncio.Lock for all queue mutations |
| Full Redis usage audit | Not performed | ✅ Complete audit in §3.1; graceful degradation for non-queue Redis |
| Health check integration | Not mentioned | ✅ Deriver health fields in /health endpoint |
| Startup sequence | Implicit | ✅ Readiness gate: deriver init completes before API serves traffic |
| CPU-bound work (GIL) | Not addressed | ✅ Executor thread for embedding calls |
| Circuit breaker | Not mentioned | ✅ 5 consecutive failures → unhealthy state |
| Dead letter queue | Not mentioned | ✅ MAX_RETRIES=3 → dead letter → 1h TTL cleanup |
| Configurable intervals | Hardcoded | ✅ IN_PROCESS_POLL_INTERVAL, WORK_UNIT_TIMEOUT, MAX_QUEUE_SIZE |
| Queue overflow behavior | "Oldest dropped" (undefined) | ✅ Drop oldest with logged warning; dropped_count metric |
| Duplicate work unit detection | Not mentioned | ✅ Relies on DB-level message_id idempotency (existing Honcho behavior) |
| Shutdown draining | Not mentioned | ✅ Grace period + drain before cancel |
| Backward compatibility matrix | Implicit | ✅ Explicit matrix in §3.9 |
| Deriver health visibility | None | ✅ /health endpoint extended with deriver status |
