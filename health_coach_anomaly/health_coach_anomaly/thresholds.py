"""Operator-tunable anomaly thresholds.

The defaults below mirror the AGENTS.md initial thresholds
verbatim. Operators can override individual fields by constructing a
:class:`TunableThresholds` and passing it to
:meth:`health_coach_anomaly.detector.AnomalyDetector`.

Rule direction is encoded in the threshold itself:
``drop_pct`` and ``collapse_ratio`` mean "fire when the metric gets
smaller"; ``rise_pct`` means "fire when the metric gets larger".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TunableThresholds:
    """Threshold set for the four default rules.

    Each rule contributes one or two scalar thresholds. The detector
    applies the rule's own semantic (``drop`` / ``rise`` / ``collapse``)
    against the relevant comparison window.

    Defaults match AGENTS.md §"Initial thresholds" exactly. Operators
    may tighten or loosen the cutoffs by passing a custom instance
    (e.g. an "off-season" config that requires a 20% HRV drop instead
    of 15%) without touching the rule implementations.
    """

    # HRV rule: current 7-day mean vs prior 28-day baseline.
    hrv_drop_pct: float = 0.15

    # RHR rule: current 7-day mean vs prior 28-day baseline.
    rhr_rise_pct: float = 0.10

    # Sleep rule (week-over-week).
    sleep_drop_pct: float = 0.30
    sleep_minimum_minutes: float = 6.0 * 60.0  # 6h in minutes
    sleep_consecutive_nights: int = 3

    # Steps collapse rule: 7-day mean vs 28-day baseline mean.
    steps_collapse_ratio: float = 0.50

    # Minimum sample size in the current window before the rule is
    # allowed to fire. This prevents the rule from firing off a single
    # outlier sample (e.g. 1 HRV reading in 7 days after a sync gap).
    min_current_samples: int = 3
    min_baseline_samples: int = 7


DEFAULT_THRESHOLDS = TunableThresholds()
"""Sentinel default; pass an instance of :class:`TunableThresholds`
to override individual cutoffs.
"""
