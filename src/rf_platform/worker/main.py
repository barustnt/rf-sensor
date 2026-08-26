from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rf_platform.backend.db.session import create_engine, create_sessionmaker
from rf_platform.backend.services.artifacts import FilesystemArtifactStore
from rf_platform.common.broker import NatsEventBus
from rf_platform.common.config import get_settings
from rf_platform.common.logging import configure_logging, get_logger
from rf_platform.worker.consumer import WorkerProcessor, decode_message
from rf_platform.worker.router import create_adapter


async def check_database_ready(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        await session.execute(text("SELECT 1"))


async def run_worker(once: bool = False, idle_timeout_seconds: float = 30.0) -> int:
    configure_logging()
    settings = get_settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    store = FilesystemArtifactStore(settings)
    adapter = create_adapter(settings)
    log = get_logger("rf_platform.worker")
    bus: NatsEventBus | None = None
    processed = 0
    try:
        try:
            await check_database_ready(sessionmaker)
        except Exception as exc:
            log.error(
                "worker_database_readiness_failed",
                error=exc.__class__.__name__,
                message=str(exc),
            )
            raise RuntimeError(
                f"database readiness check failed: {exc.__class__.__name__}"
            ) from exc
        bus = NatsEventBus(settings)
        processor = WorkerProcessor(settings, sessionmaker, store, bus, adapter)
        await bus.connect()
        idle_start = asyncio.get_running_loop().time()
        while True:
            messages = await bus.pull_messages("rf-worker-v1", batch=1, timeout_seconds=1.0)
            if not messages:
                if once and processed > 0:
                    break
                if once and asyncio.get_running_loop().time() - idle_start >= idle_timeout_seconds:
                    break
                continue
            idle_start = asyncio.get_running_loop().time()
            for msg in messages:
                payload = decode_message(msg.data)
                outcome = await processor.process_payload(payload)
                if outcome in {"succeeded", "duplicate", "missing", "failed"}:
                    await msg.ack()
                processed += 1
                log.info("worker_message_processed", outcome=outcome, job_id=payload.get("job_id"))
            if once:
                break
    finally:
        if bus is not None:
            with suppress(Exception):
                await bus.close()
        await engine.dispose()
    return processed


def cli() -> None:
    parser = argparse.ArgumentParser(description="Run RF platform worker")
    parser.add_argument("--once", action="store_true", help="Process at most one message and exit")
    parser.add_argument("--idle-timeout", type=float, default=30.0)
    args = parser.parse_args()
    asyncio.run(run_worker(once=args.once, idle_timeout_seconds=args.idle_timeout))


if __name__ == "__main__":
    cli()
