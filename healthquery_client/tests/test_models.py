from __future__ import annotations

import sys
from pathlib import Path

import pytest


PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))


from healthquery_client import (  # noqa: E402
    HealthQueryResult,
    HealthStatus,
    TimelineEvent,
)


def test_timeline_event_rejects_missing_required_fields():
    # category, type, title, summary, record_key are required; without them
    # the model must fail validation.
    with pytest.raises(Exception):
        TimelineEvent.model_validate(
            {
                "id": "evt-1",
                "timestamp": "2026-06-20T07:00:00Z",
                # missing: category, type, title, summary, record_key
            }
        )


def test_health_status_requires_status_field():
    with pytest.raises(Exception):
        HealthStatus.model_validate({"counts": {}})


def test_query_result_validates_with_no_rows():
    payload = {
        "sql": "SELECT 1",
        "row_count": 0,
        "returned_row_count": 0,
        "byte_count": 2,
        "truncated": False,
        "rows": [],
    }
    result = HealthQueryResult.model_validate(payload)
    assert result.row_count == 0
    assert result.rows == []


def test_query_result_validates_with_rows():
    payload = {
        "sql": "SELECT * FROM metric_points LIMIT 1",
        "row_count": 1,
        "returned_row_count": 1,
        "byte_count": 30,
        "truncated": False,
        "rows": [{"metric_type": "weight", "numeric_value": 82.5}],
    }
    result = HealthQueryResult.model_validate(payload)
    assert result.truncated is False
    assert result.rows[0]["metric_type"] == "weight"


def test_query_result_models_truncated_flag():
    payload = {
        "sql": "SELECT * FROM metric_points",
        "row_count": 1000,
        "returned_row_count": 1000,
        "byte_count": 1_000_000,
        "truncated": True,
        "rows": [],
    }
    result = HealthQueryResult.model_validate(payload)
    assert result.truncated is True