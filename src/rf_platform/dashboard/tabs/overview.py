from __future__ import annotations

from rf_platform.dashboard.api_client import DashboardApiClient


def render_overview(client: DashboardApiClient) -> str:
    data = client.overview()
    metrics = data.get("metrics", {})
    sensors_metric = metrics.get("sensors", {})
    jobs = data["jobs"]
    storage = data["storage"]
    warnings = storage.get("warnings", [])
    warning_text = (
        "\n".join(f"- {w.get('severity')}: {w.get('message')}" for w in warnings) or "None"
    )
    model = metrics.get("model", {})
    model_health = model.get("health", {})
    gpu = metrics.get("gpu", {})
    gpu_text = (
        f"{gpu.get('name')} free={gpu.get('memory_free_mib')}MiB/"
        f"{gpu.get('memory_total_mib')}MiB temp={gpu.get('temperature_c')}C"
        if gpu.get("available")
        else "unavailable"
    )
    return (
        f"Sensors: total={sensors_metric.get('total', 0)} "
        f"online={sensors_metric.get('online', 0)} degraded={sensors_metric.get('degraded', 0)} "
        f"offline={sensors_metric.get('offline', 0)} stale={sensors_metric.get('stale', 0)}\n"
        f"Jobs: pending={jobs.get('pending', 0)} running={jobs.get('running', 0)} "
        f"failed={jobs.get('failed', 0)} deadletter={jobs.get('deadletter', 0)} "
        f"p50={jobs.get('latency_ms_p50')}ms p95={jobs.get('latency_ms_p95')}ms\n"
        f"RF-GPT: {model.get('model_name')} version={model.get('model_version')} "
        f"adapter={model.get('adapter')} ready={model_health.get('ready')} "
        f"latency_p95={model.get('latency_ms_p95')}ms\n"
        f"GPU/VLM: {gpu_text}\n"
        f"Health={data['health'].get('status')}\n"
        f"Storage trend={storage.get('central_trend', {}).get('status')} "
        f"time_to_full={storage.get('central_trend', {}).get('time_to_full_seconds')}\n"
        f"Warnings:\n{warning_text}"
    )
