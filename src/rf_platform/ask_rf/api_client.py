from __future__ import annotations

from typing import Any

import httpx

from rf_platform.common.config import Settings
from rf_platform.contracts.api import AskRFResponse


class AskRFApiClient:
    """Server-side API client for Ask RF; no browser-visible secrets are used."""

    def __init__(self, settings: Settings) -> None:
        self.base_url = str(settings.platform_url).rstrip("/")
        self.timeout_seconds = settings.api_timeout_seconds
        self.display_timezone = settings.display_timezone

    def ready(self) -> bool:
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(f"{self.base_url}/health/ready")
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError:
            return False
        return payload.get("status") == "ok"

    def query(self, question: str, prior_context: dict[str, Any] | None = None) -> AskRFResponse:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/api/v1/ask-rf/query",
                json={
                    "question": question,
                    "display_timezone": self.display_timezone,
                    "prior_context": prior_context,
                },
            )
            response.raise_for_status()
            return AskRFResponse.model_validate(response.json())
