from __future__ import annotations

from rf_platform.dashboard.api_client import DashboardApiClient


def render_storage(client: DashboardApiClient) -> dict[str, object]:
    return client.storage()


def render_storage_history(
    client: DashboardApiClient,
    target_type: str | None = None,
    target_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    return client.storage_history(
        target_type=target_type, target_id=target_id, limit=limit, offset=offset
    )
