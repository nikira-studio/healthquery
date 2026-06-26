"""Pytest configuration for the health-coach-anomaly test suite.

Adds the parent project directory to ``sys.path`` so
``import health_coach_anomaly`` resolves the package directory
(sibling of ``healthquery_client``) and ``import healthquery_client``
resolves the existing build-#2 client library.
"""

from __future__ import annotations

import sys
from pathlib import Path


PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))


INNER_PACKAGE_DIR = Path(__file__).resolve().parents[1]
if str(INNER_PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(INNER_PACKAGE_DIR))


# Standard test fixtures used across the test modules.


def fixed_now() -> "datetime.datetime":  # type: ignore[name-defined]
    import datetime as _dt

    return _dt.datetime(2026, 6, 26, 8, 0, 0, tzinfo=_dt.timezone.utc)
