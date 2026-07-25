"""In-process deriver: runs the QueueManager polling loop as a background task
inside the API process when DERIVER_IN_PROCESS_MODE=true.

Uses a subclass of QueueManager that skips signal handler registration and
Sentry re-initialization (the API process already handles both).
"""

import asyncio
import logging
import time
from typing import Optional

from src.deriver.queue_manager import QueueManager

logger = logging.getLogger(__name__)


class InProcessQueueManager(QueueManager):
    """QueueManager variant that runs inside the API process.
    
    Differences from QueueManager:
    - No signal handler registration (API manages its own signals)
    - No Sentry initialization (API already handles it)
    - Health status exposed via properties
    - Controlled lifecycle via start()/stop()
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._in_process_task: Optional[asyncio.Task[None]] = None
        self.healthy: bool = False
        self.started_at: float = 0.0
    
    async def start(self) -> None:
        """Initialize and start the polling loop as a background task."""
        logger.info("Starting in-process deriver...")
        self.started_at = time.time()
        
        # Start the reconciler scheduler directly (skipping signal handlers)
        try:
            await self.reconciler_scheduler.start()
        except Exception:
            logger.exception("Failed to start reconciler scheduler")
        
        # Run the polling loop as a background task
        self._in_process_task = asyncio.create_task(self._run_with_health())
        self.healthy = True
        logger.info("In-process deriver started successfully")
    
    async def _run_with_health(self) -> None:
        """Run polling loop with health tracking."""
        try:
            await self._sleep_startup_jitter()
            await self.polling_loop()
        except asyncio.CancelledError:
            logger.info("In-process deriver polling loop cancelled")
            raise
        except Exception as e:
            self.healthy = False
            logger.critical("In-process deriver polling loop failed: %s", e)
        finally:
            self.healthy = False
            await self.cleanup()
    
    async def stop(self) -> None:
        """Gracefully stop the in-process deriver."""
        if not self._in_process_task:
            return
        
        logger.info("Stopping in-process deriver...")
        self.shutdown_event.set()
        
        # Stop the reconciler scheduler
        await self.reconciler_scheduler.shutdown()
        
        # Wait for graceful shutdown with timeout
        try:
            await asyncio.wait_for(self._in_process_task, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("In-process deriver did not stop gracefully, cancelling...")
            self._in_process_task.cancel()
            try:
                await self._in_process_task
            except (asyncio.CancelledError, Exception):
                pass
        
        logger.info("In-process deriver stopped")
    
    @property
    def status(self) -> dict:
        """Health status for the API /health endpoint."""
        now = time.time()
        uptime = now - self.started_at if self.started_at > 0 else 0
        
        return {
            "status": "healthy" if self.healthy else "degraded",
            "uptime_seconds": round(uptime, 1),
            "pending_work_units": self.get_total_owned_work_units(),
        }
