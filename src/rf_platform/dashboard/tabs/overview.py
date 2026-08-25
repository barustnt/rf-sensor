from __future__ import annotations

from rf_platform.dashboard.api_client import DashboardApiClient


def render_overview(client: DashboardApiClient) -> str:
    data = client.overview()
    sensors = data["sensors"].get("items", [])
    online = sum(1 for item in sensors if item.get("operational_status") == "online")
    offline = sum(1 for item in sensors if item.get("operational_status") == "offline")
    jobs = data["jobs"]
    return (
        f"Sensors: total={len(sensors)} online={online} offline={offline}\n"
        f"Jobs: pending={jobs.get('pending', 0)} running={jobs.get('running', 0)} "
        f"failed={jobs.get('failed', 0)} deadletter={jobs.get('deadletter', 0)}\n"
        f"Recent events={data['events'].get('count', 0)} alerts={data['alerts'].get('count', 0)}\n"
        f"Health={data['health'].get('status')}"
    )
