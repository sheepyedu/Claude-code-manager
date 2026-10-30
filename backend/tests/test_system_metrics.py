"""Host metrics must work without Linux procfs and without blocking ASGI."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
import psutil

from backend.services import system_metrics


@pytest.mark.asyncio
async def test_metrics_work_without_procfs_and_keep_event_loop_responsive(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("no procfs on macOS")))
    ticks = []

    def sample(interval):
        assert interval == 0.1
        time.sleep(0.03)
        assert ticks, "CPU sampling blocked the event loop"
        return 23.4

    monkeypatch.setattr(psutil, "cpu_percent", sample)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(total=1000, available=600))
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(total=200, used=50))

    async def heartbeat():
        await asyncio.sleep(0)
        ticks.append(True)

    metrics, _ = await asyncio.gather(system_metrics._collect(), heartbeat())
    assert metrics["cpu"]["utilization_percent"] == 23.4
    assert metrics["memory"]["total_bytes"] == 1000
    assert metrics["memory"]["available_bytes"] == 600
    assert metrics["memory"]["used_bytes"] == 400
    assert metrics["memory"]["used_percent"] == 40
    assert metrics["memory"]["swap_used_percent"] == 25
    assert metrics["storage"]["root"]["total_bytes"] > 0


def test_zero_swap_is_reported_without_division_by_zero(monkeypatch):
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(total=1000, available=1000))
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(total=0, used=0))
    metrics = system_metrics._memory_metrics()
    assert metrics["used_percent"] == 0
    assert metrics["swap_used_percent"] == 0


@pytest.mark.asyncio
async def test_cached_snapshot_isolated_from_callers_and_force_refresh(monkeypatch):
    from unittest.mock import AsyncMock

    system_metrics.reset_metrics_cache()
    collect = AsyncMock(return_value={"cpu": {"utilization_percent": 12.3}})
    monkeypatch.setattr(system_metrics, "_collect", collect)
    try:
        first = await system_metrics.get_system_metrics()
        first["cpu"]["utilization_percent"] = 99
        cached = await system_metrics.get_system_metrics()
        assert cached["cached"] is True
        assert cached["cpu"]["utilization_percent"] == 12.3
        assert collect.await_count == 1
        await system_metrics.get_system_metrics(force=True)
        assert collect.await_count == 2
    finally:
        system_metrics.reset_metrics_cache()
