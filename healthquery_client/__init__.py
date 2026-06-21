"""Reusable Python client library for the HealthQuery read API.

The Health Coach (and other trusted internal agents) call into this package
instead of hand-rolling HTTP calls. The library targets the read-only surface
of the HealthQuery backend, so it never holds or accepts the ingest token.

The package is intentionally narrow:

* it speaks only to ``/api/health/*`` and ``/api/reports/*``;
* it requires the ``HEALTHQUERY_READ_TOKEN`` bearer;
* it does not depend on FastAPI, FastMCP, or any agent framework, so it can be
  imported from any Python process that can reach the api container;
* it exposes both sync and async clients backed by ``httpx``.

Install in editable mode from the repo root::

    pip install -e ./healthquery_client
"""

from __future__ import annotations

from .client import AsyncHealthQueryClient, HealthQueryClient
from .exceptions import (
    HealthQueryAPIError,
    HealthQueryAuthError,
    HealthQueryError,
    HealthQuerySQLGuardError,
    HealthQueryTransportError,
)
from .models import (
    BatchSummary,
    ConfigResponse,
    HealthQueryResult,
    HealthStatus,
    TimelineEvent,
)

__all__ = [
    "AsyncHealthQueryClient",
    "BatchSummary",
    "ConfigResponse",
    "HealthQueryAPIError",
    "HealthQueryAuthError",
    "HealthQueryClient",
    "HealthQueryError",
    "HealthQueryResult",
    "HealthQuerySQLGuardError",
    "HealthQueryTransportError",
    "HealthStatus",
    "TimelineEvent",
]