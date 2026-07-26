# SPEC-002 v3.0: In-Process Deriver / Single-Container Mode

## Status: Draft v3.0 (Post-Pipeline-Audit Revision)

## 1. Executive Summary

Add an opt-in in-process deriver mode (`DERIVER_IN_PROCESS_MODE=true`) that runs
the existing `QueueManager` polling loop as an asyncio background task within the
API process, eliminating the need for a separate deriver worker process.

**Key correction from v2:** The queue is PostgreSQL-backed (not Redis-backed), so
no `InMemoryQueue` is needed. The existing `QueueManager` runs in-process via
`InProcessQueueManager` (a subclass that skips signal handler setup and Sentry
init).

## 2. Implementation Status

| Component | Status | Notes |
|---|---|---|
| InProcessQueueManager | ✅ Written | `src/deriver/in_process.py` |
| IN_PROCESS_MODE config flag | ✅ Written | `src/config.py` DeriverSettings |
| API lifespan integration | ✅ Written | `src/main.py` |
| Unit tests | ✅ Written | `tests/deriver/test_in_process.py` |
| Health check integration | ❌ Missing | /health endpoint not extended |
| Executor thread for CPU work | ❌ Missing | Embedding calls not in executor |
| Redis graceful degradation | ❌ Missing | Cache/rate-limiter fallbacks |

## 3. Architecture

```
Separate process mode (default):
  API process ──→ PG
  Deriver process (polling loop) ──→ PG

In-process mode (IN_PROCESS_MODE=true):
  API process ──→ PG
    └── background task: QueueManager.polling_loop()
```

The `InProcessQueueManager` class extends `QueueManager` with:
- `start()` — initializes reconciler, starts polling loop as background task
- `stop()` — signals shutdown, waits for graceful stop (10s timeout), then cancels
- `status` property — returns health dict for /health endpoint

## 4. Remaining Work

### 4.1 Health Check Integration
Modify the API /health endpoint to include in-process deriver status when
IN_PROCESS_MODE=true. The `InProcessQueueManager.status` property returns:
```json
{
  "status": "healthy|degraded",
  "uptime_seconds": 123.4,
  "pending_work_units": 0
}
```

**File:** `src/routers/health.py`

### 4.2 Executor Thread for Embeddings
Wrap embedding calls in `concurrent.futures.ThreadPoolExecutor` to prevent
GIL contention when the Deriver processes work units inside the API process.

**File:** To be determined (may be in the deriver's consumer code)

### 4.3 Redis Graceful Degradation
When IN_PROCESS_MODE=true and Redis is not configured:
- Session cache falls back to in-process LRU
- Rate limiting falls back to in-process counter
- Telemetry buffer falls back to in-process buffer
- API does NOT crash if Redis is unavailable

**File:** `src/cache/client.py`

## 5. Audit Trail (v2→v3)

| Issue | v2 Status | v3 Resolution |
|---|---|---|
| Spec assumed Redis-backed queue | ❌ Wrong assumption | ✅ Corrected: PG-backed queue; no InMemoryQueue needed |
| InMemoryQueue described in spec | ❌ Misleading | ✅ Removed; replaced with InProcessQueueManager approach |
| Health check not implemented | ❌ Not coded | ✅ Identified as remaining work (§4.1) |
| Executor thread not implemented | ❌ Not coded | ✅ Identified as remaining work (§4.2) |
| Redis graceful degradation | ❌ Not coded | ✅ Identified as remaining work (§4.3) |
