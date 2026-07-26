"""In-process executor management for CPU-bound operations.

Provides a shared ThreadPoolExecutor that prevents GIL contention when
CPU-bound operations (tokenization, serialization) run inside the API
process alongside the async event loop.

Used by the in-process deriver to ensure embedding tokenization and
data serialization don't block API request handling.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# Shared executor for CPU-bound tasks. Single worker is sufficient because
# CPU-bound work is serialized by the GIL anyway — more workers just add
# context-switch overhead.
_shared_executor: ThreadPoolExecutor | None = None


def get_executor() -> ThreadPoolExecutor:
    """Get or create the shared CPU-bound executor."""
    global _shared_executor
    if _shared_executor is None:
        _shared_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="cpu-bound",
        )
        logger.debug("Created CPU-bound executor")
    return _shared_executor


async def run_cpu_bound(fn, *args, **kwargs):
    """Run a CPU-bound function in the executor thread.
    
    Use this for tokenization, serialization, and other CPU-intensive
    operations that would otherwise block the async event loop.
    
    Example:
        tokens = await run_cpu_bound(encoding.encode, text)
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        get_executor(),
        lambda: fn(*args, **kwargs),
    )


def shutdown_executor() -> None:
    """Shutdown the shared executor. Called during API shutdown."""
    global _shared_executor
    if _shared_executor:
        _shared_executor.shutdown(wait=False)
        _shared_executor = None
        logger.debug("Shut down CPU-bound executor")
