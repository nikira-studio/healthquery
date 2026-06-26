"""Anomaly detector for the Health Coach (STA-5 build #5).

The detector applies the AGENTS.md anomaly thresholds to HealthQuery
metrics and returns a list of structured :class:`~health_coach_anomaly.output.Anomaly`
records. Each flag includes a **context window** — what else moved in
the same range — not just a raw deviation.

See ``README.md`` for usage and the package layout.
"""

from __future__ import annotations

from .detector import AnomalyDetector
from .output import Anomaly, AnomalyReport, AnomalySeverity
from .rules import BUILTIN_RULES
from .thresholds import DEFAULT_THRESHOLDS, TunableThresholds
from .windows import WindowSpec, daily_mean, percent_change

__all__ = [
    "Anomaly",
    "AnomalyDetector",
    "AnomalyReport",
    "AnomalySeverity",
    "BUILTIN_RULES",
    "DEFAULT_THRESHOLDS",
    "TunableThresholds",
    "WindowSpec",
    "daily_mean",
    "percent_change",
]
