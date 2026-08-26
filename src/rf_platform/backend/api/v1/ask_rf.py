from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.dependencies import db_session
from rf_platform.backend.services.ask_rf import answer_ask_rf
from rf_platform.common.logging import get_logger
from rf_platform.common.time import resolve_historical_interval
from rf_platform.contracts.api import AskRFRequest, AskRFResponse, QueryInterval

router = APIRouter(prefix="/api/v1/ask-rf", tags=["ask-rf"])
logger = get_logger("rf_platform.ask_rf.api")


@router.post("/query", response_model=AskRFResponse)
async def ask_rf_query(
    payload: AskRFRequest,
    request: Request,
    session: AsyncSession = Depends(db_session),
) -> AskRFResponse:
    settings = request.app.state.settings
    try:
        return await answer_ask_rf(
            session,
            payload,
            default_timezone=settings.display_timezone,
        )
    except Exception as exc:  # pragma: no cover - dependency failures vary by deployment
        logger.warning(
            "ask_rf_query_unavailable",
            error=exc.__class__.__name__,
            message="Ask RF could not answer from stored data",
        )
        timezone = payload.display_timezone or settings.display_timezone
        interval = resolve_historical_interval(payload.question or "today", timezone)
        return AskRFResponse(
            answer_status="unavailable",
            display_answer=(
                "Ask RF is temporarily unavailable because the platform data service is not ready. "
                "Please try again after the system is live."
            ),
            interpreted_interval=QueryInterval(
                start_utc=interval.start_utc,
                end_utc=interval.end_utc,
                display_timezone=interval.display_timezone,
                assumptions=interval.assumptions,
            ),
            time_label="unavailable",
            location_label="monitored area",
            evidence_explanation="The platform API could not read stored observations.",
            limitations=["No technical exception details are shown in Ask RF."],
            follow_up_context={},
        )
