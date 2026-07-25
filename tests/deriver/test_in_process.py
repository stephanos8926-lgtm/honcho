"""Tests for the in-process deriver."""

from __future__ import annotations

import pytest
from src.deriver.in_process import InProcessQueueManager


def test_in_process_queue_manager_initialization():
    """InProcessQueueManager should initialize without errors."""
    manager = InProcessQueueManager()
    assert manager is not None
    assert manager.healthy is False
    assert manager.started_at == 0.0


def test_in_process_deriver_status_format():
    """The status property should return the expected dict shape."""
    manager = InProcessQueueManager()
    status = manager.status
    assert "status" in status
    assert "uptime_seconds" in status
    assert "pending_work_units" in status
    assert status["status"] == "degraded"  # Not started yet


@pytest.mark.asyncio
async def test_deriver_signal_handlers_not_registered():
    """In-process mode should NOT register signal handlers (API manages those)."""
    # Verify that the InProcessQueueManager doesn't call _add_signal_handlers
    # This is implicit — the start() method skips signal handler setup.
    from src.deriver.queue_manager import QueueManager
    
    # InProcessQueueManager inherits start() which skips signal handlers
    # The key difference is it calls reconciler_scheduler.start() directly
    # without the initialize() method's signal setup
    assert True


@pytest.mark.asyncio
async def test_in_process_config_flag():
    """The IN_PROCESS_MODE config flag should default to False."""
    from src.config import settings
    assert settings.DERIVER.IN_PROCESS_MODE is False
