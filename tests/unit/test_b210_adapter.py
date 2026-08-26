from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import respx
from PIL import Image

from rf_platform.common.config import Settings
from rf_platform.preprocessing import atheer_hann
from rf_platform.sensor_agent.adapters.b210 import (
    B210ActualRadio,
    B210ConfigurationError,
    B210DeviceOpenError,
    B210IncompleteCaptureError,
    B210InvalidSamplesError,
    B210MetadataError,
    B210NoMatchingDeviceError,
    B210OverflowError,
    B210PreprocessingError,
    B210ReceiveTimeoutError,
    B210SensorAdapter,
    B210SerialMismatchError,
    B210UhdDevice,
    UhdB210Device,
    UhdUnavailableError,
    load_uhd,
)
from rf_platform.sensor_agent.adapters.base import CaptureRequest
from rf_platform.sensor_agent.profiles import load_profile
from rf_platform.sensor_agent.service import SensorService, create_sensor_adapter
from rf_platform.sensor_agent.spool import DurableSpool
from rf_platform.sensor_agent.upload import UploadError


def _samples(count: int, *, invalid: bool = False) -> np.ndarray:
    t = np.arange(count, dtype=np.float32)
    data = (0.01 * np.exp(2j * np.pi * t / 128)).astype(np.complex64)
    if invalid:
        data[3] = np.nan + 0j
    return data


class FakeB210Device(B210UhdDevice):
    def __init__(
        self,
        samples: np.ndarray,
        *,
        serial: str = "321D88A",
        uhd_version: str = "4.9.0.HEAD-release",
        error: Exception | None = None,
        chunk_sizes: list[int] | None = None,
        actual_offset_hz: float = 12.0,
        open_error: Exception | None = None,
        config_error: Exception | None = None,
        zero_first_recv: bool = False,
    ) -> None:
        self.samples = samples
        self.serial_value = serial
        self.version = uhd_version
        self.error = error
        self.open_error = open_error
        self.config_error = config_error
        self.zero_first_recv = zero_first_recv
        self.chunk_sizes = list(chunk_sizes or [])
        self.actual_offset_hz = actual_offset_hz
        self.open_args: str | None = None
        self.closed = False
        self.plan: Any = None
        self.stream_args: tuple[str, str, int] | None = None
        self.started_sample_count: int | None = None
        self.offset = 0
        self.recv_calls = 0

    def open(self, device_args: str) -> None:
        if self.open_error is not None:
            raise self.open_error
        self.open_args = device_args

    def close(self) -> None:
        self.closed = True

    def uhd_version(self) -> str | None:
        return self.version

    def serial(self, channel: int) -> str | None:
        assert channel == 0
        return self.serial_value

    def rx_channel_count(self) -> int:
        return 2

    def configure_rx(self, plan: Any) -> B210ActualRadio:
        if self.config_error is not None:
            raise self.config_error
        self.plan = plan
        return B210ActualRadio(
            serial=self.serial_value,
            uhd_version=self.version,
            rx_channel=plan.rx_channel,
            antenna=plan.antenna,
            center_frequency_hz=float(plan.center_frequency_hz) + self.actual_offset_hz,
            sample_rate_sps=float(plan.sample_rate_sps),
            bandwidth_hz=float(plan.bandwidth_hz),
            gain_db=plan.gain_db,
        )

    def create_rx_stream(self, cpu_format: str, wire_format: str, channel: int) -> object:
        self.stream_args = (cpu_format, wire_format, channel)
        return object()

    def start_finite_stream(self, streamer: object, sample_count: int) -> None:
        self.started_sample_count = sample_count

    def recv_into(self, streamer: object, buffer: np.ndarray, timeout_seconds: float) -> int:
        self.recv_calls += 1
        if self.zero_first_recv:
            self.zero_first_recv = False
            return 0
        if self.error is not None:
            raise self.error
        remaining = len(self.samples) - self.offset
        if remaining <= 0:
            raise B210IncompleteCaptureError("fake stream ended before requested sample count")
        requested = len(buffer)
        limited = self.chunk_sizes.pop(0) if self.chunk_sizes else requested
        count = min(requested, limited, remaining)
        buffer[:count] = self.samples[self.offset : self.offset + count]
        self.offset += count
        return count


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    data: dict[str, Any] = {
        "sensor_id": "laptop-b210-001",
        "sensor_token": "token",
        "sensor_adapter": "b210",
        "sensor_profile": "b210_2g4_demo",
        "platform_url": "http://api.local",
        "spool_root": tmp_path / "spool",
        "b210_device_args": "serial=321D88A",
        "b210_serial": "321D88A",
        "b210_rx_channel": 0,
        "b210_antenna": "RX2",
        "b210_center_frequency_hz": 2_440_000_000,
        "b210_sample_rate_sps": 20_000_000,
        "b210_bandwidth_hz": 20_000_000,
        "b210_gain_db": 30.0,
        "b210_sample_count": atheer_hann.NFFT * atheer_hann.TIME_ROWS,
        "b210_receive_timeout_seconds": 1.0,
        "b210_settling_seconds": 0.0,
        "b210_cpu_format": "fc32",
        "b210_wire_format": "sc16",
        "b210_max_recv_samples_per_chunk": 65_536,
        "b210_capture_output_dir": tmp_path / "captures",
    }
    data.update(overrides)
    return Settings(**data)


def _profile() -> Any:
    return load_profile("b210_2g4_demo")


def test_adapter_selection_and_lazy_uhd_import(tmp_path: Path) -> None:
    sys.modules.pop("uhd", None)
    b210 = create_sensor_adapter(_settings(tmp_path))
    simulated = create_sensor_adapter(
        Settings(sensor_id="sim", sensor_token="token", sensor_adapter="simulated")
    )
    assert isinstance(b210, B210SensorAdapter)
    assert simulated.__class__.__name__ == "SimulatedSensorAdapter"
    assert "uhd" not in sys.modules


def test_uhd_unavailable_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "uhd", None)
    with pytest.raises(UhdUnavailableError):
        load_uhd()


@pytest.mark.asyncio
async def test_configuration_mapping_exact_sample_collection_and_metadata(tmp_path: Path) -> None:
    count = atheer_hann.NFFT * atheer_hann.TIME_ROWS
    fake = FakeB210Device(
        _samples(count),
        chunk_sizes=[10_000, 20_000, 50_000, count],
    )
    adapter = B210SensorAdapter(_settings(tmp_path), uhd_device=fake)

    bundle = await adapter.capture(CaptureRequest(profile=_profile()))

    assert fake.open_args == "serial=321D88A"
    assert fake.plan.center_frequency_hz == 2_440_000_000
    assert fake.plan.sample_rate_sps == 20_000_000
    assert fake.plan.bandwidth_hz == 20_000_000
    assert fake.plan.gain_db == 30.0
    assert fake.plan.sample_count == count
    assert fake.started_sample_count == count
    assert fake.recv_calls > 1
    assert fake.stream_args == ("fc32", "sc16", 0)

    metadata = bundle.envelope.radio.hardware
    assert metadata["adapter_type"] == "b210"
    assert metadata["manufacturer"] == "Ettus Research"
    assert metadata["device_type"] == "USRP B210"
    assert metadata["serial"] == "321D88A"
    assert metadata["uhd_version"] == "4.9.0.HEAD-release"
    assert metadata["requested_center_frequency_hz"] == 2_440_000_000
    assert metadata["actual_center_frequency_hz"] == 2_440_000_012.0
    assert metadata["requested_sample_count"] == count
    assert metadata["received_sample_count"] == count
    assert metadata["cpu_sample_format"] == "fc32"
    assert metadata["wire_sample_format"] == "sc16"
    assert metadata["raw_iq_persistence_enabled"] is False
    assert metadata["sample_stats"]["max_abs"] > 0
    assert bundle.envelope.preprocessing.pipeline_version == "atheer-hann-v1"
    assert bundle.envelope.preprocessing.window == "hann"
    assert bundle.envelope.preprocessing.metadata["pipeline_id"] == "atheer-hann-v1"
    Image.open(bundle.artifact_path).verify()


@pytest.mark.asyncio
async def test_receive_timeout_metadata_error_overflow_and_incomplete(tmp_path: Path) -> None:
    cases = [
        B210ReceiveTimeoutError("timeout"),
        B210MetadataError("metadata error"),
        B210OverflowError("overflow"),
        B210IncompleteCaptureError("short read"),
    ]
    for error in cases:
        fake = FakeB210Device(_samples(1024), error=error)
        adapter = B210SensorAdapter(_settings(tmp_path), uhd_device=fake)
        with pytest.raises(error.__class__):
            await adapter.capture(CaptureRequest(profile=_profile()))
        health = await adapter.health()
        assert health.connected is False
        assert error.__class__.__name__ in str(health.last_error) or str(error) in str(
            health.last_error
        )


@pytest.mark.asyncio
async def test_open_serial_and_configuration_failures_are_explicit(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    no_device = B210SensorAdapter(
        settings,
        uhd_device=FakeB210Device(
            _samples(1024),
            open_error=B210NoMatchingDeviceError("no matching device"),
        ),
    )
    with pytest.raises(B210NoMatchingDeviceError):
        await no_device.open()

    open_failure = B210SensorAdapter(
        settings,
        uhd_device=FakeB210Device(
            _samples(1024),
            open_error=B210DeviceOpenError("open failed"),
        ),
    )
    with pytest.raises(B210DeviceOpenError):
        await open_failure.open()

    mismatch = B210SensorAdapter(
        settings, uhd_device=FakeB210Device(_samples(1024), serial="other")
    )
    with pytest.raises(B210SerialMismatchError):
        await mismatch.open()

    config_failure = B210SensorAdapter(
        settings,
        uhd_device=FakeB210Device(
            _samples(atheer_hann.NFFT * atheer_hann.TIME_ROWS),
            config_error=B210ConfigurationError("tune failed"),
        ),
    )
    with pytest.raises(B210ConfigurationError):
        await config_failure.capture(CaptureRequest(profile=_profile()))


@pytest.mark.asyncio
async def test_zero_samples_are_rejected(tmp_path: Path) -> None:
    fake = FakeB210Device(_samples(atheer_hann.NFFT * atheer_hann.TIME_ROWS), zero_first_recv=True)
    adapter = B210SensorAdapter(_settings(tmp_path), uhd_device=fake)
    with pytest.raises(B210ReceiveTimeoutError, match="zero samples"):
        await adapter.capture(CaptureRequest(profile=_profile()))


@pytest.mark.asyncio
async def test_incomplete_sample_count_from_stream_is_rejected(tmp_path: Path) -> None:
    fake = FakeB210Device(_samples(1024))
    adapter = B210SensorAdapter(_settings(tmp_path), uhd_device=fake)
    with pytest.raises(B210IncompleteCaptureError):
        await adapter.capture(CaptureRequest(profile=_profile()))


@pytest.mark.asyncio
async def test_invalid_numeric_samples_are_rejected(tmp_path: Path) -> None:
    count = atheer_hann.NFFT * atheer_hann.TIME_ROWS
    fake = FakeB210Device(_samples(count, invalid=True))
    adapter = B210SensorAdapter(_settings(tmp_path), uhd_device=fake)
    with pytest.raises(B210InvalidSamplesError) as exc:
        await adapter.capture(CaptureRequest(profile=_profile()))
    assert "NaN" in str(exc.value) or "infinite" in str(exc.value)


@pytest.mark.asyncio
async def test_preprocessing_failure_is_classified(tmp_path: Path) -> None:
    fake = FakeB210Device(_samples(1024))
    adapter = B210SensorAdapter(_settings(tmp_path, b210_sample_count=1024), uhd_device=fake)
    with pytest.raises(B210PreprocessingError):
        await adapter.capture(CaptureRequest(profile=_profile()))


def test_uhd_wrapper_exposes_no_tx_path() -> None:
    source = inspect.getsource(UhdB210Device)
    assert "set_tx" not in source
    assert "get_tx" not in source
    assert "tx_stream" not in source


@pytest.mark.asyncio
@respx.mock
async def test_spool_upload_and_restart_recovery_use_b210_capture(tmp_path: Path) -> None:
    count = atheer_hann.NFFT * atheer_hann.TIME_ROWS
    fake = FakeB210Device(_samples(count))
    settings = _settings(tmp_path)
    service = SensorService(settings, adapter=B210SensorAdapter(settings, uhd_device=fake))

    item = await service.capture_to_spool("b210_2g4_demo")
    recovered = DurableSpool(settings.spool_root, settings.spool_max_bytes).pending_items()
    assert [pending.envelope.capture_id for pending in recovered] == [item.envelope.capture_id]

    route = respx.post("http://api.local/api/v1/captures").mock(
        side_effect=[
            httpx.Response(503, text="api temporarily unavailable"),
            httpx.Response(
                202,
                json={
                    "schema_version": "1.0",
                    "capture_id": item.envelope.capture_id,
                    "ingestion_status": "accepted",
                    "job_id": "job-1",
                },
            ),
        ]
    )
    with pytest.raises(UploadError):
        await service.upload_pending()
    assert DurableSpool(settings.spool_root, settings.spool_max_bytes).pending_items()

    uploads = await service.upload_pending()
    assert uploads[0]["capture_id"] == item.envelope.capture_id
    assert route.called
    assert DurableSpool(settings.spool_root, settings.spool_max_bytes).pending_items() == []
