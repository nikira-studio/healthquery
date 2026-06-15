from __future__ import annotations

import os

from fastapi import Header, HTTPException, status
from fastapi import Request

from app_settings import get_settings


def _extract_token(header_value: str) -> str:
    if not header_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
        )
    if header_value.lower().startswith("bearer "):
        return header_value.split(" ", 1)[1].strip()
    return header_value.strip()


def _require_token(provided: str, expected: str, token_kind: str) -> None:
    if provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid {token_kind} token",
        )


def _alternate_header_name() -> str | None:
    name = os.getenv("HEALTHQUERY_AUTH_HEADER", "").strip()
    return name or None


async def require_ingest_auth(
    request: Request,
    authorization: str = Header(default=""),
) -> None:
    settings = get_settings()
    if authorization:
        _require_token(_extract_token(authorization), settings.ingest_token, "ingest")
        return

    header_name = _alternate_header_name()
    if header_name:
        provided = request.headers.get(header_name, "")
        _require_token(_extract_token(provided), settings.ingest_token, "ingest")
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing Authorization header",
    )


async def require_read_auth(authorization: str = Header(default="")) -> None:
    settings = get_settings()
    _require_token(_extract_token(authorization), settings.read_token, "read")
