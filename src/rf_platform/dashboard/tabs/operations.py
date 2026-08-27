from __future__ import annotations

from rf_platform.dashboard.api_client import DashboardApiClient


def render_metrics(client: DashboardApiClient) -> dict[str, object]:
    return client.metrics()


def render_scan_profiles(client: DashboardApiClient) -> dict[str, object]:
    return client.scan_profiles()


def render_coverage(
    client: DashboardApiClient,
    sensor_id: str | None = None,
    start_utc: str | None = None,
    end_utc: str | None = None,
) -> dict[str, object]:
    return client.coverage(sensor_id=sensor_id, start_utc=start_utc, end_utc=end_utc)


def run_retention_report(client: DashboardApiClient, actor: str) -> dict[str, object]:
    return client.retention_report(actor=actor or "operator")
