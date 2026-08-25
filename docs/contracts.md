# Contracts

All public payloads include `schema_version`. IDs are JSON strings. Timestamps are UTC-aware and
stored in UTC. Presentation and historical query parsing may convert to `Asia/Dubai` or another
configured display time zone.

The Pydantic contracts live under `src/rf_platform/contracts` and are the source for API payload
validation.
