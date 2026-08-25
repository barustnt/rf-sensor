from __future__ import annotations

import argparse
import asyncio

from rf_platform.backend.db.session import create_engine, create_sessionmaker
from rf_platform.backend.services.artifacts import FilesystemArtifactStore
from rf_platform.common.broker import NatsEventBus
from rf_platform.common.config import get_settings
from rf_platform.common.logging import configure_logging, get_logger
from rf_platform.worker.consumer import WorkerProcessor, decode_message
from rf_platform.worker.router import create_adapter


async def run_worker(once: bool = False, idle_timeout_seconds: float = 30.0) -> int:
    configure_logging()
    settings = get_settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    bus = NatsEventBus(settings)
    store = FilesystemArtifactStore(settings)
    adapter = create_adapter(settings)
    processor = WorkerProcessor(settings, sessionmaker, store, bus, adapter)
    log = get_logger("rf_platform.worker")
    await bus.connect()
    processed = 0
    idle_start = asyncio.get_running_loop().time()
    try:
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
