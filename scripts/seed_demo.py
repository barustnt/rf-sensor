from __future__ import annotations

import asyncio
from pathlib import Path

import yaml
from sqlalchemy import select

from rf_platform.backend.db import models
from rf_platform.backend.db.session import create_engine, create_sessionmaker
from rf_platform.common.config import get_settings
from rf_platform.common.ids import new_id
from rf_platform.common.time import utc_now
from rf_platform.contracts.capture import CaptureProfile


async def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_sessionmaker(engine)
    async with factory() as session:
        for path in sorted(Path("config/profiles").glob("*.yml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            profile = CaptureProfile.model_validate(data)
            existing = (
                await session.execute(
                    select(models.CaptureProfileRow).where(
                        models.CaptureProfileRow.profile_id == profile.profile_id,
                        models.CaptureProfileRow.version == profile.schema_version,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    models.CaptureProfileRow(
                        row_id=new_id(),
                        profile_id=profile.profile_id,
                        version=profile.schema_version,
                        definition=profile.model_dump(mode="json"),
                        active=profile.enabled,
                        created_at_utc=utc_now(),
                        updated_at_utc=utc_now(),
                    )
                )
            else:
                existing.definition = profile.model_dump(mode="json")
                existing.active = profile.enabled
                existing.updated_at_utc = utc_now()
        session.add(
            models.SystemEvent(
                severity="info",
                service="seed",
                event_type="profiles_seeded",
                message="Capture profiles seeded",
                sensor_id=None,
                correlation_id=None,
                context={},
                timestamp_utc=utc_now(),
            )
        )
        await session.commit()
    await engine.dispose()


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
