from __future__ import annotations

from rf_platform.dashboard.api_client import DashboardApiClient


def render_logs(
    client: DashboardApiClient,
    severity: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, object]]:
    return client.logs(limit=limit, offset=offset, severity=severity).get("items", [])
