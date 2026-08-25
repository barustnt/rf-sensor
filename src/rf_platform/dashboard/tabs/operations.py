from __future__ import annotations

from rf_platform.dashboard.api_client import DashboardApiClient


def render_metrics(client: DashboardApiClient) -> dict[str, object]:
    return client.metrics()


def run_retention_report(client: DashboardApiClient, actor: str) -> dict[str, object]:
    return client.retention_report(actor=actor or "operator")
