from __future__ import annotations

from rf_platform.dashboard.api_client import DashboardApiClient


def render_alerts(
    client: DashboardApiClient,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, object]]:
    return client.alerts(limit=limit, offset=offset, status=status).get("items", [])


def update_alert_status(
    client: DashboardApiClient,
    alert_id: str,
    status: str,
    actor: str,
    comment: str,
) -> dict[str, object]:
    return client.update_alert(alert_id, status, actor=actor or "operator", comment=comment)
