from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from auth import require_ingest_auth
from services.ingest import ingest_health_payload

router = APIRouter(prefix="/api/webhook", tags=["webhook"], include_in_schema=False)


@router.post("/health", dependencies=[Depends(require_ingest_auth)])
async def ingest_health_webhook(request: Request) -> dict[str, object]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid JSON payload",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Webhook payload must be a JSON object",
        )

    result = await ingest_health_payload(payload)
    return {
        "status": "success",
        "processed": result["processed"],
        "inserted": result["inserted"],
        "updated": result["updated"],
        "skipped": result["skipped"],
        "errors": result["errors"],
        "batch_id": result["batch_id"],
    }
