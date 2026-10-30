"""Low-overhead host resource metrics with a bounded refresh interval."""

from __future__ import annotations

import asyncio
import copy
import os
import shutil
import time
from datetime import datetime, timezone
from typing import Any

import psutil


CACHE_TTL_SECONDS = 60 * 60
_cache: dict[str, Any] | None = None
_cache_at = 0.0
_cache_lock = asyncio.Lock()


async def _cpu_metrics() -> dict[str, Any]:
    # psutil uses native counters on macOS/Linux. The blocking sampling interval
    # belongs in a worker thread so a dashboard refresh cannot stall ASGI.
    utilization = await asyncio.to_thread(psutil.cpu_percent, interval=0.1)
    load_1m = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
    return {
        "utilization_percent": round(max(0.0, min(100.0, utilization)), 1),
        "load_1m": round(load_1m, 2),
        "cores": os.cpu_count() or 1,
    }


def _memory_metrics() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    total = memory.total
    available = memory.available
    used = max(total - available, 0)
    return {
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "used_percent": round((used / total * 100) if total else 0.0, 1),
        "swap_total_bytes": swap.total,
        "swap_used_bytes": swap.used,
        "swap_used_percent": round(
            (swap.used / swap.total * 100) if swap.total else 0.0, 1
        ),
    }


def _disk_metrics(path: str) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    return {
        "path": path,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": round(
            (usage.used / usage.total * 100) if usage.total else 0.0, 1
        ),
    }


async def _collect() -> dict[str, Any]:
    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "cpu": await _cpu_metrics(),
        "memory": _memory_metrics(),
        "storage": {
            "root": _disk_metrics("/"),
            "tmp": _disk_metrics("/tmp"),
        },
    }


async def get_system_metrics(*, force: bool = False) -> dict[str, Any]:
    """Return cached host metrics, refreshing at most once per hour by default."""

    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and not force and now - _cache_at < CACHE_TTL_SECONDS:
        result = copy.deepcopy(_cache)
        result["cached"] = True
        result["cache_age_seconds"] = max(0, int(now - _cache_at))
        return result

    async with _cache_lock:
        now = time.monotonic()
        if _cache is not None and not force and now - _cache_at < CACHE_TTL_SECONDS:
            result = copy.deepcopy(_cache)
            result["cached"] = True
            result["cache_age_seconds"] = max(0, int(now - _cache_at))
            return result
        result = await _collect()
        _cache = copy.deepcopy(result)
        _cache_at = time.monotonic()
        result["cached"] = False
        result["cache_age_seconds"] = 0
        return result


def reset_metrics_cache() -> None:
    """Reset the process cache for tests and controlled service lifecycle."""

    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0
