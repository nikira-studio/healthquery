from __future__ import annotations

import json
from datetime import date
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth import require_read_auth
from services.reporting import (
    build_ask_response,
    build_doctor_visit_report,
    llm_is_configured,
    maybe_rewrite_with_llm,
)
from utils.sse import create_done_event, create_progress_event, create_token_event

router = APIRouter(prefix="/api/reports", tags=["reports"], dependencies=[Depends(require_read_auth)])


class DateRangeRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None


class DoctorVisitReportRequest(DateRangeRequest):
    stream: bool = Field(default=False)


class AskRequest(DateRangeRequest):
    question: str = Field(..., min_length=1)


@router.post("/doctor-visit", operation_id="post_doctor_visit_report")
async def post_doctor_visit_report(body: DoctorVisitReportRequest) -> Any:
    report = await build_doctor_visit_report(body.start_date, body.end_date)

    if body.stream and llm_is_configured():
        async def event_stream() -> AsyncIterator[str]:
            yield create_progress_event("Compiling report", 20)
            prompt = (
                "Rewrite the following non-diagnostic health summary clearly and concisely. "
                "Do not offer medical advice. Preserve the disclaimer.\n\n"
                f"{json.dumps(report, indent=2, sort_keys=True)}"
            )
            llm_text = await maybe_rewrite_with_llm(prompt)
            final_text = llm_text or report["narrative"]
            streamed_report = {**report, "mode": "llm", "narrative": final_text}
            yield create_token_event(final_text)
            yield create_done_event(json.dumps({"status": "success", "mode": "llm", "report": streamed_report}))

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return {
        "status": "success",
        "mode": "deterministic",
        "report": report,
        "context": {
            "summary_days": report["coverage"]["summary_days_covered"],
            "sleep_sessions": report["coverage"]["sleep_sessions"],
            "workouts": report["coverage"]["workouts"],
        },
    }


@router.post("/ask", operation_id="post_health_ask")
async def post_health_ask(body: AskRequest) -> dict[str, Any]:
    return await build_ask_response(body.question, body.start_date, body.end_date)
