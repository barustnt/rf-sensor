from __future__ import annotations

import subprocess
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.backend.services.ingestion import job_summary
from rf_platform.backend.services.registry import derive_operational_status
from rf_platform.common.config import Settings
from rf_platform.common.time import utc_now
from rf_platform.worker.rfgpt.local import LocalVLLMRFGPTAdapter


def percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


async def operational_metrics(
    session: AsyncSession,
    settings: Settings,
    request_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = utc_now()
    sensors = list((await session.execute(select(models.Sensor))).scalars())
    statuses = {"online": 0, "degraded": 0, "offline": 0, "stale": 0}
    heartbeat_ages = []
    for sensor in sensors:
        status = derive_operational_status(sensor, settings.offline_after_seconds)
        statuses[status] = statuses.get(status, 0) + 1
        if sensor.last_seen_at_utc:
            heartbeat_ages.append(max(0.0, (now - sensor.last_seen_at_utc).total_seconds()))
    jobs = await job_summary(session)
    latency_rows = list(
        (
            await session.execute(
                select(models.ModelRun.latency_ms)
                .order_by(models.ModelRun.completed_at_utc.desc())
                .limit(500)
            )
        ).scalars()
    )
    capture_count_15m = (
        await session.execute(
            select(func.count())
            .select_from(models.Capture)
            .where(models.Capture.started_at_utc >= now - timedelta(minutes=15))
        )
    ).scalar_one()
    upload_bytes = (
        await session.execute(select(func.coalesce(func.sum(models.Artifact.byte_size), 0)))
    ).scalar_one()
    oldest_pending = jobs.get("oldest_pending_at_utc")
    model_health = await _model_health(settings)
    return {
        "timestamp_utc": now.isoformat(),
        "requests": request_metrics or {"count": 0, "avg_latency_ms": None, "by_status": {}},
        "sensors": {
            "total": len(sensors),
            **statuses,
            "max_heartbeat_age_seconds": max(heartbeat_ages) if heartbeat_ages else None,
        },
        "captures": {
            "last_15m": capture_count_15m,
            "rate_per_minute_15m": round(capture_count_15m / 15.0, 3),
            "accepted_upload_bytes": int(upload_bytes),
        },
        "jobs": {
            **jobs,
            "queue_depth": int(jobs.get("pending", 0)),
            "oldest_pending_at_utc": oldest_pending,
        },
        "model": {
            "inference_count": len(latency_rows),
            "latency_ms_p50": percentile(latency_rows, 50),
            "latency_ms_p95": percentile(latency_rows, 95),
            "adapter": settings.rfgpt_adapter,
            "model_name": settings.rfgpt_model_name,
            "model_version": settings.rfgpt_model_version,
            "health": model_health,
        },
        "gpu": gpu_metrics(),
    }


async def _model_health(settings: Settings) -> dict[str, Any]:
    if settings.rfgpt_adapter == "vllm":
        return (await LocalVLLMRFGPTAdapter(settings).health()).model_dump(mode="json")
    return {
        "adapter": settings.rfgpt_adapter,
        "ready": settings.rfgpt_adapter == "mock",
        "model_name": settings.rfgpt_model_name,
        "model_version": settings.rfgpt_model_version,
        "message": "mock adapter ready" if settings.rfgpt_adapter == "mock" else "not checked",
        "details": {},
    }


def gpu_metrics() -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,memory.used,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            capture_output=True,
            check=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "message": "nvidia-smi unavailable"}
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    parts = [part.strip() for part in line.split(",")]
    if len(parts) != 5:
        return {"available": False, "message": "unexpected nvidia-smi output"}
    name, total, free, used, temp = parts
    try:
        return {
            "available": True,
            "name": name,
            "memory_total_mib": int(total),
            "memory_free_mib": int(free),
            "memory_used_mib": int(used),
            "temperature_c": int(temp),
        }
    except ValueError:
        return {"available": False, "message": "unexpected nvidia-smi numeric output"}
