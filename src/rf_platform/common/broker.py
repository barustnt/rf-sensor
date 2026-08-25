from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import nats
from nats.aio.client import Client as NATS
from nats.aio.msg import Msg
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, StorageType

from rf_platform.common.config import Settings
from rf_platform.common.logging import get_logger

STREAM_NAME = "RF"
STREAM_SUBJECTS = ["rf.>"]
ANALYSIS_REQUESTED = "rf.analysis.requested.v1"
ANALYSIS_COMPLETED = "rf.analysis.completed.v1"
EVENT_CREATED = "rf.event.created.v1"
ALERT_CREATED = "rf.alert.created.v1"
SENSOR_HEALTH = "rf.sensor.health.v1"
DEADLETTER = "rf.deadletter.v1"


class NatsEventBus:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.nc: NATS | None = None
        self.js: JetStreamContext | None = None
        self.log = get_logger("rf_platform.nats")

    async def connect(self) -> None:
        if self.nc and self.nc.is_connected:
            return
        self.nc = await nats.connect(str(self.settings.nats_url), name="rf-platform")
        self.js = self.nc.jetstream()
        await self.ensure_stream()

    async def ensure_stream(self) -> None:
        if self.js is None:
            raise RuntimeError("NATS is not connected")
        try:
            await self.js.stream_info(STREAM_NAME)
        except Exception:
            await self.js.add_stream(
                name=STREAM_NAME,
                subjects=STREAM_SUBJECTS,
                storage=StorageType.FILE,
                retention="limits",
            )

    async def close(self) -> None:
        if self.nc and not self.nc.is_closed:
            await self.nc.drain()

    async def publish(self, subject: str, payload: dict[str, Any]) -> None:
        await self.connect()
        if self.js is None:
            raise RuntimeError("NATS JetStream is not connected")
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        await self.js.publish(subject, data)

    async def health(self) -> dict[str, Any]:
        try:
            await self.connect()
            if self.js is None:
                return {"status": "unavailable"}
            info = await self.js.stream_info(STREAM_NAME)
            return {
                "status": "ok",
                "stream": info.config.name,
                "subjects": list(info.config.subjects or []),
            }
        except Exception as exc:  # pragma: no cover - exact NATS failures vary by environment
            return {"status": "error", "error": exc.__class__.__name__}

    async def pull_messages(
        self,
        durable: str,
        batch: int = 1,
        timeout_seconds: float = 1.0,
    ) -> Sequence[Msg]:
        await self.connect()
        if self.js is None:
            raise RuntimeError("NATS JetStream is not connected")
        try:
            sub = await self.js.pull_subscribe(
                ANALYSIS_REQUESTED,
                durable=durable,
                stream=STREAM_NAME,
                config=ConsumerConfig(
                    durable_name=durable,
                    ack_policy=AckPolicy.EXPLICIT,
                    deliver_policy=DeliverPolicy.ALL,
                    filter_subject=ANALYSIS_REQUESTED,
                ),
            )
        except Exception:
            sub = await self.js.pull_subscribe(
                ANALYSIS_REQUESTED, durable=durable, stream=STREAM_NAME
            )
        try:
            return await sub.fetch(batch=batch, timeout=timeout_seconds)
        except NatsTimeoutError:
            return []
