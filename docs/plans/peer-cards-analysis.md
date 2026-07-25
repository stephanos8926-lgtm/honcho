# Peer Cards in Self-Hosted Honcho

## Current Status: ✅ Fully Available

Peer cards are **already implemented and available in self-hosted Honcho**. No code changes are needed to enable them.

## What Peer Cards Are

Peer cards are structured representations that one peer (observer) builds of another peer (observed). They're stored in the `internal_metadata` JSON field of the `Peer` model as:
- `peer_card` — when observer == observed (self-card)
- `{observed}_peer_card` — when observer != observed (other-card)

Each peer card is a **list of strings** capturing key traits, context, and behavioral patterns.

## How They Work

### 1. Generation (Dreamer)
The **Dreamer** background worker (`src/dreamer/orchestrator.py`) generates peer cards by:
- Analyzing message history for the observer/observed pair
- Using LLM-powered analysis to extract patterns
- Writing results back to `peer.internal_metadata`

Relevant test: `tests/dreamer/test_card_refresh.py`

### 2. Retrieval
```python
from src.crud.peer_card import get_peer_card, set_peer_card

# Get what Alice thinks about Bob
card = await get_peer_card(
    db, "workspace_name",
    observer="alice", observed="bob"
)  # Returns list[str] | None
```

### 3. Consumption (Dialectic)
The **Dialectic** agent uses peer cards automatically when:
- Generating responses in `src/dialectic/chat.py`
- Building context in `src/dialectic/core.py` and `src/dialectic/prompts.py`
- The prompts include peer card context for personalized responses

### 4. API Endpoints (Python SDK)
```python
from honcho import Honcho

client = Honcho(environment="local", base_url="http://localhost:8000")
ws = client.create_workspace("demo")

# Peer cards are accessed via the SDK
alice = ws.create_peer("alice")
# Peer card for alice observed by alice:
card_hint = {"peer_card": alice.internal_metadata.get("peer_card")}
```

## Architecture Summary

```
Peer interactions → Deriver (background) → Stores observations
                                          ↓
                              Dreamer (background) → Generates peer cards
                                          ↓
                              Stored in Peer.internal_metadata
                                          ↓
                              Dialectic agent → Reads peer cards → Personalizes responses
```

## Configuration

Peer cards are enabled by default when:
1. The Deriver worker is running (background message processing)
2. The Dreamer is running (background card generation)
3. LLM providers are configured (for the Dreamer's analysis)

No special flags or configuration needed.

## What Would Need Custom Development (If Desired)

The existing peer card system is comprehensive. Custom development would only be needed for:

1. **Custom card format** — Different from the current list-of-strings format
2. **Custom refresh schedule** — Different from the Dreamer's default timing
3. **Alternative generation approach** — Using a different model/technique than the Dreamer
4. **External card storage** — Different from `internal_metadata` JSON field

None of these are required for basic functionality.
