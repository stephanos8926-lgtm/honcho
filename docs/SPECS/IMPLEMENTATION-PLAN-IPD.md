# Implementation Plan: In-Process Deriver (SPEC-002 v2.0)

**Feature:** IP-Deriver
**Specification:** `docs/SPECS/SPEC-002-in-process-deriver.md`
**ADR:** `docs/ADRs/ADR-002-in-process-deriver.md`
**Planned Effort:** ~9 days (3 phases)
**Architecture:** Queue abstraction → InMemoryQueue → API lifespan integration → health monitoring
**Backward Compatibility:** Default `IN_PROCESS_MODE=false` — zero behavior change

---

## Phase 1: Queue Abstraction + InMemoryQueue (3 days)

### Pre-Phase Research (0.5 days)

**Research Objective:** Understand Honcho's existing Redis queue implementation, work unit model, and all Redis touchpoints across the codebase.

**Files to read:**
- `src/deriver/queue_manager.py` — Redis-backed queue, work unit claiming/processing/completion
- `src/deriver/work_unit.py` or similar — WorkUnit dataclass/model
- `src/cache/` or `src/cache/client.py` — Redis cache client
- `src/config.py` — DeriverSettings, CacheSettings
- `src/main.py` — lifespan, Redis initialization
- `src/telemetry/` — if Redis is used for telemetry buffering

**Research questions:**
1. What's the exact QueueManager interface? (methods: enqueue, claim, complete, fail, etc.)
2. What data structure represents a WorkUnit? (dataclass, Pydantic, ORM model?)
3. How does the current polling work? (interval, batch size, error handling)
4. Does the API server connect to Redis independently of the Deriver? (for session cache)
5. Are there any Redis-dependent code paths in session management, rate limiting, or telemetry?
6. How does the existing QueueManager handle serialization/deserialization of work units?

**Research output:** `docs/research/phase1-redis-audit.md` — comprehensive list of every Redis connection, with its purpose and whether it's required or optional.

### Task 1.1: Extract QueueInterface protocol

**Objective:** Create a protocol class that both Redis-backed queue and InMemoryQueue can implement.

**Files:**
- Create: `src/deriver/queue_protocol.py`

**Step 1: Write failing test (protocol conformance)**

```python
# tests/deriver/test_queue_protocol.py
from typing import Protocol, runtime_checkable
from src.deriver.queue_protocol import QueueInterface, WorkUnit

def test_queue_interface_is_protocol():
    assert issubclass(QueueInterface, Protocol)

def test_work_unit_dataclass_has_required_fields():
    unit = WorkUnit(id="test-id", workspace_name="ws", session_name="sess")
    assert unit.id == "test-id"
    assert unit.retry_count == 0
    assert unit.last_error is None
```

**Step 2: Implement protocol**

```python
# src/deriver/queue_protocol.py
from dataclasses import dataclass, field
from typing import Optional, Protocol
import time


@dataclass
class WorkUnit:
    """A unit of work for the deriver to process.
    
    This is what gets enqueued by the API when a message arrives and
    claimed by the deriver for processing.
    """
    id: str
    workspace_name: str
    session_name: str
    created_at: float = field(default_factory=time.time)
    retry_count: int = 0
    last_error: Optional[str] = None


class QueueInterface(Protocol):
    """Protocol that both Redis queue and InMemoryQueue must satisfy."""
    
    async def enqueue(self, unit: WorkUnit) -> bool:
        """Enqueue a work unit. Returns False if queue is full."""
        ...
    
    async def claim(self, timeout: float = 1.0) -> Optional[WorkUnit]:
        """Claim the next available work unit. Blocks up to timeout."""
        ...
    
    async def complete(self, unit_id: str) -> None:
        """Mark a work unit as completed."""
        ...
    
    async def fail(self, unit_id: str, error: str = "") -> None:
        """Mark a work unit as failed. Retries up to MAX_RETRIES."""
        ...
```

**Step 3: Run test, commit**

### Task 1.2: Implement InMemoryQueue

**Objective:** Build the full InMemoryQueue with notification, retry, dead letter, overflow handling.

**Files:**
- Create: `src/deriver/in_memory_queue.py`

**Step 1: Write comprehensive tests**

```python
# tests/deriver/test_in_memory_queue.py
import pytest
from src.deriver.in_memory_queue import InMemoryQueue
from src.deriver.queue_protocol import WorkUnit

@pytest.mark.asyncio
async def test_enqueue_and_claim():
    q = InMemoryQueue(max_size=10)
    unit = WorkUnit(id="1", workspace_name="ws", session_name="s")
    assert await q.enqueue(unit) is True
    claimed = await q.claim(timeout=0.1)
    assert claimed is not None
    assert claimed.id == "1"

@pytest.mark.asyncio
async def test_claim_empty_returns_none():
    q = InMemoryQueue(max_size=10)
    claimed = await q.claim(timeout=0.1)
    assert claimed is None

@pytest.mark.asyncio
async def test_queue_overflow_drops_oldest():
    q = InMemoryQueue(max_size=2)
    await q.enqueue(WorkUnit(id="1", workspace_name="ws", session_name="s"))
    await q.enqueue(WorkUnit(id="2", workspace_name="ws", session_name="s"))
    await q.enqueue(WorkUnit(id="3", workspace_name="ws", session_name="s"))
    # "1" should have been dropped
    assert q.dropped_count == 1

@pytest.mark.asyncio
async def test_retry_limit_moves_to_dead_letter():
    q = InMemoryQueue(max_size=10)
    unit = WorkUnit(id="1", workspace_name="ws", session_name="s")
    await q.enqueue(unit)
    for _ in range(3):
        claimed = await q.claim(timeout=0.1)
        await q.fail(claimed.id, "error")
    assert q.dead_letter_count == 1
    assert q.pending_count == 0

@pytest.mark.asyncio
async def test_notification_wakes_consumer():
    q = InMemoryQueue(max_size=10)
    # Enqueue and claim should wake immediately (no polling delay)
    await q.enqueue(WorkUnit(id="1", workspace_name="ws", session_name="s"))
    claimed = await q.claim(timeout=5.0)
    assert claimed is not None  # Should not wait 5s
```

**Step 2: Implement InMemoryQueue**

```python
# src/deriver/in_memory_queue.py
import asyncio
import logging
import time
from collections import OrderedDict
from typing import Optional

from src.deriver.queue_protocol import WorkUnit

logger = logging.getLogger(__name__)


class InMemoryQueue:
    """Drop-in for Redis-backed queue when IN_PROCESS_MODE=true.
    
    Notification-driven (asyncio.Event), not polling-based. Has built-in
    retry limit (3 attempts), dead letter quarantine, and oldest-dropped
    overflow behavior. Thread-safe via asyncio.Lock.
    """
    
    MAX_RETRIES = 3
    DEAD_LETTER_TTL_SECONDS = 3600  # 1 hour
    
    def __init__(self, max_size: int = 1000):
        self._queue: asyncio.Queue[WorkUnit] = asyncio.Queue(maxsize=max_size)
        self._pending: dict[str, WorkUnit] = OrderedDict()
        self._dead_letter: dict[str, tuple[WorkUnit, float]] = {}
        self._lock = asyncio.Lock()
        self._notify = asyncio.Event()
        self._max_size = max_size
        self._dropped_count = 0
    
    async def enqueue(self, unit: WorkUnit) -> bool:
        try:
            self._queue.put_nowait(unit)
        except asyncio.QueueFull:
            # Drop oldest pending to make room
            async with self._lock:
                if self._pending:
                    oldest_id = next(iter(self._pending))
                    self._pending.pop(oldest_id)
                    self._dropped_count += 1
                    logger.warning(
                        "Queue overflow: dropped work unit %s (total: %d)",
                        oldest_id, self._dropped_count,
                    )
            # Retry after dropping
            self._queue.put_nowait(unit)
        
        async with self._lock:
            self._pending[unit.id] = unit
        self._notify.set()
        return True
    
    async def claim(self, timeout: float = 1.0) -> Optional[WorkUnit]:
        try:
            async with asyncio.timeout(timeout):
                await self._notify.wait()
                self._notify.clear()
                return self._queue.get_nowait()
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
                self._dead_letter[unit_id] = (unit, time.time())
                self._pending.pop(unit_id)
                logger.error(
                    "Work unit %s dead-lettered after %d retries: %s",
                    unit_id, unit.retry_count, error,
                )
            else:
                self._queue.put_nowait(unit)
    
    async def drain_dead_letter(self) -> int:
        now = time.time()
        async with self._lock:
            stale = [
                uid for uid, (_, ts) in self._dead_letter.items()
                if now - ts > self.DEAD_LETTER_TTL_SECONDS
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

**Step 3: Run all tests** — `pytest tests/deriver/test_in_memory_queue.py tests/deriver/test_queue_protocol.py -xvs`

**Step 4: Commit**

### Task 1.3: Add configuration flags

**Objective:** Add IN_PROCESS_MODE and related settings to DeriverSettings.

**Files:**
- Modify: `src/config.py` — DeriverSettings class

**Step 1: Write test**

```python
# tests/config/test_deriver_settings.py
from src.config import settings

def test_in_process_mode_default_false():
    assert settings.DERIVER.IN_PROCESS_MODE is False

def test_in_process_mode_env_override(monkeypatch):
    monkeypatch.setenv("DERIVER_IN_PROCESS_MODE", "true")
    # Re-initialize settings with env override
    from src.config import AppSettings
    s = AppSettings()
    assert s.DERIVER.IN_PROCESS_MODE is True
```

**Step 2: Implement config**

```python
# src/config.py — inside DeriverSettings class

IN_PROCESS_MODE: bool = Field(
    default=False,
    description="When True, run deriver as asyncio background task inside the API process. "
                "Queue uses InMemoryQueue instead of Redis. Redis becomes optional.",
)
IN_PROCESS_POLL_INTERVAL_SECONDS: float = Field(
    default=0.5, ge=0.1, le=10.0,
    description="Polling interval for in-process deriver's claim loop.",
)
IN_PROCESS_MAX_QUEUE_SIZE: int = Field(
    default=1000, ge=1, le=100_000,
    description="Maximum pending work units before oldest-dropped overflow.",
)
IN_PROCESS_WORK_UNIT_TIMEOUT_SECONDS: float = Field(
    default=30.0, ge=1.0, le=300.0,
    description="Per-work-unit timeout. Exceeding triggers retry.",
)
```

**Step 3: Run tests, commit**

### Task 1.4: Full Redis usage audit

**Objective:** Document every Redis connection in the codebase and determine which are required vs optional.

**Files:**
- Create: `docs/research/redis-audit.md`

**Step 1: Search for Redis references**

```bash
grep -rn "redis\|Redis\|aioredis\|redis_url\|REDIS" src/ --include="*.py" | grep -v __pycache__ | grep -v ".pyc"
```

**Step 2: Categorize each usage**

| File | Line | Purpose | Required? | Impact if Redis down |
|---|---|---|---|---|
| `src/cache/client.py` | 42 | Session cache | Optional | API serves without cache |
| `src/deriver/queue_manager.py` | 15 | Work unit queue | Required for deriver | Deriver stalls |
| `src/main.py` | 88 | Redis connection init | Required | Process fails |
| ... | ... | ... | ... | ... |

**Step 3: Document fallback strategy** for each non-queue Redis usage when IN_PROCESS_MODE=true (e.g., in-process LRU for cache, in-process counter for rate limiting)

---

## Phase 2: API Lifespan Integration (3 days)

### Pre-Phase Research (0.5 days)

**Research Objective:** Understand the API lifespan, app state initialization, and shutdown sequence.

**Files to read:**
- `src/main.py` — lifespan function, app state initialization
- `src/deriver/__main__.py` — deriver entry point (to understand what the deriver initializes)
- `src/deriver/deriver.py` — main loop

### Task 2.1: Add lifespan hooks

**Files:**
- Modify: `src/main.py` — lifespan function
- Modify: `src/app_state.py` or equivalent — add queue, deriver state fields

**Step 1: Write test**

```python
# tests/test_in_process_lifespan.py
@pytest.mark.asyncio
async def test_in_process_deriver_starts_with_lifespan():
    # Simulate the lifespan startup with IN_PROCESS_MODE=true
    # Verify app.state.queue is InMemoryQueue
    # Verify app.state.deriver_healthy is True
    pass

@pytest.mark.asyncio
async def test_in_process_deriver_stops_on_shutdown():
    # Simulate lifespan shutdown
    # Verify pending work units are logged
    pass
```

### Task 2.2: Implement background deriver loop

**Files:**
- Create: `src/deriver/in_process_runner.py`

**Step 1: Write test**

```python
# tests/deriver/test_in_process_runner.py
@pytest.mark.asyncio
async def test_runner_processes_work_units():
    from src.deriver.in_process_runner import _run_deriver_loop
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    
    state = MagicMock()
    state.queue = InMemoryQueue(max_size=10)
    state.deriver_healthy = True
    state.deriver_consecutive_failures = 0
    
    unit = WorkUnit(id="test", workspace_name="ws", session_name="s")
    await state.queue.enqueue(unit)
    
    # Run loop briefly, cancel
    task = asyncio.create_task(_run_deriver_loop(state))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    
    assert state.queue.pending_count == 0
```

### Task 2.3: Add health check integration

**Files:**
- Modify: `src/routers/health.py` or equivalent

**Step 1: Write test**

```python
# tests/routers/test_health.py
@pytest.mark.asyncio
async def test_health_returns_deriver_status(async_client):
    with override_settings(DERIVER={"IN_PROCESS_MODE": True}):
        resp = await async_client.get("/health")
        data = resp.json()
        assert "deriver" in data
        assert data["deriver"]["status"] in ("healthy", "degraded")
```

---

## Phase 3: Testing + Documentation (3 days)

### Pre-Phase Research

**Objective:** Understand how existing Honcho integration tests work (fixtures, test client, DB setup).

**Files to read:**
- `tests/conftest.py` — test fixtures, async_client, db_session
- `tests/deriver/` — existing deriver tests for patterns

### Task 3.1: Integration test — end-to-end

**Step 1: Write test**

```python
# tests/integration/test_in_process_deriver_e2e.py
@pytest.mark.asyncio
async def test_message_to_observation_in_process(async_client, db_session):
    """End-to-end: create message → deriver processes → observation appears."""
    with override_settings(DERIVER={"IN_PROCESS_MODE": True}):
        # Create workspace, peer, session, message
        # Wait for deriver to process (poll with timeout)
        # Assert observation was created
        pass
```

### Task 3.2: Performance benchmark

**Step 1: Write benchmark script**

```python
# tests/bench/test_in_process_throughput.py
@pytest.mark.bench
async def test_api_latency_under_deriver_load():
    """API p95 should stay under 500ms while deriver processes work units."""
    pass
```

### Task 3.3: Documentation

**Files:**
- Create: `docs/deployment/single-container.md`
- Modify: `RAPIDWEBS.README.md` — add in-process deriver entry
- Modify: `AGENTS.md` — add in-process deriver notes

**Documentation content:**
```markdown
# Single-Container Deployment (In-Process Deriver)

Prerequisites:
- PostgreSQL 16+ with pgvector
- Python 3.13+

Quick start:
```bash
# Clone and install
git clone https://github.com/stephanos8926-lgtm/honcho.git
cd honcho
uv sync

# Configure .env
cat > .env << EOF
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/honcho
DERIVER_IN_PROCESS_MODE=true
EMBEDDING_MODEL_CONFIG__TRANSPORT=rw_inference
EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=http://localhost:8300/v1
EMBEDDING_VECTOR_DIMENSIONS=384
EOF

# Run (single process — API + Deriver)
uv run python -m src.main
```
```
