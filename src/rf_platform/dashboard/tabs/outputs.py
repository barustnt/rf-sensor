from __future__ import annotations

from rf_platform.dashboard.api_client import DashboardApiClient


def render_outputs(client: DashboardApiClient) -> list[dict[str, object]]:
    return client.outputs().get("items", [])
