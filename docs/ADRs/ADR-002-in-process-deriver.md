# ADR-002 v2.0: In-Process Deriver / Single-Container Mode

## Status: Draft v2.0 (Post-Audit Revision)

## Context
Honcho's separate-worker architecture is the #1 self-hosted barrier. The v1
spec missed: full Redis audit, notification-based queue, thread safety,
CPU-bound task management, circuit breaker, and health check integration.

## Decision
Add opt-in `DERIVER_IN_PROCESS_MODE=true` with:
1. `InMemoryQueue` with asyncio.Event notification (not polling)
2. Thread-safe queue via asyncio.Lock
3. Redis usage audit: queue eliminated; cache/rate-limiter/telemetry degrade gracefully
4. Executor thread for CPU-bound embedding calls (GIL avoidance)
5. Circuit breaker: unhealthy after 5 consecutive failures
6. Dead letter queue: MAX_RETRIES=3 → dead letter → 1h TTL cleanup
7. Startup readiness gate: deriver init completes before API serves traffic
8. Health check: deriver status in /health endpoint
9. Graceful shutdown: drain remaining work units before cancel

## Consequences
- Positive: Single-container deployment possible
- Positive: Redis eliminated as hard dependency
- Positive: No config change for existing deployments (default=false)
- Positive: Health visibility (deriver status in API health endpoint)
- Negative: < 50MB memory overhead from deriver deps in API process
- Negative: Queue lost on restart (acceptable: re-derives from messages)
- Risk: GIL contention under load (mitigated: executor thread for CPU work)

## Compliance
- Default `false` → zero behavior change for existing deployments
- `QueueInterface` protocol allows Redis ↔ InMemoryQueue swap
- All existing Honcho tests pass unchanged when IN_PROCESS_MODE=false

## References
- SPEC-002 v2.0 (this revision)
- v1 audit: forward, reverse, adversarial reviews
- GitHub issues #494, #822, #789
