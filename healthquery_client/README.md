# healthquery-client

Read-only Python client library for the HealthQuery backend.

The package is the canonical way for trusted internal agents (notably the
**Health Coach**) to query the local HealthQuery service without holding
ingest credentials or hand-rolling HTTP calls.

## What it covers

The client targets the read-only surface only:

* `GET /api/health/status`, `/overview`, `/summary`, `/activity`,
  `/sleep`, `/vitals`, `/body`, `/timeline`, `/batches`, `/config`
* `POST /api/health/query` — read-only SQL guard; capped at 1000 rows / 1 MB
* `POST /api/reports/doctor-visit` — deterministic JSON report
* `POST /api/reports/ask` — optional LLM-backed Q&A

The ingest webhook (`POST /api/webhook/health`) is intentionally **not**
exposed. Agents must never have the ingest token.

## Install

From the HealthQuery repo root, editable install:

```bash
pip install -e ./healthquery_client
```

Or copy the `healthquery_client/` directory into your project and
`pip install httpx pydantic` directly.

## Usage

### Sync

```python
from healthquery_client import HealthQueryClient

with HealthQueryClient() as client:
    status = client.get_status()
    overview = client.get_overview()
    timeline = client.get_timeline(days=14)

    if status["counts"]["ingest_batches"] == 0:
        print("No data yet.")
```

### Async

```python
import asyncio
from healthquery_client import AsyncHealthQueryClient

async def main():
    async with AsyncHealthQueryClient() as client:
        status = await client.get_status()
        overview = await client.get_overview()
        report = await client.generate_doctor_visit_report(
            start_date="2026-06-01", end_date="2026-06-20"
        )
    print(report["title"])

asyncio.run(main())
```

### Typed responses

The library ships Pydantic models for the endpoints that agents consume
most often:

```python
from healthquery_client import (
    AsyncHealthQueryClient,
    HealthStatus,
    TimelineEvent,
    HealthQueryResult,
)

async def fetch_recent_events(client: AsyncHealthQueryClient) -> list[TimelineEvent]:
    raw = await client.get_timeline(days=7)
    return [TimelineEvent.model_validate(e) for e in raw["events"]]
```

### Custom base URL or transport

```python
import httpx
from healthquery_client import HealthQueryClient

# Override base URL for an out-of-cluster consumer.
client = HealthQueryClient(base_url="https://health.veditz.com")

# Inject a mock transport for tests.
mock_transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"status": "ok"}))
client = HealthQueryClient(transport=mock_transport)
```

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `HEALTHQUERY_BASE_URL` | `http://healthquery-api:3136` | Base URL of the api container. Override for out-of-cluster callers. |
| `HEALTHQUERY_READ_TOKEN` | _(required)_ | Bearer token for the read API. |

If the token is missing at construction time, the client raises
`HealthQueryAuthError` immediately.

## Error model

| Exception | When |
| --- | --- |
| `HealthQueryError` | Base class for every error raised by the library. |
| `HealthQueryAuthError` | Missing token or HTTP 401/403. |
| `HealthQueryTransportError` | Connection refused, DNS failure, non-JSON body. |
| `HealthQueryAPIError` | API returned a 4xx/5xx with a JSON body. |
| `HealthQuerySQLGuardError` | The backend's read-only SQL guard rejected a query. |

## Testing

```bash
pip install -e ".[test]"
pytest -q
```

Tests use `httpx.MockTransport` so they exercise the real client wiring
without touching the network.

## Versioning

This package follows the HealthQuery backend's `/api/health/*` contract.
Pinning the backend version that a given client release targets is the
operator's responsibility — the package does not negotiate the contract
at runtime.