from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from rf_platform.contracts._base import UtcDatetimeMixin, VersionedContract


class SensorLocation(VersionedContract):
    site: str = "campus"
    building: str = "unknown"
    room: str = "lab"
    coordinates: dict[str, float] | None = None


class SensorCapabilities(VersionedContract):
    frequency_min_hz: int | None = None
    frequency_max_hz: int | None = None
    maximum_sample_rate_sps: int | None = None
    rx_channels: int = Field(default=1, ge=1)
    supported_profiles: list[str] = Field(default_factory=lambda: ["campus_general"])


class SensorRegistration(UtcDatetimeMixin, VersionedContract):
    sensor_id: str
    display_name: str
    node_type: Literal["edge_sensor"] = "edge_sensor"
    adapter: str
    location: SensorLocation
    groups: list[str] = Field(default_factory=list)
    capabilities: SensorCapabilities
    software_version: str
    hostname: str
    registered_at_utc: datetime


class DiskStatus(VersionedContract):
    total_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)
    used_percent: float = Field(ge=0, le=100)


class SpoolStatus(UtcDatetimeMixin, VersionedContract):
    pending_items: int = Field(ge=0)
    pending_bytes: int = Field(ge=0)
    oldest_item_utc: datetime | None = None


class SystemStatus(VersionedContract):
    cpu_percent: float = Field(ge=0, le=100)
    memory_percent: float = Field(ge=0, le=100)
    process_uptime_seconds: float = Field(ge=0)


class RadioHealth(VersionedContract):
    connected: bool
    last_error: str | None = None


class SensorHeartbeat(UtcDatetimeMixin, VersionedContract):
    sensor_id: str
    sequence: int = Field(ge=0)
    timestamp_utc: datetime
    status: Literal["online", "degraded"] = "online"
    active_profile: str
    disk: DiskStatus
    spool: SpoolStatus
    system: SystemStatus
    radio: RadioHealth
    last_capture_utc: datetime | None = None
    clock_offset_ms: float | None = None


class DesiredState(VersionedContract):
    sensor_id: str
    desired_profile: str
    config_version: int = Field(ge=1)
