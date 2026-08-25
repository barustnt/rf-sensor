from __future__ import annotations

from rf_platform.dashboard.api_client import DashboardApiClient


def ask_rf(client: DashboardApiClient, question: str, timezone: str) -> dict[str, object]:
    return client.ask(question, timezone)
