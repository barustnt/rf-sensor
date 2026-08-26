from __future__ import annotations

import json
import socket
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import cast

import httpx

from rf_platform import __version__
from rf_platform.common.config import Settings
from rf_platform.common.time import utc_now
from rf_platform.contracts.sensor import (
    SensorHeartbeat,
    SensorLocation,
    SensorRegistration,
    SpoolStatus,
)
from rf_platform.sensor_agent.adapters.b210 import B210SensorAdapter
from rf_platform.sensor_agent.adapters.base import CaptureRequest, SensorAdapter
from rf_platform.sensor_agent.adapters.simulated import SimulatedSensorAdapter
from rf_platform.sensor_agent.health import disk_status, system_status
from rf_platform.sensor_agent.profiles import load_profile, validate_profile_against_capabilities
from rf_platform.sensor_agent.spool import DurableSpool, SpoolItem
from rf_platform.sensor_agent.upload import UploadError, upload_item


class SensorService:
    def __init__(self, settings: Settings, adapter: SensorAdapter | None = None) -> None:
        if not settings.sensor_id:
            raise RuntimeError("RF_SENSOR_ID must be set for the sensor agent")
        self.settings = settings
        self.spool = DurableSpool(settings.spool_root, settings.spool_max_bytes)
        self.adapter = adapter or create_sensor_adapter(settings)
        self.state_path = settings.spool_root / "sensor-state.json"
        self.sequence = self._load_sequence()
        self.last_capture_utc: datetime | None = None

    def _load_sequence(self) -> int:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            return int(payload.get("last_heartbeat_sequence", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return 0

    def _save_sequence(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({"last_heartbeat_sequence": self.sequence}, sort_keys=True),
            encoding="utf-8",
        )

    async def register(self) -> dict[str, object]:
        token = self.settings.require_sensor_token().get_secret_value()
        await self.adapter.open()
        capabilities = await self.adapter.capabilities()
        registration = SensorRegistration(
            sensor_id=self.settings.sensor_id,
            display_name=self.settings.sensor_display_name,
            adapter=self.settings.sensor_adapter,
            location=SensorLocation(room=self.settings.sensor_location),
            groups=["campus", self.settings.sensor_adapter],
            capabilities=capabilities,
            software_version=__version__,
            hostname=socket.gethostname(),
            registered_at_utc=utc_now(),
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{str(self.settings.platform_url).rstrip('/')}/api/v1/sensors/register",
                json=registration.model_dump(mode="json"),
                headers={"X-Sensor-Token": token},
            )
        response.raise_for_status()
        return response.json()

    async def send_heartbeat(self) -> dict[str, object]:
        token = self.settings.require_sensor_token().get_secret_value()
        self.sequence += 1
        health = await self.adapter.health()
        spool_status = self.spool.status()
        heartbeat = SensorHeartbeat(
            sensor_id=self.settings.sensor_id,
            sequence=self.sequence,
            timestamp_utc=utc_now(),
            status="online" if health.connected else "degraded",
            active_profile=self.settings.sensor_profile,
            disk=disk_status(Path(self.settings.spool_root)),
            spool=SpoolStatus.model_validate({"schema_version": "1.0", **spool_status}),
            system=system_status(),
            radio=health,
            last_capture_utc=self.last_capture_utc,
            clock_offset_ms=None,
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{str(self.settings.platform_url).rstrip('/')}/api/v1/sensors/{self.settings.sensor_id}/heartbeat",
                json=heartbeat.model_dump(mode="json"),
                headers={"X-Sensor-Token": token},
            )
        response.raise_for_status()
        self._save_sequence()
        return response.json()

    async def poll_desired_state(self) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{str(self.settings.platform_url).rstrip('/')}/api/v1/sensors/{self.settings.sensor_id}/desired-state"
            )
        response.raise_for_status()
        payload = response.json()
        return str(payload["desired_profile"])

    async def capture_to_spool(self, profile_id: str | None = None) -> SpoolItem:
        await self.adapter.open()
        capabilities = await self.adapter.capabilities()
        profile = load_profile(profile_id or self.settings.sensor_profile)
        validate_profile_against_capabilities(profile, capabilities)
        await self.adapter.apply_profile(profile)
        bundle = await self.adapter.capture(CaptureRequest(profile=profile))
        self.last_capture_utc = bundle.envelope.ended_at_utc
        return self.spool.put(bundle)

    async def upload_pending(self, delete_after_success: bool = True) -> list[dict[str, object]]:
        results = []
        for item in self.spool.pending_items():
            result = await upload_item(self.settings, item)
            results.append(result)
            if result.get("capture_id") == item.envelope.capture_id and delete_after_success:
                self.spool.delete(item)
        return results

    async def run_once(self, keep_spool_after_upload: bool = False) -> dict[str, object]:
        await self.register()
        await self.send_heartbeat()
        desired = await self.poll_desired_state()
        item = await self.capture_to_spool(desired)
        upload_results = await self.upload_pending(delete_after_success=not keep_spool_after_upload)
        await self.send_heartbeat()
        return {
            "capture_id": item.envelope.capture_id,
            "spool_item": self.spool.export_item(item),
            "uploads": upload_results,
        }

    async def try_capture_when_api_down(self) -> SpoolItem:
        return await self.capture_to_spool(self.settings.sensor_profile)


async def try_upload_pending(settings: Settings) -> list[dict[str, object]]:
    service = SensorService(settings)
    with suppress(httpx.HTTPError, RuntimeError):
        await service.register()
    try:
        return await service.upload_pending()
    except UploadError:
        return []


def create_sensor_adapter(settings: Settings) -> SensorAdapter:
    if settings.sensor_adapter == "simulated":
        return cast(SensorAdapter, SimulatedSensorAdapter(settings))
    if settings.sensor_adapter == "b210":
        return cast(SensorAdapter, B210SensorAdapter(settings))
    raise RuntimeError(f"unsupported sensor adapter: {settings.sensor_adapter}")
