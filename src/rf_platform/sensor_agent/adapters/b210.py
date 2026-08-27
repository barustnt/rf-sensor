from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from rf_platform import __version__
from rf_platform.common.config import Settings
from rf_platform.common.ids import new_id
from rf_platform.common.logging import get_logger
from rf_platform.common.scan_profiles import (
    B210_MAX_FREQUENCY_HZ,
    B210_MAX_SAMPLE_RATE_SPS,
    B210_MIN_FREQUENCY_HZ,
    load_scan_profile_set,
)
from rf_platform.common.time import utc_now
from rf_platform.contracts.capture import (
    ArtifactDescriptor,
    CaptureEnvelope,
    CaptureProfile,
    DSPMetrics,
    PreprocessingSettings,
    RadioSettings,
)
from rf_platform.contracts.sensor import RadioHealth, SensorCapabilities
from rf_platform.preprocessing.atheer_hann import (
    IMG_H,
    IMG_W,
    NFFT,
    PIPELINE_ID,
    VMAX_DBFS,
    VMIN_DBFS,
    preprocess_iq,
)
from rf_platform.sensor_agent.adapters.base import CaptureBundle, CaptureRequest

logger = get_logger("rf_platform.sensor.b210")


class B210AdapterError(RuntimeError):
    """Base class for receive-only B210 sensor failures."""


class UhdUnavailableError(B210AdapterError):
    pass


class B210DeviceOpenError(B210AdapterError):
    pass


class B210NoMatchingDeviceError(B210DeviceOpenError):
    pass


class B210SerialMismatchError(B210AdapterError):
    pass


class B210ConfigurationError(B210AdapterError):
    pass


class B210ReceiveTimeoutError(B210AdapterError):
    pass


class B210MetadataError(B210AdapterError):
    pass


class B210OverflowError(B210MetadataError):
    pass


class B210IncompleteCaptureError(B210AdapterError):
    pass


class B210InvalidSamplesError(B210AdapterError):
    pass


class B210PreprocessingError(B210AdapterError):
    pass


@dataclass(frozen=True)
class B210RadioPlan:
    device_args: str
    expected_serial: str | None
    rx_channel: int
    antenna: str | None
    center_frequency_hz: int
    sample_rate_sps: int
    bandwidth_hz: int
    gain_db: float | None
    sample_count: int
    receive_timeout_seconds: float
    settling_seconds: float
    cpu_format: str
    wire_format: str
    max_recv_samples_per_chunk: int


@dataclass(frozen=True)
class B210ActualRadio:
    serial: str | None
    uhd_version: str | None
    rx_channel: int
    antenna: str | None
    center_frequency_hz: float
    sample_rate_sps: float
    bandwidth_hz: float
    gain_db: float | None


class B210UhdDevice(Protocol):
    def open(self, device_args: str) -> None: ...

    def close(self) -> None: ...

    def uhd_version(self) -> str | None: ...

    def serial(self, channel: int) -> str | None: ...

    def rx_channel_count(self) -> int: ...

    def configure_rx(self, plan: B210RadioPlan) -> B210ActualRadio: ...

    def create_rx_stream(self, cpu_format: str, wire_format: str, channel: int) -> Any: ...

    def start_finite_stream(self, streamer: Any, sample_count: int) -> None: ...

    def recv_into(self, streamer: Any, buffer: np.ndarray, timeout_seconds: float) -> int: ...


def load_uhd() -> Any:
    """Import UHD lazily so non-hardware runtimes do not need it installed."""
    try:
        import uhd  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised without UHD in tests
        raise UhdUnavailableError(
            "UHD Python bindings are unavailable; run B210 capture in the rf-b210 environment"
        ) from exc
    return uhd


class UhdB210Device:
    """Thin receive-only wrapper around UHD's Python API.

    This class intentionally exposes no transmit operations.
    """

    def __init__(self, uhd_module: Any | None = None) -> None:
        self._uhd = uhd_module
        self._usrp: Any | None = None

    def open(self, device_args: str) -> None:
        self._uhd = self._uhd or load_uhd()
        try:
            self._usrp = self._uhd.usrp.MultiUSRP(device_args)
        except Exception as exc:
            message = str(exc)
            if "LookupError" in exc.__class__.__name__ or "No devices" in message:
                raise B210NoMatchingDeviceError(f"no matching B210 device: {message}") from exc
            raise B210DeviceOpenError(f"failed to open B210: {message}") from exc

    def close(self) -> None:
        self._usrp = None

    def uhd_version(self) -> str | None:
        uhd = self._require_uhd()
        for attr in ("get_version_string", "get_version"):
            func = getattr(uhd, attr, None)
            if callable(func):
                try:
                    return str(func())
                except Exception:
                    pass
        libpyuhd = getattr(uhd, "libpyuhd", None)
        func = getattr(libpyuhd, "get_version_string", None)
        if callable(func):
            try:
                return str(func())
            except Exception:
                return None
        return None

    def serial(self, channel: int) -> str | None:
        usrp = self._require_usrp()
        try:
            info = usrp.get_usrp_rx_info(channel)
            if isinstance(info, dict):
                for key in ("mboard_serial", "serial", "rx_serial"):
                    if value := info.get(key):
                        return str(value)
        except Exception:
            pass
        try:
            sensor = usrp.get_mboard_sensor("serial")
            value = getattr(sensor, "value", None)
            if value:
                return str(value)
            to_pp_string = getattr(sensor, "to_pp_string", None)
            if callable(to_pp_string):
                return str(to_pp_string())
        except Exception:
            return None
        return None

    def rx_channel_count(self) -> int:
        usrp = self._require_usrp()
        getter = getattr(usrp, "get_rx_num_channels", None)
        if callable(getter):
            try:
                return int(getter())
            except Exception:
                return 1
        return 1

    def configure_rx(self, plan: B210RadioPlan) -> B210ActualRadio:
        usrp = self._require_usrp()
        uhd = self._require_uhd()
        try:
            usrp.set_rx_rate(float(plan.sample_rate_sps), plan.rx_channel)
            tune_request = uhd.types.TuneRequest(float(plan.center_frequency_hz))
            usrp.set_rx_freq(tune_request, plan.rx_channel)
            usrp.set_rx_bandwidth(float(plan.bandwidth_hz), plan.rx_channel)
            if plan.gain_db is not None:
                usrp.set_rx_gain(float(plan.gain_db), plan.rx_channel)
            if plan.antenna:
                usrp.set_rx_antenna(plan.antenna, plan.rx_channel)
            actual_antenna = plan.antenna
            get_antenna = getattr(usrp, "get_rx_antenna", None)
            if callable(get_antenna):
                actual_antenna = str(get_antenna(plan.rx_channel))
            return B210ActualRadio(
                serial=self.serial(plan.rx_channel),
                uhd_version=self.uhd_version(),
                rx_channel=plan.rx_channel,
                antenna=actual_antenna,
                center_frequency_hz=float(usrp.get_rx_freq(plan.rx_channel)),
                sample_rate_sps=float(usrp.get_rx_rate(plan.rx_channel)),
                bandwidth_hz=float(usrp.get_rx_bandwidth(plan.rx_channel)),
                gain_db=float(usrp.get_rx_gain(plan.rx_channel))
                if hasattr(usrp, "get_rx_gain")
                else plan.gain_db,
            )
        except Exception as exc:
            raise B210ConfigurationError(f"failed to configure B210 RX path: {exc}") from exc

    def create_rx_stream(self, cpu_format: str, wire_format: str, channel: int) -> Any:
        usrp = self._require_usrp()
        uhd = self._require_uhd()
        stream_args = uhd.usrp.StreamArgs(cpu_format, wire_format)
        stream_args.channels = [channel]
        return usrp.get_rx_stream(stream_args)

    def start_finite_stream(self, streamer: Any, sample_count: int) -> None:
        uhd = self._require_uhd()
        command = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
        command.num_samps = int(sample_count)
        command.stream_now = True
        streamer.issue_stream_cmd(command)

    def recv_into(self, streamer: Any, buffer: np.ndarray, timeout_seconds: float) -> int:
        uhd = self._require_uhd()
        metadata = uhd.types.RXMetadata()
        recv_buffer = buffer.reshape(1, -1)
        count = int(streamer.recv(recv_buffer, metadata, timeout_seconds))
        error_code = getattr(metadata, "error_code", None)
        error_name = str(error_code).lower()
        none_code = getattr(getattr(uhd.types, "RXMetadataErrorCode", object), "none", None)
        if none_code is not None and error_code == none_code:
            return count
        if "none" in error_name:
            return count
        if "timeout" in error_name:
            raise B210ReceiveTimeoutError("B210 receive timed out")
        if "overflow" in error_name:
            raise B210OverflowError("B210 RX overflow reported by UHD")
        raise B210MetadataError(f"UHD RX metadata error: {error_code}")

    def _require_uhd(self) -> Any:
        if self._uhd is None:
            self._uhd = load_uhd()
        return self._uhd

    def _require_usrp(self) -> Any:
        if self._usrp is None:
            raise B210DeviceOpenError("B210 device is not open")
        return self._usrp


class B210SensorAdapter:
    """Receive-only USRP B210 sensor adapter."""

    def __init__(
        self,
        settings: Settings,
        *,
        output_dir: Path | None = None,
        uhd_device: B210UhdDevice | None = None,
    ) -> None:
        self.settings = settings
        self.output_dir = (
            output_dir
            or settings.b210_capture_output_dir
            or (settings.spool_root / "b210-captures")
        )
        self._uhd = uhd_device or UhdB210Device()
        self.opened = False
        self.active_profile = settings.sensor_profile
        self.last_error: str | None = None
        self.last_actual_radio: B210ActualRadio | None = None
        self.last_capture_metadata: dict[str, Any] = {}

    async def open(self) -> None:
        if self.opened:
            return
        plan = self._plan_for_profile(None)
        try:
            self._uhd.open(plan.device_args)
            serial = self._uhd.serial(plan.rx_channel)
            if plan.expected_serial and serial != plan.expected_serial:
                raise B210SerialMismatchError(
                    f"configured serial {plan.expected_serial!r} does not match device {serial!r}"
                )
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.opened = True
            self.last_error = None
            logger.info(
                "b210_opened",
                sensor_id=self.settings.sensor_id,
                device_args=plan.device_args,
                expected_serial=plan.expected_serial,
                actual_serial=serial,
                rx_channel=plan.rx_channel,
            )
        except B210AdapterError as exc:
            self.last_error = str(exc)
            raise
        except Exception as exc:
            self.last_error = str(exc)
            raise B210DeviceOpenError(f"failed to open B210: {exc}") from exc

    async def capabilities(self) -> SensorCapabilities:
        rx_channels = self._uhd.rx_channel_count() if self.opened else 1
        profiles = [
            self.settings.sensor_profile,
            "b210_2g4_demo",
            "campus_general",
            "campus_2g4_coexistence",
            "exam_ble",
            "calibration",
            "device_experiment",
        ]
        try:
            scan_set = load_scan_profile_set(
                self.settings.scan_profile_config,
                expected_profile_set=self.settings.scan_profile_set,
            )
            profiles.extend(profile.profile_id for profile in scan_set.profiles)
        except Exception:
            pass
        supported_profiles = list(dict.fromkeys(profiles))
        return SensorCapabilities(
            frequency_min_hz=B210_MIN_FREQUENCY_HZ,
            frequency_max_hz=B210_MAX_FREQUENCY_HZ,
            maximum_sample_rate_sps=B210_MAX_SAMPLE_RATE_SPS,
            rx_channels=rx_channels,
            supported_profiles=supported_profiles,
        )

    async def apply_profile(self, profile: CaptureProfile) -> None:
        self.active_profile = profile.profile_id

    async def capture(self, request: CaptureRequest) -> CaptureBundle:
        await self.open()
        profile = request.profile
        plan = self._plan_for_profile(profile)
        capture_id = new_id()
        started = utc_now()
        logger.info(
            "b210_capture_start",
            sensor_id=self.settings.sensor_id,
            capture_id=capture_id,
            profile_id=profile.profile_id,
            center_frequency_hz=plan.center_frequency_hz,
            sample_rate_sps=plan.sample_rate_sps,
            bandwidth_hz=plan.bandwidth_hz,
            gain_db=plan.gain_db,
            sample_count=plan.sample_count,
            rx_channel=plan.rx_channel,
            antenna=plan.antenna,
        )
        try:
            actual = self._uhd.configure_rx(plan)
            self.last_actual_radio = actual
            logger.info(
                "b210_configured",
                sensor_id=self.settings.sensor_id,
                capture_id=capture_id,
                actual_center_frequency_hz=actual.center_frequency_hz,
                actual_sample_rate_sps=actual.sample_rate_sps,
                actual_bandwidth_hz=actual.bandwidth_hz,
                actual_gain_db=actual.gain_db,
                actual_antenna=actual.antenna,
            )
            if plan.settling_seconds:
                await asyncio.sleep(plan.settling_seconds)
            stream = self._uhd.create_rx_stream(plan.cpu_format, plan.wire_format, plan.rx_channel)
            samples, received = self._receive_exact(stream, plan)
            ended = utc_now()
            self._validate_samples(samples, received, plan.sample_count)
            preprocessing = preprocess_iq(
                samples,
                sample_rate=int(round(actual.sample_rate_sps)),
                center_frequency_hz=int(round(actual.center_frequency_hz)),
                gain_db=actual.gain_db,
                band_prior=profile.profile_id,
            )
            artifact_path = self.output_dir / f"{capture_id}-spectrogram.png"
            artifact_path.write_bytes(preprocessing.png_bytes)
            if self.settings.b210_persist_raw_iq:
                (self.output_dir / f"{capture_id}-iq.c64").write_bytes(samples.tobytes())
            sha = hashlib.sha256(preprocessing.png_bytes).hexdigest()
            hardware = self._hardware_metadata(plan, actual, received, started, ended, samples)
            self.last_capture_metadata = hardware
            logger.info(
                "b210_preprocessing_completed",
                sensor_id=self.settings.sensor_id,
                capture_id=capture_id,
                preprocessing_pipeline=preprocessing.pipeline_id,
                artifact_path=str(artifact_path),
                artifact_sha256=sha,
            )
            envelope = CaptureEnvelope(
                capture_id=capture_id,
                sensor_id=self.settings.sensor_id,
                session_id=request.session_id,
                correlation_id=new_id(),
                profile_id=profile.profile_id,
                started_at_utc=started,
                ended_at_utc=ended,
                radio=self._radio_settings_from_actual(profile, plan, actual, hardware),
                preprocessing=self._preprocessing_settings(preprocessing.metadata),
                dsp_metrics=DSPMetrics(
                    noise_floor_db=preprocessing.measurements.get("noise_floor_dbfs"),
                    peak_power_db=preprocessing.measurements.get("peak_dbfs"),
                    occupied_bandwidth_hz=preprocessing.measurements.get("occ_bw_hz"),
                ),
                artifacts=[
                    ArtifactDescriptor(
                        kind="spectrogram",
                        filename="spectrogram.png",
                        mime_type="image/png",
                        size_bytes=len(preprocessing.png_bytes),
                        sha256=sha,
                    )
                ],
                created_at_utc=utc_now(),
            )
            self.last_error = None
            logger.info(
                "b210_capture_completed",
                sensor_id=self.settings.sensor_id,
                capture_id=capture_id,
                received_samples=received,
                requested_samples=plan.sample_count,
                duration_seconds=hardware["capture_duration_seconds"],
            )
            return CaptureBundle(envelope=envelope, artifact_path=artifact_path)
        except B210AdapterError as exc:
            self.last_error = str(exc)
            self.last_capture_metadata = self._failure_metadata(
                capture_id, profile.profile_id, started, exc
            )
            logger.error(
                "b210_capture_failed",
                sensor_id=self.settings.sensor_id,
                capture_id=capture_id,
                error=exc.__class__.__name__,
                message=str(exc),
            )
            raise
        except Exception as exc:
            wrapped = B210PreprocessingError(f"B210 capture/preprocessing failed: {exc}")
            self.last_error = str(wrapped)
            self.last_capture_metadata = self._failure_metadata(
                capture_id, profile.profile_id, started, wrapped
            )
            logger.error(
                "b210_capture_failed",
                sensor_id=self.settings.sensor_id,
                capture_id=capture_id,
                error=exc.__class__.__name__,
                message=str(exc),
            )
            raise wrapped from exc

    async def health(self) -> RadioHealth:
        return RadioHealth(
            connected=self.opened and self.last_error is None, last_error=self.last_error
        )

    async def close(self) -> None:
        self._uhd.close()
        self.opened = False

    def _receive_exact(self, stream: Any, plan: B210RadioPlan) -> tuple[np.ndarray, int]:
        samples = np.empty(plan.sample_count, dtype=np.complex64)
        received = 0
        self._uhd.start_finite_stream(stream, plan.sample_count)
        while received < plan.sample_count:
            chunk_size = min(plan.max_recv_samples_per_chunk, plan.sample_count - received)
            buffer = samples[received : received + chunk_size]
            count = self._uhd.recv_into(stream, buffer, plan.receive_timeout_seconds)
            if count == 0:
                raise B210ReceiveTimeoutError("B210 receive returned zero samples")
            if count < 0 or count > chunk_size:
                raise B210MetadataError(f"invalid UHD receive count {count}")
            received += count
        return samples, received

    def _validate_samples(self, samples: np.ndarray, received: int, requested: int) -> None:
        if received == 0:
            raise B210IncompleteCaptureError("B210 returned zero samples")
        if received != requested:
            raise B210IncompleteCaptureError(
                f"B210 returned {received} samples; requested {requested}"
            )
        if not np.isfinite(samples).all():
            raise B210InvalidSamplesError("B210 capture contains NaN or infinite samples")

    def _plan_for_profile(self, profile: CaptureProfile | None) -> B210RadioPlan:
        sample_rate = self.settings.b210_sample_rate_sps or (
            profile.radio.sample_rate_sps if profile else 1
        )
        sample_count = self.settings.b210_sample_count
        if sample_count is None and profile is not None:
            sample_count = max(1, int(sample_rate * (profile.capture.duration_ms / 1000.0)))
        if sample_count is None:
            sample_count = 1
        serial = self.settings.b210_serial or None
        device_args = self.settings.b210_device_args
        if not device_args and serial:
            device_args = f"serial={serial}"
        return B210RadioPlan(
            device_args=device_args,
            expected_serial=serial,
            rx_channel=self.settings.b210_rx_channel,
            antenna=self.settings.b210_antenna or (profile.radio.antenna if profile else None),
            center_frequency_hz=self.settings.b210_center_frequency_hz
            or (profile.radio.center_frequency_hz if profile else 1),
            sample_rate_sps=sample_rate,
            bandwidth_hz=self.settings.b210_bandwidth_hz
            or (profile.radio.bandwidth_hz if profile else 1),
            gain_db=self.settings.b210_gain_db
            if self.settings.b210_gain_db is not None
            else (profile.radio.gain_db if profile else None),
            sample_count=sample_count,
            receive_timeout_seconds=self.settings.b210_receive_timeout_seconds,
            settling_seconds=self.settings.b210_settling_seconds,
            cpu_format=self.settings.b210_cpu_format,
            wire_format=self.settings.b210_wire_format,
            max_recv_samples_per_chunk=self.settings.b210_max_recv_samples_per_chunk,
        )

    def _radio_settings_from_actual(
        self,
        profile: CaptureProfile,
        plan: B210RadioPlan,
        actual: B210ActualRadio,
        hardware: dict[str, Any],
    ) -> RadioSettings:
        return RadioSettings(
            center_frequency_hz=int(round(actual.center_frequency_hz)),
            sample_rate_sps=int(round(actual.sample_rate_sps)),
            bandwidth_hz=int(round(actual.bandwidth_hz)),
            gain_mode=profile.radio.gain_mode,
            gain_db=actual.gain_db,
            antenna=actual.antenna or plan.antenna,
            hardware=hardware,
        )

    def _preprocessing_settings(self, metadata: dict[str, Any]) -> PreprocessingSettings:
        return PreprocessingSettings(
            pipeline_version=PIPELINE_ID,
            fft_size=NFFT,
            hop_size=NFFT,
            window="hann",
            db_min=VMIN_DBFS,
            db_max=VMAX_DBFS,
            image_width_px=IMG_W,
            image_height_px=IMG_H,
            color_map="viridis",
            include_axes=False,
            time_axis_direction="left-to-right",
            frequency_axis_direction="low-frequency-at-bottom",
            metadata=metadata,
        )

    def _hardware_metadata(
        self,
        plan: B210RadioPlan,
        actual: B210ActualRadio,
        received: int,
        started: Any,
        ended: Any,
        samples: np.ndarray,
    ) -> dict[str, Any]:
        duration_seconds = max(0.0, (ended - started).total_seconds())
        sample_abs = np.abs(samples)
        return {
            "adapter_type": "b210",
            "manufacturer": "Ettus Research",
            "device_type": "USRP B210",
            "serial": actual.serial,
            "uhd_version": actual.uhd_version,
            "rx_channel": plan.rx_channel,
            "antenna": actual.antenna or plan.antenna,
            "requested_center_frequency_hz": plan.center_frequency_hz,
            "actual_center_frequency_hz": actual.center_frequency_hz,
            "requested_sample_rate_sps": plan.sample_rate_sps,
            "actual_sample_rate_sps": actual.sample_rate_sps,
            "requested_bandwidth_hz": plan.bandwidth_hz,
            "actual_bandwidth_hz": actual.bandwidth_hz,
            "requested_gain_db": plan.gain_db,
            "actual_gain_db": actual.gain_db,
            "requested_sample_count": plan.sample_count,
            "received_sample_count": received,
            "capture_duration_seconds": duration_seconds,
            "cpu_sample_format": plan.cpu_format,
            "wire_sample_format": plan.wire_format,
            "preprocessing_pipeline_id": PIPELINE_ID,
            "raw_iq_persistence_enabled": self.settings.b210_persist_raw_iq,
            "sample_stats": {
                "mean_real": float(np.mean(samples.real)),
                "mean_imag": float(np.mean(samples.imag)),
                "rms": float(np.sqrt(np.mean(sample_abs**2))),
                "max_abs": float(np.max(sample_abs)),
            },
        }

    def _failure_metadata(
        self,
        capture_id: str,
        profile_id: str,
        started: Any,
        exc: Exception,
    ) -> dict[str, Any]:
        return {
            "adapter_type": "b210",
            "sensor_id": self.settings.sensor_id,
            "capture_id": capture_id,
            "profile_id": profile_id,
            "started_at_utc": started.isoformat()
            if hasattr(started, "isoformat")
            else str(started),
            "error": {
                "class": exc.__class__.__name__,
                "message": str(exc),
            },
        }


def software_version() -> str:
    return __version__
