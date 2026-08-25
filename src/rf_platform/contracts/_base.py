from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from rf_platform.common.time import ensure_utc


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class VersionedContract(ContractModel):
    schema_version: str = "1.0"


class UtcDatetimeMixin(BaseModel):
    @field_validator("*", mode="after", check_fields=False)
    @classmethod
    def _validate_utc_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return ensure_utc(value)
        return value
