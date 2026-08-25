from __future__ import annotations

from rf_platform.dashboard.api_client import DashboardApiClient


def render_jobs(client: DashboardApiClient) -> dict[str, object]:
    return client.jobs()


def render_job_list(
    client: DashboardApiClient,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, object]]:
    return client.job_list(status=status, limit=limit, offset=offset).get("items", [])


def retry_job(
    client: DashboardApiClient, job_id: str, actor: str, comment: str
) -> dict[str, object]:
    return client.retry_job(job_id, actor=actor or "operator", comment=comment)
