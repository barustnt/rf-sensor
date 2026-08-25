from __future__ import annotations

from rf_platform.dashboard.api_client import DashboardApiClient


def render_jobs(client: DashboardApiClient) -> dict[str, object]:
    return client.jobs()
