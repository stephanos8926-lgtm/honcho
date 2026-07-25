# ADR-002: In-Process Deriver / Single-Container Mode

## Status: Draft

## Context
Honcho's separate-worker architecture (API + Deriver + Redis + PG) is the
#1 barrier to self-hosted adoption (#494, #822, #789). Competitors (Hindsight,
Mem0 library mode, ByteRover) offer single-process deployments. Our own
deployment on an 8GB Hetzner VPS doesn't warrant the overhead of a full
Redis-backed queue for single-user traffic.

## Decision
Add an opt-in `DERIVER_IN_PROCESS_MODE=true` flag that runs the Deriver worker
as an asyncio background task within the API process, using an in-memory queue
instead of Redis. Default remains `false` for backward compatibility.

## Consequences
- Positive: Single-container deployment (API + Deriver together)
- Positive: No Redis dependency when in-process mode is enabled
- Positive: Simplifies self-hosted setup dramatically
- Positive: Zero config change for existing deployments (opt-in)
- Negative: Queue state lost on restart (acceptable: re-derives from messages)
- Negative: CPU contention between API and Deriver (acceptable: I/O-bound)
- Risk: Deriver could block API under load (mitigated: timeout + backpressure)

## Compliance
- Existing behavior unchanged when `DERIVER_IN_PROCESS_MODE=false` (default)
- QueueInterface abstraction allows swapping Redis ↔ in-memory transparently
- Reconciler (vector sync) works identically in both modes

## References
- SPEC-002: In-Process Deriver / Single-Container Mode
- GitHub issues #494, #822, #789 (self-hosted pain points)
- elkimek/honcho-self-hosted (community deployment script)
