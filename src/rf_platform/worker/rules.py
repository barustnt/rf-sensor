from __future__ import annotations

RULE_ID = "technology_observation_v1"
RULE_VERSION = "1.0"


def should_create_event(labels: list[str]) -> bool:
    return bool(labels)
