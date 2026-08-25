from __future__ import annotations

from typing import Any

import httpx

from rf_platform.common.config import Settings


class DashboardApiClient:
    def __init__(self, settings: Settings) -> None:
        self.base_url = str(settings.platform_url).rstrip("/")

    def _get(self, path: str) -> dict[str, Any]:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{self.base_url}{path}")
            response.raise_for_status()
            return response.json()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(f"{self.base_url}{path}", json=payload)
            response.raise_for_status()
            return response.json()

    def overview(self) -> dict[str, Any]:
        sensors = self.sensors()
        jobs = self.jobs()
        storage = self.storage()
        events = self.events()
        alerts = self.alerts()
        ready = self._get("/health/ready")
        return {
            "sensors": sensors,
            "jobs": jobs,
            "storage": storage,
            "events": events,
            "alerts": alerts,
            "health": ready,
        }

    def sensors(self) -> dict[str, Any]:
        return self._get("/api/v1/sensors")

    def storage(self) -> dict[str, Any]:
        return self._get("/api/v1/platform/storage")

    def jobs(self) -> dict[str, Any]:
        return self._get("/api/v1/jobs/summary")

    def outputs(self) -> dict[str, Any]:
        return self._get("/api/v1/analyses")

    def logs(self) -> dict[str, Any]:
        return self._get("/api/v1/logs?limit=50")

    def events(self) -> dict[str, Any]:
        return self._get("/api/v1/events")

    def alerts(self) -> dict[str, Any]:
        return self._get("/api/v1/alerts")

    def ask(self, question: str, timezone: str) -> dict[str, Any]:
        return self._post("/api/v1/query", {"question": question, "timezone": timezone})
