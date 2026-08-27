from __future__ import annotations

import argparse
import asyncio
import json
import sys

from rf_platform.common.config import get_settings
from rf_platform.common.logging import configure_logging, get_logger
from rf_platform.sensor_agent.scanner import B210ScanRunner, dry_run_plan
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
    retry_delay = settings.sensor_retry_initial_seconds
    while True:
        logger = get_logger("rf_platform.sensor")
        try:
            await service.send_heartbeat()
        except Exception as exc:
            logger.warning("heartbeat_failed", error=exc.__class__.__name__, message=str(exc))
        profile_id = settings.sensor_profile
        try:
            profile_id = await service.poll_desired_state()
        except Exception as exc:
            logger.warning(
                "desired_state_poll_failed",
                error=exc.__class__.__name__,
                message=str(exc),
                fallback_profile=profile_id,
            )
        try:
            item = await service.capture_to_spool(profile_id)
            logger.info("capture_spooled", capture_id=item.envelope.capture_id)
            retry_delay = settings.sensor_retry_initial_seconds
        except Exception as exc:
            logger.error("capture_failed", error=exc.__class__.__name__, message=str(exc))
            await asyncio.sleep(retry_delay)
            retry_delay = min(settings.sensor_retry_max_seconds, max(retry_delay * 2, 1.0))
        try:
            uploads = await service.upload_pending()
            logger.info("upload_pending_completed", count=len(uploads))
        except Exception as exc:  # keep spooling if API is temporarily unavailable
            logger.warning("upload_failed", error=exc.__class__.__name__, message=str(exc))
        sleep_seconds = settings.capture_interval_seconds or settings.heartbeat_interval_seconds
        await asyncio.sleep(sleep_seconds)


async def run_b210_scan(
    *, one_cycle: bool = False, max_slices: int | None = None
) -> dict[str, object]:
    configure_logging()
    runner = B210ScanRunner(get_settings())
    runner.install_signal_handlers()
    return await runner.run(one_cycle=one_cycle, max_slices=max_slices)


def cli() -> None:
    parser = argparse.ArgumentParser(description="Run RF sensor agent")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--keep-spool-after-upload", action="store_true")
    parser.add_argument(
        "--scan-plan", action="store_true", help="Print a dry-run scan plan and exit"
    )
    parser.add_argument("--scan", action="store_true", help="Run sequential B210 scan cycles")
    parser.add_argument("--scan-one-cycle", action="store_true", help="Run one complete scan cycle")
    parser.add_argument("--scan-max-slices", type=int, default=None)
    parser.add_argument(
        "--scan-plan-verbose",
        action="store_true",
        help="Include the full scan catalogue in dry-run output",
    )
    args = parser.parse_args()
    try:
        if args.scan_plan:
            configure_logging()
            print(
                json.dumps(
                    dry_run_plan(
                        get_settings(),
                        max_slices=args.scan_max_slices,
                        verbose=args.scan_plan_verbose,
                    ),
                    indent=2,
                )
            )
        elif args.scan:
            asyncio.run(
                run_b210_scan(one_cycle=args.scan_one_cycle, max_slices=args.scan_max_slices)
            )
        elif args.once:
            asyncio.run(run_once(keep_spool_after_upload=args.keep_spool_after_upload))
        else:
            asyncio.run(run_forever())
    except Exception as exc:
        configure_logging()
        get_logger("rf_platform.sensor").error(
            "sensor_command_failed", error=exc.__class__.__name__, message=str(exc)
        )
        print(f"rf-sensor failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    cli()
