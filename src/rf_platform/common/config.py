from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: str = "development"
    timezone: str = "Asia/Dubai"

    platform_url: str = Field(default="http://localhost:8000")
    api_host: str = "localhost"
    api_port: int = 8000
    dashboard_host: str = "localhost"
    dashboard_port: int = 7860
    gradio_share: bool = False
    cors_origins: str = ""

    database_url: str = "postgresql+asyncpg://rf_platform@localhost:5432/rf_platform"
    nats_url: str = "nats://localhost:4222"
    artifact_backend: str = "filesystem"
    artifact_root: Path = Path(".data/artifacts")
    max_upload_bytes: int = 10 * 1024 * 1024

    sensor_id: str = ""
    sensor_token: SecretStr | None = None
    sensor_display_name: str = "RF Sensor"
    sensor_location: str = "unknown"
    sensor_adapter: str = "simulated"
    sensor_profile: str = "campus_general"
    heartbeat_interval_seconds: int = 10
    offline_after_seconds: int = 30
    spool_root: Path = Path(".data/spool")
    spool_max_bytes: int = 10_737_418_240
    simulated_fixture_path: Path | None = None
    capture_interval_seconds: float = 0.0
    sensor_retry_initial_seconds: float = 1.0
    sensor_retry_max_seconds: float = 30.0

    b210_device_args: str = ""
    b210_serial: str = ""
    b210_rx_channel: int = 0
    b210_antenna: str | None = None
    b210_center_frequency_hz: int | None = None
    b210_sample_rate_sps: int | None = None
    b210_bandwidth_hz: int | None = None
    b210_gain_db: float | None = None
    b210_sample_count: int | None = None
    b210_receive_timeout_seconds: float = 5.0
    b210_settling_seconds: float = 0.1
    b210_cpu_format: str = "fc32"
    b210_wire_format: str = "sc16"
    b210_capture_output_dir: Path | None = None
    b210_persist_raw_iq: bool = False
    b210_max_recv_samples_per_chunk: int = 65_536

    retention_report_only: bool = True
    retention_heartbeat_days: int = 30
    retention_capture_days: int = 180
    retention_artifact_days: int = 30
    retention_log_days: int = 14
    storage_warning_used_percent: float = 85.0
    storage_critical_used_percent: float = 95.0

    rfgpt_adapter: str = "mock"
    rfgpt_model_name: str = "rfgpt"
    rfgpt_model_version: str = "mock-v1"
    rfgpt_endpoint: str = Field(default="http://localhost:8090")
    rfgpt_conda_env: str = ""
    rfgpt_request_timeout_seconds: int = 120
    rfgpt_health_timeout_seconds: float = 2.0
    rfgpt_temperature: float = 0.0
    rfgpt_top_p: float = 1.0
    rfgpt_repetition_penalty: float = 1.05
    rfgpt_max_output_tokens: int = 224
    worker_max_attempts: int = 5
    worker_concurrency: int = 1

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @field_validator("artifact_backend")
    @classmethod
    def validate_artifact_backend(cls, value: str) -> str:
        if value != "filesystem":
            raise ValueError("Milestone 1 supports only filesystem artifact storage")
        return value

    @field_validator("sensor_adapter")
    @classmethod
    def validate_sensor_adapter(cls, value: str) -> str:
        allowed = {"simulated", "b210"}
        if value not in allowed:
            raise ValueError(f"unsupported sensor adapter: {value}")
        return value

    @field_validator("rfgpt_adapter")
    @classmethod
    def validate_rfgpt_adapter(cls, value: str) -> str:
        allowed = {"mock", "local", "vllm"}
        if value not in allowed:
            raise ValueError(f"unsupported RF-GPT adapter: {value}")
        return value

    @field_validator("worker_concurrency")
    @classmethod
    def validate_worker_concurrency(cls, value: int) -> int:
        if value < 1:
            raise ValueError("worker concurrency must be at least 1")
        if value != 1:
            raise ValueError("worker concurrency is limited to 1 for the current milestones")
        return value

    @field_validator("rfgpt_temperature", "rfgpt_top_p", "rfgpt_repetition_penalty")
    @classmethod
    def validate_non_negative_float(cls, value: float) -> float:
        if value < 0:
            raise ValueError("RF-GPT inference parameters must be non-negative")
        return value

    @field_validator(
        "capture_interval_seconds",
        "sensor_retry_initial_seconds",
        "sensor_retry_max_seconds",
        "b210_receive_timeout_seconds",
        "b210_settling_seconds",
    )
    @classmethod
    def validate_non_negative_seconds(cls, value: float) -> float:
        if value < 0:
            raise ValueError("duration settings must be non-negative")
        return value

    @field_validator("b210_gain_db")
    @classmethod
    def validate_optional_non_negative_float(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("B210 gain must be non-negative")
        return value

    @field_validator("b210_rx_channel")
    @classmethod
    def validate_b210_rx_channel(cls, value: int) -> int:
        if value < 0:
            raise ValueError("B210 RX channel must be non-negative")
        return value

    @field_validator(
        "b210_center_frequency_hz",
        "b210_sample_rate_sps",
        "b210_bandwidth_hz",
        "b210_sample_count",
        "b210_max_recv_samples_per_chunk",
    )
    @classmethod
    def validate_optional_positive_int(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("B210 integer settings must be positive")
        return value

    @field_validator("rfgpt_max_output_tokens")
    @classmethod
    def validate_max_output_tokens(cls, value: int) -> int:
        if value < 1:
            raise ValueError("RF-GPT maximum output tokens must be positive")
        return value

    def require_sensor_token(self) -> SecretStr:
        if self.sensor_token is None or not self.sensor_token.get_secret_value():
            raise RuntimeError("RF_SENSOR_TOKEN must be set for sensor-authenticated operations")
        return self.sensor_token

    def redacted(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        for key in list(data):
            lowered = key.lower()
            if "token" in lowered or "password" in lowered or lowered == "database_url":
                data[key] = "***redacted***"
        return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
