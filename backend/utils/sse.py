from __future__ import annotations

import json


def create_sse_event(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


def create_progress_event(message: str, percent: int) -> str:
    return create_sse_event("progress", json.dumps({"message": message, "percent": percent}))


def create_token_event(token: str) -> str:
    return create_sse_event("token", json.dumps(token))


def create_done_event(data: str = "{}") -> str:
    return create_sse_event("done", data)
