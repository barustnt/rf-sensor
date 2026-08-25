from __future__ import annotations

import json
from pathlib import Path

import httpx

from rf_platform.common.config import Settings
from rf_platform.sensor_agent.spool import SpoolItem


class UploadError(RuntimeError):
    pass


async def upload_item(settings: Settings, item: SpoolItem) -> dict[str, object]:
    token = settings.require_sensor_token().get_secret_value()
    url = f"{str(settings.platform_url).rstrip('/')}/api/v1/captures"
    metadata = item.envelope.model_dump(mode="json")
    data = {"metadata": json.dumps(metadata, separators=(",", ":"))}
    with Path(item.artifact_path).open("rb") as handle:
        files = [("artifacts", ("spectrogram.png", handle, "image/png"))]
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url, data=data, files=files, headers={"X-Sensor-Token": token}
            )
    if response.status_code not in {202}:
        raise UploadError(f"upload failed: {response.status_code} {response.text}")
    return response.json()
