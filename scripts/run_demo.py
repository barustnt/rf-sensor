from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from acceptance_infra import cleanup_isolated_infra, start_isolated_infra  # noqa: E402
from rf_platform.common.config import get_settings, reset_settings_cache  # noqa: E402
from rf_platform.dashboard.api_client import DashboardApiClient  # noqa: E402
from rf_platform.sensor_agent.service import SensorService  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("", 0))
        return int(sock.getsockname()[1])


def _base_env(api_port: int, token: str) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{SRC}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(SRC)
    )
    env.update(
        {
            "RF_ENV": "development",
            "RF_TIMEZONE": "Asia/Dubai",
            "RF_PLATFORM_URL": f"http://localhost:{api_port}",
            "RF_API_HOST": "localhost",
            "RF_API_PORT": str(api_port),
            "RF_DASHBOARD_HOST": "localhost",
            "RF_DASHBOARD_PORT": "7860",
            "RF_GRADIO_SHARE": "false",
            "RF_DATABASE_URL": "postgresql+asyncpg://rf_platform:change-me@127.0.0.1:5432/rf_platform",
            "RF_NATS_URL": "nats://127.0.0.1:4222",
            "RF_POSTGRES_USER": "rf_platform",
            "RF_POSTGRES_PASSWORD": "change-me",
            "RF_POSTGRES_DB": "rf_platform",
            "RF_ARTIFACT_BACKEND": "filesystem",
            "RF_ARTIFACT_ROOT": str(ROOT / ".data" / "demo" / "artifacts"),
            "RF_SPOOL_ROOT": str(ROOT / ".data" / "demo" / "spool"),
            "RF_SPOOL_MAX_BYTES": "1073741824",
            "RF_SENSOR_ID": "sim-sensor-001",
            "RF_SENSOR_TOKEN": token,
            "RF_SENSOR_DISPLAY_NAME": "Simulated RF Sensor",
            "RF_SENSOR_LOCATION": "demo-room",
            "RF_SENSOR_ADAPTER": "simulated",
            "RF_SENSOR_PROFILE": "campus_general",
            "RF_HEARTBEAT_INTERVAL_SECONDS": "2",
            "RF_OFFLINE_AFTER_SECONDS": "1",
            "RF_RFGPT_ADAPTER": "mock",
            "RF_RFGPT_MODEL_NAME": "rfgpt",
            "RF_RFGPT_MODEL_VERSION": "mock-v1",
            "RF_WORKER_MAX_ATTEMPTS": "5",
            "RF_WORKER_CONCURRENCY": "1",
            "RF_SIMULATED_FIXTURE_PATH": str(
                ROOT / "tests" / "fixtures" / "spectrograms" / "simulated_ble_like.png"
            ),
        }
    )
    return env


def _run(cmd: list[str], env: dict[str, str], timeout: int = 120) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, env=env, check=True, timeout=timeout)


def _start_api(env: dict[str, str], api_port: int) -> subprocess.Popen[str]:
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "rf_platform.backend.main:create_app",
            "--factory",
            "--host",
            "localhost",
            "--port",
            str(api_port),
        ],
        cwd=ROOT,
        env=env,
        text=True,
    )
    return proc


def _stop_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    with suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)


def _wait_ready(base_url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/health/ready", timeout=2.0)
            last = response.text
            if response.status_code == 200 and response.json().get("status") == "ok":
                return
        except httpx.HTTPError as exc:
            last = f"{exc.__class__.__name__}: {exc}"
        time.sleep(1)
    raise RuntimeError(f"API did not become ready: {last}")


def _api_get(base_url: str, path: str) -> dict[str, Any]:
    response = httpx.get(f"{base_url}{path}", timeout=10.0)
    response.raise_for_status()
    return response.json()


async def _sensor_cycle(keep_spool: bool = False) -> dict[str, Any]:
    reset_settings_cache()
    service = SensorService(get_settings())
    return await service.run_once(keep_spool_after_upload=keep_spool)


async def _capture_spool_with_api_down() -> str:
    reset_settings_cache()
    service = SensorService(get_settings())
    item = await service.try_capture_when_api_down()
    with suppress(Exception):
        await service.upload_pending(delete_after_success=False)
    return item.envelope.capture_id


async def _upload_pending() -> list[dict[str, object]]:
    reset_settings_cache()
    service = SensorService(get_settings())
    await service.register()
    return await service.upload_pending(delete_after_success=True)


def _run_worker_once(env: dict[str, str]) -> None:
    _run(
        [sys.executable, "-m", "rf_platform.worker.main", "--once", "--idle-timeout", "8"],
        env,
        timeout=60,
    )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}", flush=True)


def main() -> None:
    token = secrets.token_urlsafe(24)
    api_port = _free_port()
    env = _base_env(api_port, token)
    demo_root = ROOT / ".data" / "demo"
    shutil.rmtree(demo_root, ignore_errors=True)
    demo_root.mkdir(parents=True, exist_ok=True)

    infra = start_isolated_infra(env)
    api_proc: subprocess.Popen[str] | None = None
    try:
        os.environ.update(env)
        reset_settings_cache()
        _run([sys.executable, "-m", "alembic", "upgrade", "head"], env, timeout=120)
        _run([sys.executable, "scripts/seed_demo.py"], env, timeout=60)

        api_proc = _start_api(env, api_port)
        _wait_ready(env["RF_PLATFORM_URL"])

        first = asyncio.run(_sensor_cycle(keep_spool=True))
        first_capture_id = str(first["capture_id"])
        sensors = _api_get(env["RF_PLATFORM_URL"], "/api/v1/sensors")
        _assert(sensors["count"] >= 1, "1. simulated sensor registers")
        heartbeats = _api_get(
            env["RF_PLATFORM_URL"], f"/api/v1/sensors/{env['RF_SENSOR_ID']}/heartbeats"
        )
        _assert(
            heartbeats["items"] and "disk" in heartbeats["items"][0],
            "2. heartbeats expose device storage",
        )
        capture = _api_get(env["RF_PLATFORM_URL"], f"/api/v1/captures/{first_capture_id}")
        _assert(capture["artifacts"], "3. fixture spectrogram is spooled and uploaded")
        jobs_before = _api_get(env["RF_PLATFORM_URL"], "/api/v1/jobs/summary")
        _assert(jobs_before["pending"] >= 1, "4. durable analysis job is created")

        _run_worker_once(env)
        analyses = _api_get(env["RF_PLATFORM_URL"], "/api/v1/analyses")
        _assert(analyses["count"] >= 1, "5-6. mock worker stores valid RF-GPT-like result")
        events = _api_get(env["RF_PLATFORM_URL"], "/api/v1/events")
        _assert(events["count"] >= 1, "7. configured rule creates an event")

        client = DashboardApiClient(get_settings())
        dashboard_seen = {
            "sensors": client.sensors(),
            "storage": client.storage(),
            "jobs": client.jobs(),
            "outputs": client.outputs(),
            "logs": client.logs(),
        }
        _assert(
            dashboard_seen["sensors"]["count"] >= 1
            and dashboard_seen["outputs"]["count"] >= 1
            and dashboard_seen["logs"]["count"] >= 1,
            "8. dashboard API client sees sensor, storage, jobs, output, and logs",
        )
        query = client.ask("What happened today?", "Asia/Dubai")
        _assert(
            query["evidence"] and query["summary"]["analysis_count"] >= 1,
            "9. historical query returns evidence",
        )

        # Duplicate upload from retained spool must not create another analysis job.
        reset_settings_cache()
        dup_service = SensorService(get_settings())
        duplicate_result = asyncio.run(dup_service.upload_pending(delete_after_success=True))
        _assert(
            duplicate_result and duplicate_result[0]["ingestion_status"] == "duplicate",
            "10. duplicate capture upload is idempotent",
        )
        analyses_after_dup = _api_get(env["RF_PLATFORM_URL"], "/api/v1/analyses")
        _assert(
            analyses_after_dup["count"] == analyses["count"],
            "10. duplicate upload creates no duplicate analysis",
        )

        # Worker stopped: upload while no worker is running, then process after restart.
        second = asyncio.run(_sensor_cycle(keep_spool=False))
        second_capture_id = str(second["capture_id"])
        jobs_queued = _api_get(env["RF_PLATFORM_URL"], "/api/v1/jobs/summary")
        _assert(
            jobs_queued["pending"] >= 1,
            "worker stopped acceptance: uploaded capture waits in durable queue",
        )
        _run_worker_once(env)
        second_analyses = _api_get(
            env["RF_PLATFORM_URL"], f"/api/v1/analyses?capture_id={second_capture_id}"
        )
        _assert(second_analyses["count"] == 1, "worker restart acceptance: queued job is processed")

        # API outage: stop API, capture/spool locally, restart API, upload successfully.
        _stop_process(api_proc)
        down_capture_id = asyncio.run(_capture_spool_with_api_down())
        api_proc = _start_api(env, api_port)
        _wait_ready(env["RF_PLATFORM_URL"])
        uploaded = asyncio.run(_upload_pending())
        _assert(
            any(item["capture_id"] == down_capture_id for item in uploaded),
            "API outage acceptance: spooled capture uploads after restart",
        )
        _run_worker_once(env)

        time.sleep(2.0)
        offline = _api_get(env["RF_PLATFORM_URL"], f"/api/v1/sensors/{env['RF_SENSOR_ID']}")
        _assert(
            offline["operational_status"] == "offline", "offline sensor is visible after threshold"
        )

        final_dashboard = DashboardApiClient(get_settings()).overview()
        _assert(
            final_dashboard["health"]["status"] == "ok",
            "dashboard overview sees healthy backend dependencies",
        )
        print("SIMULATED END-TO-END ACCEPTANCE PASSED", flush=True)
    finally:
        if api_proc is not None:
            _stop_process(api_proc)
        cleanup_isolated_infra(infra.project_name, env)


if __name__ == "__main__":
    main()
