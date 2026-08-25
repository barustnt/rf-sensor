from __future__ import annotations

import time
from pathlib import Path

import psutil

from rf_platform.contracts.sensor import DiskStatus, SystemStatus

_PROCESS_START = time.monotonic()


def disk_status(path: Path) -> DiskStatus:
    path.mkdir(parents=True, exist_ok=True)
    usage = psutil.disk_usage(str(path))
    return DiskStatus(
        total_bytes=usage.total,
        free_bytes=usage.free,
        used_percent=float(usage.percent),
    )


def system_status() -> SystemStatus:
    return SystemStatus(
        cpu_percent=float(psutil.cpu_percent(interval=None)),
        memory_percent=float(psutil.virtual_memory().percent),
        process_uptime_seconds=max(0.0, time.monotonic() - _PROCESS_START),
    )
