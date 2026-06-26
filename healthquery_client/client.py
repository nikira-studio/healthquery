"""Synchronous and async HTTP clients for the HealthQuery read API.

The clients are thin wrappers around :mod:`httpx`. They centralise auth
header construction, base URL resolution, error mapping, retry, and
the endpoint-to-method mapping so individual callers (Health Coach,
reporters, MCP server, tests) can stay small.

Design notes
------------

* ``HEALTHQUERY_BASE_URL`` defaults to ``http://healthquery-api:3136``
  because that is the in-cluster DNS name + port the Health Coach and
  other Docker Compose services should use. Operators running the client
  outside the compose network can override the env var, or pass
  ``base_url`` explicitly.
* ``HEALTHQUERY_READ_TOKEN`` is mandatory. The clients raise
  :class:`HealthQueryAuthError` at construction time if it is missing.
* The HTTP transport is injectable for tests; pass a
  ``httpx.Client``/``httpx.AsyncClient`` instance via ``transport=`` to
  point the client at a mock.
* 5xx responses are retried up to three times with exponential backoff.
  401/403 responses are never retried — they surface as
  :class:`HealthQueryAuthError` immediately.
* The bearer token is never written to logs, never included in error
  messages, and never appears in ``__repr__`` / ``__str__``.
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import Any, Awaitable, Callable, Mapping, MutableMapping

import httpx

from .exceptions import (
    HealthQueryAPIError,
    HealthQueryAuthError,
    HealthQuerySQLGuardError,
    HealthQueryTransportError,
)
from .models import ConfigResponse, HealthQueryResult, HealthStatus

DEFAULT_BASE_URL = "http://healthquery-api:3136"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5
API_PREFIX = "/api/health"
REPORTS_PREFIX = "/api/reports"


def _default_base_url() -> str:
    value = os.getenv("HEALTHQUERY_BASE_URL", "").strip()
    return value.rstrip("/") if value else DEFAULT_BASE_URL


def _default_read_token() -> str:
    return os.getenv("HEALTHQUERY_READ_TOKEN", "").strip()


def _is_sql_guard_rejection(status_code: int, payload: object) -> bool:
    """Heuristic for backend ``SqlGuardError`` 400 responses.

    The backend raises ``SqlGuardError`` for any rejection of the
    ``/api/health/query`` payload. The messages are stable across versions
    (see ``backend/services/sql_guard.py``), so matching them is reliable.
    """
    if status_code != 400:
        return False
    text: str | None = None
    if isinstance(payload, str):
        text = payload
    elif isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            text = detail
    if text is None:
        return False
    lowered = text.lower()
    needles = (
        "only select statements",
        "only one statement",
        "forbidden sql construct",
        "invalid sql",
        "sql query is required",
        "sql guard",
    )
    return any(needle in lowered for needle in needles)


def _redact_token(text: str, token: str | None) -> str:
    """Return ``text`` with the bearer token (if any) replaced by ``***``.

    Only tokens that look like bearer tokens (>= 8 chars, not pure
    whitespace) are scrubbed, so short single-character tokens cannot
    accidentally rewrite legitimate server messages.
    """
    if not token or len(token) < 8:
        return text
    return text.replace(token, "***")


class _BaseClient:
    """Shared state for sync and async clients."""

    def __init__(
        self,
        base_url: str | None = None,
        read_token: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        retry_sleep: Callable[[float], None] | None = None,
        retry_async_sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        resolved_base = (base_url if base_url is not None else _default_base_url()).strip()
        if not resolved_base:
            raise HealthQueryAuthError("HealthQuery base_url is required")
        resolved_token = (read_token if read_token is not None else _default_read_token()).strip()
        if not resolved_token:
            raise HealthQueryAuthError(
                "HEALTHQUERY_READ_TOKEN is required; "
                "pass read_token= or set the env var"
            )
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be >= 0")
        self.base_url = resolved_base.rstrip("/")
        self.read_token = resolved_token
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self._retry_sleep = retry_sleep
        self._retry_async_sleep = retry_async_sleep

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(base_url={self.base_url!r}, "
            f"read_token=***, timeout_seconds={self.timeout_seconds}, "
            f"max_retries={self.max_retries})"
        )

    @property
    def _headers(self) -> MutableMapping[str, str]:
        return {"Authorization": f"Bearer {self.read_token}"}

    def _should_retry(self, status_code: int, attempt: int) -> bool:
        return 500 <= status_code < 600 and attempt < self.max_retries

    def _compute_backoff(self, attempt: int) -> float:
        base = self.retry_backoff_seconds * (2 ** attempt)
        jitter = random.uniform(0.0, base * 0.25)
        return base + jitter

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, token: str | None = None) -> None:
        if response.status_code < 400:
            return
        if response.status_code in (401, 403):
            raise HealthQueryAuthError(
                _redact_token(
                    f"HealthQuery rejected the bearer token (HTTP {response.status_code})",
                    token,
                )
            )
        api_error = HealthQueryAPIError.from_response(response)
        # Defense in depth: if the api somehow echoes the bearer in the
        # detail, scrub it before the exception text leaves the client.
        if token:
            scrubbed_detail: object = _redact_token(str(api_error.detail), token)
        else:
            scrubbed_detail = api_error.detail
        # SQL guard rejections get their own subclass so callers can branch
        # on the specific failure mode without parsing the message.
        if _is_sql_guard_rejection(api_error.status_code, scrubbed_detail):
            raise HealthQuerySQLGuardError(api_error.status_code, scrubbed_detail)
        raise HealthQueryAPIError(api_error.status_code, scrubbed_detail)


class HealthQueryClient(_BaseClient):
    """Synchronous HealthQuery client.

    Example::

        from healthquery_client import HealthQueryClient

        with HealthQueryClient() as client:
            status = client.get_status()
            overview = client.get_overview()
            timeline = client.get_timeline(days=14)
    """

    def __init__(
        self,
        base_url: str | None = None,
        read_token: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        retry_sleep: Callable[[float], None] | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            read_token=read_token,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            retry_sleep=retry_sleep,
        )
        if transport is not None:
            self._owns_client = False
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=dict(self._headers),
                timeout=timeout_seconds,
                transport=transport,
            )
        else:
            self._owns_client = True
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=dict(self._headers),
                timeout=timeout_seconds,
            )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "HealthQueryClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def _sleep(self, seconds: float) -> None:
        if self._retry_sleep is not None:
            self._retry_sleep(seconds)
            return
        import time

        time.sleep(seconds)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        auth_required: bool = True,
    ) -> Any:
        """Issue a request with 5xx retry.

        ``auth_required=False`` is used by :meth:`probe` for the no-auth
        liveness endpoint, which must not send ``Authorization``.
        """
        # When auth is not required, pass an empty Authorization header so
        # it overrides the base client headers (httpx merges request-level
        # headers over base headers, but only when the request header is
        # explicitly set to an empty string for the well-known header).
        headers_override: dict[str, str] | None = None
        if not auth_required:
            headers_override = {"Authorization": ""}
        last_response: httpx.Response | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    headers=headers_override,
                )
            except httpx.HTTPError as exc:
                raise HealthQueryTransportError(
                    f"HealthQuery request failed: {exc}"
                ) from exc
            last_response = response
            if 500 <= response.status_code < 600 and self._should_retry(
                response.status_code, attempt
            ):
                self._sleep(self._compute_backoff(attempt))
                continue
            break
        assert last_response is not None
        self._raise_for_status(last_response, token=None if not auth_required else self.read_token)
        if last_response.status_code == 204 or not last_response.content:
            return None
        try:
            return last_response.json()
        except ValueError as exc:
            # Defense in depth: redact the bearer from the surfaced body
            # text so the exception never echoes the token. This mirrors
            # the 401/403 path at :meth:`_raise_for_status` and closes the
            # last remaining surface where the token could leak via repr().
            safe_text = _redact_token(last_response.text, self.read_token)
            raise HealthQueryTransportError(
                f"HealthQuery returned non-JSON body: {safe_text!r}"
            ) from exc

    # --- /api/health/* ---

    def probe(self) -> dict[str, Any]:
        """Call ``GET /api/health`` (no auth) for liveness checks."""
        return self._request_json("GET", "/api/health", auth_required=False)

    def get_status(self) -> "HealthStatus":
        return HealthStatus.model_validate(self._request_json("GET", f"{API_PREFIX}/status"))

    def get_overview(self) -> dict[str, Any]:
        return self._request_json("GET", f"{API_PREFIX}/overview")

    def get_summary(self) -> dict[str, Any]:
        return self._request_json("GET", f"{API_PREFIX}/summary")

    def get_activity(self) -> dict[str, Any]:
        return self._request_json("GET", f"{API_PREFIX}/activity")

    def get_sleep(self) -> dict[str, Any]:
        return self._request_json("GET", f"{API_PREFIX}/sleep")

    def get_vitals(self) -> dict[str, Any]:
        return self._request_json("GET", f"{API_PREFIX}/vitals")

    def get_body(self) -> dict[str, Any]:
        return self._request_json("GET", f"{API_PREFIX}/body")

    def get_timeline(self, days: int = 14) -> dict[str, Any]:
        return self._request_json("GET", f"{API_PREFIX}/timeline", params={"days": days})

    def get_batches(self, limit: int = 10) -> dict[str, Any]:
        return self._request_json("GET", f"{API_PREFIX}/batches", params={"limit": limit})

    def get_config(self) -> "ConfigResponse":
        return ConfigResponse.model_validate(self._request_json("GET", f"{API_PREFIX}/config"))

    def post_query(self, sql: str) -> "HealthQueryResult":
        return HealthQueryResult.model_validate(
            self._request_json("POST", f"{API_PREFIX}/query", json_body={"sql": sql})
        )

    # --- /api/reports/* ---

    def generate_doctor_visit_report(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"stream": stream}
        if start_date is not None:
            body["start_date"] = start_date
        if end_date is not None:
            body["end_date"] = end_date
        return self._request_json("POST", f"{REPORTS_PREFIX}/doctor-visit", json_body=body)

    def ask_health_question(
        self,
        question: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"question": question}
        if start_date is not None:
            body["start_date"] = start_date
        if end_date is not None:
            body["end_date"] = end_date
        return self._request_json("POST", f"{REPORTS_PREFIX}/ask", json_body=body)


class AsyncHealthQueryClient(_BaseClient):
    """Asynchronous HealthQuery client.

    Same surface as :class:`HealthQueryClient`; uses ``httpx.AsyncClient``
    so callers can ``await`` calls in a FastAPI/agent loop.
    """

    def __init__(
        self,
        base_url: str | None = None,
        read_token: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        retry_sleep: Callable[[float], Awaitable[None]] | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            read_token=read_token,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            retry_async_sleep=retry_sleep,
        )
        if transport is not None:
            self._owns_client = False
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=dict(self._headers),
                timeout=timeout_seconds,
                transport=transport,
            )
        else:
            self._owns_client = True
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=dict(self._headers),
                timeout=timeout_seconds,
            )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "AsyncHealthQueryClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    async def _sleep(self, seconds: float) -> None:
        if self._retry_async_sleep is not None:
            await self._retry_async_sleep(seconds)
            return
        await asyncio.sleep(seconds)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        auth_required: bool = True,
    ) -> Any:
        headers_override: dict[str, str] | None = None
        if not auth_required:
            headers_override = {"Authorization": ""}
        last_response: httpx.Response | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    headers=headers_override,
                )
            except httpx.HTTPError as exc:
                raise HealthQueryTransportError(
                    f"HealthQuery request failed: {exc}"
                ) from exc
            last_response = response
            if 500 <= response.status_code < 600 and self._should_retry(
                response.status_code, attempt
            ):
                await self._sleep(self._compute_backoff(attempt))
                continue
            break
        assert last_response is not None
        self._raise_for_status(
            last_response, token=None if not auth_required else self.read_token
        )
        if last_response.status_code == 204 or not last_response.content:
            return None
        try:
            return last_response.json()
        except ValueError as exc:
            # Defense in depth: mirror the sync client — redact the bearer
            # from the surfaced body text so the exception never echoes
            # the token.
            safe_text = _redact_token(last_response.text, self.read_token)
            raise HealthQueryTransportError(
                f"HealthQuery returned non-JSON body: {safe_text!r}"
            ) from exc

    # --- /api/health/* ---

    async def probe(self) -> dict[str, Any]:
        return await self._request_json("GET", "/api/health", auth_required=False)

    async def get_status(self) -> "HealthStatus":
        return HealthStatus.model_validate(
            await self._request_json("GET", f"{API_PREFIX}/status")
        )

    async def get_overview(self) -> dict[str, Any]:
        return await self._request_json("GET", f"{API_PREFIX}/overview")

    async def get_summary(self) -> dict[str, Any]:
        return await self._request_json("GET", f"{API_PREFIX}/summary")

    async def get_activity(self) -> dict[str, Any]:
        return await self._request_json("GET", f"{API_PREFIX}/activity")

    async def get_sleep(self) -> dict[str, Any]:
        return await self._request_json("GET", f"{API_PREFIX}/sleep")

    async def get_vitals(self) -> dict[str, Any]:
        return await self._request_json("GET", f"{API_PREFIX}/vitals")

    async def get_body(self) -> dict[str, Any]:
        return await self._request_json("GET", f"{API_PREFIX}/body")

    async def get_timeline(self, days: int = 14) -> dict[str, Any]:
        return await self._request_json("GET", f"{API_PREFIX}/timeline", params={"days": days})

    async def get_batches(self, limit: int = 10) -> dict[str, Any]:
        return await self._request_json("GET", f"{API_PREFIX}/batches", params={"limit": limit})

    async def get_config(self) -> "ConfigResponse":
        return ConfigResponse.model_validate(
            await self._request_json("GET", f"{API_PREFIX}/config")
        )

    async def post_query(self, sql: str) -> "HealthQueryResult":
        return HealthQueryResult.model_validate(
            await self._request_json("POST", f"{API_PREFIX}/query", json_body={"sql": sql})
        )

    # --- /api/reports/* ---

    async def generate_doctor_visit_report(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"stream": stream}
        if start_date is not None:
            body["start_date"] = start_date
        if end_date is not None:
            body["end_date"] = end_date
        return await self._request_json("POST", f"{REPORTS_PREFIX}/doctor-visit", json_body=body)

    async def ask_health_question(
        self,
        question: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"question": question}
        if start_date is not None:
            body["start_date"] = start_date
        if end_date is not None:
            body["end_date"] = end_date
        return await self._request_json("POST", f"{REPORTS_PREFIX}/ask", json_body=body)