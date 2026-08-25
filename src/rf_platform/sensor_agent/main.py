from __future__ import annotations

import argparse
import asyncio

from rf_platform.common.config import get_settings
from rf_platform.common.logging import configure_logging, get_logger
from rf_platform.sensor_agent.service import SensorService


async def run_once(keep_spool_after_upload: bool = False) -> dict[str, object]:
    configure_logging()
    service = SensorService(get_settings())
    result = await service.run_once(keep_spool_after_upload=keep_spool_after_upload)
    get_logger("rf_platform.sensor").info("sensor_once_completed", capture_id=result["capture_id"])
    return result


async def run_forever() -> None:
    settings = get_settings()
    service = SensorService(settings)
    await service.register()
    while True:
        await service.send_heartbeat()
        await service.capture_to_spool(settings.sensor_profile)
        try:
            await service.upload_pending()
        except Exception as exc:  # keep spooling if API is temporarily unavailable
            get_logger("rf_platform.sensor").warning("upload_failed", error=exc.__class__.__name__)
        await asyncio.sleep(settings.heartbeat_interval_seconds)


def cli() -> None:
    parser = argparse.ArgumentParser(description="Run RF sensor agent")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--keep-spool-after-upload", action="store_true")
    args = parser.parse_args()
    if args.once:
        asyncio.run(run_once(keep_spool_after_upload=args.keep_spool_after_upload))
    else:
        asyncio.run(run_forever())


if __name__ == "__main__":
    cli()
