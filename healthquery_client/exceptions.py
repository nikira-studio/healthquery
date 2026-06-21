"""Exception hierarchy for the HealthQuery client library.

All exceptions raised by the client inherit from :class:`HealthQueryError`
so callers can opt into catching every library error with a single
``except`` clause while still being able to distinguish specific failure
modes when they care.
"""

from __future__ import annotations

import httpx


class HealthQueryError(Exception):
    """Base class for every error raised by :mod:`healthquery_client`."""


class HealthQueryAuthError(HealthQueryError):
    """Raised when the api rejects the bearer token (HTTP 401/403)."""


class HealthQueryTransportError(HealthQueryError):
    """Raised when the api is unreachable or returns a non-JSON error body.

    The underlying :class:`httpx.HTTPError` is attached as ``__cause__``.
    """


class HealthQueryAPIError(HealthQueryError):
    """Raised when the api returns a structured 4xx/5xx error with a JSON body.

    Attributes:
        status_code: HTTP status returned by the api.
        detail: Parsed ``detail`` field from the error body, or the raw text
            if the body did not contain one.
    """

    def __init__(self, status_code: int, detail: object) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HealthQuery API error {status_code}: {detail}")

    @classmethod
    def from_response(cls, response: httpx.Response) -> "HealthQueryAPIError":
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        detail: object
        if isinstance(payload, dict) and "detail" in payload:
            detail = payload["detail"]
        else:
            detail = payload
        return cls(response.status_code, detail)


class HealthQuerySQLGuardError(HealthQueryAPIError):
    """Raised when the backend SQL guard rejects a query (HTTP 400).

    Subclass of :class:`HealthQueryAPIError` so callers can still treat it
    as an api error if they do not care about the specific failure mode.
    """