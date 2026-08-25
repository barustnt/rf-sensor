from __future__ import annotations

from rf_platform.dashboard.api_client import DashboardApiClient


def render_sensors(
    client: DashboardApiClient,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, object]]:
    return client.sensors(limit=limit, offset=offset, status=status).get("items", [])
