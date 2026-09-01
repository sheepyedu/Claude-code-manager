"""Low-overhead host resource metrics with a bounded refresh interval."""

from __future__ import annotations

import asyncio
import copy
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CACHE_TTL_SECONDS = 60 * 60
_cache: dict[str, Any] | None = None
_cache_at = 0.0
_cache_lock = asyncio.Lock()


def _read_cpu_times() -> tuple[int, int]:
    """Return total and idle jiffies for the aggregate CPU line."""

    line = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0]
    fields = line.split()
    if not fields or fields[0] != "cpu":
        raise RuntimeError("aggregate CPU statistics are unavailable")
    values = [int(value) for value in fields[1:]]
    total = sum(values)
    idle = (values[3] if len(values) > 3 else 0) + (
        values[4] if len(values) > 4 else 0
    )
    return total, idle


async def _cpu_metrics() -> dict[str, Any]:
    before_total, before_idle = _read_cpu_times()
    # A short delta gives utilization rather than a load-based estimate while
    # keeping the endpoint non-blocking for the rest of the application.
    await asyncio.sleep(0.1)
    after_total, after_idle = _read_cpu_times()
    total_delta = max(after_total - before_total, 1)
    idle_delta = max(after_idle - before_idle, 0)
    utilization = max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100))
    load_1m = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
    return {
        "utilization_percent": round(utilization, 1),
        "load_1m": round(load_1m, 2),
        "cores": os.cpu_count() or 1,
    }


def _memory_metrics() -> dict[str, Any]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        key, _, raw = line.partition(":")
        if not _:
            continue
        parts = raw.strip().split()
        if parts:
            # /proc/meminfo reports byte-sized values as kB.
            values[key] = int(parts[0]) * 1024

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    used = max(total - available, 0)
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    swap_used = max(swap_total - swap_free, 0)
    return {
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "used_percent": round((used / total * 100) if total else 0.0, 1),
        "swap_total_bytes": swap_total,
        "swap_used_bytes": swap_used,
        "swap_used_percent": round(
            (swap_used / swap_total * 100) if swap_total else 0.0, 1
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
