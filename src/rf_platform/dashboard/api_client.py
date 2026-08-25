from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from rf_platform.common.config import Settings


def _query(params: dict[str, Any]) -> str:
    clean = {
        key: value
        for key, value in params.items()
        if value is not None and value != "" and value != []
    }
    return f"?{urlencode(clean, doseq=True)}" if clean else ""


class DashboardApiClient:
    """API-only dashboard client; it never accesses the database or sensors directly."""

    def __init__(self, settings: Settings) -> None:
        self.base_url = str(settings.platform_url).rstrip("/")

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{self.base_url}{path}{_query(params)}")
            response.raise_for_status()
            return response.json()

    def _post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(f"{self.base_url}{path}", json=payload or {})
            response.raise_for_status()
            return response.json()

    def _patch(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=20.0) as client:
            response = client.patch(f"{self.base_url}{path}", json=payload)
            response.raise_for_status()
            return response.json()

    def overview(self) -> dict[str, Any]:
        sensors = self.sensors(limit=100)
        jobs = self.jobs()
        storage = self.storage()
        events = self.events(limit=10)
        alerts = self.alerts(limit=10)
        ready = self._get("/health/ready")
        metrics = self.metrics()
        return {
            "sensors": sensors,
            "jobs": jobs,
            "storage": storage,
            "events": events,
            "alerts": alerts,
            "health": ready,
            "metrics": metrics,
        }

    def sensors(
        self, limit: int = 50, offset: int = 0, status: str | None = None
    ) -> dict[str, Any]:
        return self._get("/api/v1/sensors", limit=limit, offset=offset, status=status)

    def storage(self) -> dict[str, Any]:
        return self._get("/api/v1/platform/storage")

    def storage_history(
        self,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self._get(
            "/api/v1/platform/storage/history",
            target_type=target_type,
            target_id=target_id,
            limit=limit,
            offset=offset,
        )

    def jobs(self) -> dict[str, Any]:
        return self._get("/api/v1/jobs/summary")

    def job_list(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        return self._get("/api/v1/jobs", status=status, limit=limit, offset=offset)

    def retry_job(
        self, job_id: str, actor: str = "operator", comment: str | None = None
    ) -> dict[str, Any]:
        return self._post(
            f"/api/v1/analyses/jobs/{job_id}/retry", {"actor": actor, "comment": comment or ""}
        )

    def outputs(
        self,
        limit: int = 50,
        offset: int = 0,
        sensor_id: str | None = None,
        profile_id: str | None = None,
        location: str | None = None,
        technology: str | None = None,
        model_version: str | None = None,
        prompt_version: str | None = None,
        status: str | None = None,
        start_utc: str | None = None,
        end_utc: str | None = None,
    ) -> dict[str, Any]:
        return self._get(
            "/api/v1/analyses",
            limit=limit,
            offset=offset,
            sensor_id=sensor_id,
            profile_id=profile_id,
            location=location,
            technology=technology,
            model_version=model_version,
            prompt_version=prompt_version,
            status=status,
            start_utc=start_utc,
            end_utc=end_utc,
        )

    def output_detail(self, analysis_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/analyses/{analysis_id}")

    def logs(
        self,
        limit: int = 50,
        offset: int = 0,
        severity: str | None = None,
        service: str | None = None,
        sensor_id: str | None = None,
        event_type: str | None = None,
        correlation_id: str | None = None,
        start_utc: str | None = None,
        end_utc: str | None = None,
    ) -> dict[str, Any]:
        return self._get(
            "/api/v1/logs",
            limit=limit,
            offset=offset,
            severity=severity,
            service=service,
            sensor_id=sensor_id,
            event_type=event_type,
            correlation_id=correlation_id,
            start_utc=start_utc,
            end_utc=end_utc,
        )

    def events(self, limit: int = 50, offset: int = 0, status: str | None = None) -> dict[str, Any]:
        return self._get("/api/v1/events", limit=limit, offset=offset, status=status)

    def alerts(self, limit: int = 50, offset: int = 0, status: str | None = None) -> dict[str, Any]:
        return self._get("/api/v1/alerts", limit=limit, offset=offset, status=status)

    def update_alert(
        self, alert_id: str, status: str, actor: str = "operator", comment: str | None = None
    ) -> dict[str, Any]:
        return self._patch(
            f"/api/v1/alerts/{alert_id}",
            {"status": status, "actor": actor, "comment": comment or ""},
        )

    def annotate(
        self, target_type: str, target_id: str, label: str, actor: str, comment: str | None = None
    ) -> dict[str, Any]:
        return self._post(
            "/api/v1/annotations",
            {
                "target_type": target_type,
                "target_id": target_id,
                "label": label,
                "actor": actor,
                "comment": comment or "",
            },
        )

    def metrics(self) -> dict[str, Any]:
        return self._get("/api/v1/platform/metrics")

    def retention_report(self, actor: str = "operator") -> dict[str, Any]:
        return self._post("/api/v1/platform/retention/report", {"actor": actor})

    def ask(self, question: str, timezone: str) -> dict[str, Any]:
        return self._post("/api/v1/query", {"question": question, "timezone": timezone})
