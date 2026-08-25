from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from rf_platform import __version__
from rf_platform.common.time import utc_now

_SECRET_KEYS = ("token", "password", "authorization", "database_url")


def _redact(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        if any(secret in key.lower() for secret in _SECRET_KEYS):
            event_dict[key] = "***redacted***"
    return event_dict


def _add_common(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict.setdefault("timestamp", utc_now().isoformat().replace("+00:00", "Z"))
    event_dict.setdefault("software_version", __version__)
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=getattr(logging, level.upper())
    )
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        _add_common,
        _redact,
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
