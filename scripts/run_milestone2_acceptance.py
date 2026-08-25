from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import sys
import time
from datetime import timedelta
from typing import Any

from run_demo import (  # noqa: E402
    ROOT,
    _assert,
    _base_env,
    _free_port,
    _run,
    _run_worker_once,
    _sensor_cycle,
    _start_api,
    _stop_process,
    _wait_ready,
)

SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import select  # noqa: E402

from rf_platform.backend.db import models  # noqa: E402
from rf_platform.backend.db.session import create_engine, create_sessionmaker  # noqa: E402
from rf_platform.common.config import get_settings, reset_settings_cache  # noqa: E402
from rf_platform.common.ids import new_id  # noqa: E402
from rf_platform.common.time import utc_now  # noqa: E402
from rf_platform.dashboard.api_client import DashboardApiClient  # noqa: E402
from rf_platform.dashboard.tabs.outputs import render_output_detail  # noqa: E402


async def _seed_failed_job() -> str:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_sessionmaker(engine)
    async with factory() as session:
        capture = (await session.execute(select(models.Capture))).scalars().first()
        if capture is None:
            raise AssertionError("failed-job fixture requires an existing capture")
        job_id = new_id()
        now = utc_now()
        session.add(
            models.AnalysisJob(
                job_id=job_id,
                capture_id=capture.capture_id,
                model_name=settings.rfgpt_model_name,
                model_version=settings.rfgpt_model_version,
                prompt_version="retry-fixture-v1",
                status="failed",
                attempt_count=1,
                available_at_utc=now,
                error_category="fixture",
                error_message="fixture failure for audited retry acceptance",
                started_at_utc=now - timedelta(seconds=5),
                completed_at_utc=now,
                created_at_utc=now - timedelta(seconds=10),
                updated_at_utc=now,
            )
        )
        session.add(
            models.SystemEvent(
                severity="error",
                service="acceptance",
                event_type="retry_fixture_created",
                message=f"Created failed retry fixture {job_id}",
                sensor_id=capture.sensor_id,
                correlation_id=capture.correlation_id,
                context={"job_id": job_id},
                timestamp_utc=now,
            )
        )
        await session.commit()
    await engine.dispose()
    return job_id


async def _seed_retention_fixture() -> str:
    settings = get_settings()
    object_key = "retention-fixture/old-spectrogram.png"
    artifact_path = settings.artifact_root / object_key
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"retention fixture artifact")
    engine = create_engine(settings)
    factory = create_sessionmaker(engine)
    async with factory() as session:
        sensor = (await session.execute(select(models.Sensor))).scalars().first()
        if sensor is None:
            raise AssertionError("retention fixture requires an existing sensor")
        capture_id = new_id()
        now = utc_now()
        old = now - timedelta(days=settings.retention_artifact_days + 7)
        artifact_id = new_id()
        session.add(
            models.Capture(
                capture_id=capture_id,
                sensor_id=sensor.sensor_id,
                session_id="retention-fixture",
                correlation_id=f"retention-{capture_id}",
                profile_id="campus_general",
                started_at_utc=old,
                ended_at_utc=old + timedelta(seconds=1),
                radio={
                    "center_frequency_hz": 2_440_000_000,
                    "sample_rate_sps": 2_000_000,
                    "bandwidth_hz": 2_000_000,
                },
                preprocessing={"pipeline_version": "fixture"},
                dsp_metrics={},
                state="accepted",
                metadata_fingerprint="r" * 64,
                created_at_utc=old,
                received_at_utc=old,
                updated_at_utc=old,
            )
        )
        await session.flush()
        session.add(
            models.Artifact(
                artifact_id=artifact_id,
                capture_id=capture_id,
                kind="spectrogram",
                backend="filesystem",
                object_key=object_key,
                mime_type="image/png",
                byte_size=artifact_path.stat().st_size,
                sha256="2" * 64,
                retention_class="ordinary",
                created_at_utc=old,
            )
        )
        session.add(
            models.SystemEvent(
                severity="debug",
                service="acceptance",
                event_type="old_log_fixture",
                message="Old log entry for retention report-only acceptance",
                sensor_id=sensor.sensor_id,
                correlation_id=None,
                context={},
                timestamp_utc=now - timedelta(days=settings.retention_log_days + 7),
            )
        )
        await session.commit()
    await engine.dispose()
    return artifact_id


async def _seed_storage_trend(current_free_bytes: int, target_id: str) -> None:
    engine = create_engine(get_settings())
    factory = create_sessionmaker(engine)
    async with factory() as session:
        now = utc_now()
        total = max(current_free_bytes + 100_000_000, 200_000_000)
        samples = [
            (now - timedelta(hours=2), current_free_bytes + 20_000_000),
            (now - timedelta(hours=1), current_free_bytes + 10_000_000),
        ]
        for timestamp, free_bytes in samples:
            used_percent = round(((total - free_bytes) / total) * 100, 2)
            session.add(
                models.StorageSnapshot(
                    target_type="central",
                    target_id=target_id,
                    label="Laptop (all-in-one)",
                    total_bytes=total,
                    free_bytes=free_bytes,
                    used_percent=used_percent,
                    artifact_bytes=0,
                    spool_bytes=None,
                    pending_items=None,
                    source="acceptance_fixture",
                    context={"fixture": True},
                    timestamp_utc=timestamp,
                )
            )
        await session.commit()
    await engine.dispose()


async def _seed_sensor_storage_history(sensor_id: str) -> None:
    engine = create_engine(get_settings())
    factory = create_sessionmaker(engine)
    async with factory() as session:
        now = utc_now()
        total = 1_000_000_000
        samples = [
            (now - timedelta(minutes=3), 700_000_000),
            (now - timedelta(minutes=2), 690_000_000),
            (now - timedelta(minutes=1), 680_000_000),
        ]
        for index, (timestamp, free_bytes) in enumerate(samples):
            session.add(
                models.StorageSnapshot(
                    target_type="sensor",
                    target_id=sensor_id,
                    label=sensor_id,
                    total_bytes=total,
                    free_bytes=free_bytes,
                    used_percent=round(((total - free_bytes) / total) * 100, 2),
                    artifact_bytes=None,
                    spool_bytes=index * 1024,
                    pending_items=index,
                    source="acceptance_fixture",
                    context={"fixture": True},
                    timestamp_utc=timestamp,
                )
            )
        await session.commit()
    await engine.dispose()


def _assert_paged(payload: dict[str, Any], message: str) -> None:
    _assert(
        {"items", "count", "total", "limit", "offset"}.issubset(payload),
        f"{message}: paged envelope",
    )
    _assert(payload["count"] <= payload["limit"], f"{message}: bounded page size")


def main() -> None:
    token = secrets.token_urlsafe(24)
    api_port = _free_port()
    env = _base_env(api_port, token)
    os.environ.update(env)
    reset_settings_cache()

    demo_root = ROOT / ".data" / "demo"
    shutil.rmtree(demo_root, ignore_errors=True)
    demo_root.mkdir(parents=True, exist_ok=True)

    _run(
        [
            "docker",
            "compose",
            "-f",
            "deploy/docker-compose.infra.yml",
            "--project-name",
            "rf-sensor",
            "down",
            "-v",
            "--remove-orphans",
        ],
        env,
        timeout=120,
    )
    _run(
        [
            "docker",
            "compose",
            "-f",
            "deploy/docker-compose.infra.yml",
            "--project-name",
            "rf-sensor",
            "up",
            "-d",
            "--wait",
        ],
        env,
        timeout=180,
    )
    _run([sys.executable, "-m", "alembic", "upgrade", "head"], env, timeout=120)
    _run([sys.executable, "scripts/seed_demo.py"], env, timeout=60)

    api_proc = _start_api(env, api_port)
    try:
        _wait_ready(env["RF_PLATFORM_URL"])
        client = DashboardApiClient(get_settings())

        for _ in range(6):
            asyncio.run(_sensor_cycle(keep_spool=False))
            _run_worker_once(env)

        filtered_outputs = client.outputs(
            limit=2,
            offset=1,
            sensor_id=env["RF_SENSOR_ID"],
            profile_id="campus_general",
            technology="bluetooth-like",
            model_version="mock-v1",
            prompt_version="technology-detection-primary-v2",
            status="succeeded",
        )
        _assert_paged(filtered_outputs, "filtered RF-GPT outputs")
        _assert(filtered_outputs["total"] >= 3, "dashboard filters find seeded RF-GPT outputs")
        _assert(
            all(item["model"]["version"] == "mock-v1" for item in filtered_outputs["items"]),
            "mock model version is consistently mock-v1",
        )

        analysis_id = filtered_outputs["items"][0]["analysis_id"]
        detail_markdown = render_output_detail(client, analysis_id)
        _assert("### Structured findings" in detail_markdown, "RF-GPT detail is readable")
        _assert("mock-v1" in detail_markdown, "RF-GPT detail displays model version")
        _assert("not verified ground truth" in detail_markdown, "RF-GPT limitations are visible")
        _assert(
            "![Spectrogram preview]" in detail_markdown,
            "RF-GPT detail includes spectrogram preview",
        )

        open_alerts = client.alerts(status="open", limit=1)
        _assert_paged(open_alerts, "alert list")
        _assert(open_alerts["count"] == 1, "seeded alert is available")
        alert_id = open_alerts["items"][0]["alert_id"]
        for status in ["acknowledged", "dismissed", "confirmed"]:
            updated = client.update_alert(
                alert_id,
                status,
                actor="acceptance",
                comment=f"{status} during Milestone 2 acceptance",
            )
            _assert(updated["status"] == status, f"alert can be {status}")
        annotations = client._get("/api/v1/annotations", target_type="alert", target_id=alert_id)
        _assert(annotations["total"] >= 3, "alert operator mutations are annotated")
        audit_logs = client.logs(event_type="alert_status_updated")
        _assert(audit_logs["total"] >= 3, "alert operator mutations are audited")

        failed_job_id = asyncio.run(_seed_failed_job())
        failed_jobs = client.job_list(status="failed")
        _assert(
            any(
                item["job_id"] == failed_job_id and item["retry_eligible"]
                for item in failed_jobs["items"]
            ),
            "failed job appears as retry-eligible",
        )
        retry_result = client.retry_job(
            failed_job_id,
            actor="acceptance",
            comment="retry fixture during Milestone 2 acceptance",
        )
        _assert(retry_result["status"] == "pending", "retry control requeues eligible failed job")
        _run_worker_once(env)
        retried_jobs = client.job_list(status="succeeded")
        _assert(
            any(item["job_id"] == failed_job_id for item in retried_jobs["items"]),
            "retried job is processed successfully",
        )
        retry_logs = client.logs(event_type="job_retry_requested")
        _assert(retry_logs["total"] >= 1, "retry operator mutation is audited")

        storage = client.storage()
        central = storage["central"]
        asyncio.run(_seed_storage_trend(int(central["free_bytes"]), str(central["target_id"])))
        history = client.storage_history(
            target_type="central",
            target_id=str(central["target_id"]),
            limit=10,
        )
        trend = history["trends"][f"central:{central['target_id']}"]["trend"]
        _assert(history["count"] >= 3, "central storage history records samples")
        _assert(trend["status"] == "filling", "central storage trend detects filling history")
        _assert(
            trend["time_to_full_seconds"] is not None,
            "central storage time-to-full appears when enough data exists",
        )
        sensor_history = client.storage_history(target_type="sensor", target_id=env["RF_SENSOR_ID"])
        if sensor_history["count"] < 3:
            asyncio.run(_seed_sensor_storage_history(env["RF_SENSOR_ID"]))
            sensor_history = client.storage_history(
                target_type="sensor", target_id=env["RF_SENSOR_ID"]
            )
        _assert(sensor_history["count"] >= 3, "sensor storage history records samples")
        _assert(
            storage["warnings"] == [] or isinstance(storage["warnings"], list),
            "storage warnings are bounded",
        )

        metrics = client.metrics()
        _assert(metrics["sensors"]["total"] >= 1, "operational metrics include sensors")
        _assert(metrics["jobs"]["queue_depth"] >= 0, "operational metrics include job queue")
        _assert(metrics["model"]["model_version"] == "mock-v1", "metrics expose mock-v1 model")
        _assert(metrics["requests"]["count"] >= 1, "operational metrics include API request counts")

        old_artifact_id = asyncio.run(_seed_retention_fixture())
        report = client.retention_report(actor="acceptance")
        _assert(report["mode"] == "report-only", "retention is report-only")
        _assert(report["delete_enabled"] is False, "retention report does not enable deletion")
        _assert(
            any(item["target_id"] == old_artifact_id for item in report["items"]),
            "retention report identifies eligible artifact file",
        )
        _assert(
            all(item["would_delete"] is False for item in report["items"]),
            "retention report does not delete eligible data",
        )

        dashboard_pages = {
            "sensors": client.sensors(limit=3, offset=0),
            "jobs": client.job_list(limit=3, offset=0),
            "outputs": client.outputs(limit=3, offset=0),
            "logs": client.logs(limit=3, offset=0),
            "alerts": client.alerts(limit=3, offset=0),
        }
        for name, payload in dashboard_pages.items():
            _assert_paged(payload, f"dashboard API pagination for {name}")

        _run([sys.executable, "scripts/verify_backup_restore.py"], env, timeout=300)
        print("MILESTONE 2 ACCEPTANCE PASSED", flush=True)
    finally:
        _stop_process(api_proc)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
