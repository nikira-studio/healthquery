"""Output dataclasses for the anomaly detector.

The detector returns :class:`AnomalyReport`, a list of
:class:`Anomaly` records plus the run-level context. Each
:class:`Anomaly` carries:

* a stable ``id`` so the build-#4 weekly summary can refer to it;
* a ``severity`` so the summary can render "clinically significant"
  flags prominently (per AGENTS.md §"Output bar");
* a ``context`` dict so the summary can render the context block
  ("what else moved") inline without re-querying HealthQuery;
* a ``data_window`` so the operator can reproduce the source
  numbers via curl (third-party verification per AGENTS.md).

The output is **plain data** — no HealthQuery types, no Pydantic
field validators that could pull in HealthQuery's evolving contract.
That keeps the build-#4 consumer decoupled from HealthQuery schema
bumps.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any

from .windows import WindowSpec


class AnomalySeverity(str, enum.Enum):
    """How prominently the weekly summary should render a flag.

    AGENTS.md §"Output bar" splits flagged metrics into two
    presentation tiers: trend-context anomalies and clinically
    significant anomalies. The summary must flag the latter
    prominently, not bury them in the trend section.
    """

    INFO = "info"
    """Trend-context anomaly; rendered inside the weekly summary
    "trends" section."""

    PROMINENT = "prominent"
    """Clinically significant anomaly (HRV crash + reported illness,
    weight loss beyond expected, sleep collapse) — rendered at the
    top of the summary, not buried."""


class AnomalyStatus(str, enum.Enum):
    """Whether the rule fired, was suppressed, or saw no data."""

    FIRED = "fired"
    """Threshold met; the flag is real."""

    WITHIN_THRESHOLD = "within_threshold"
    """The detector ran end-to-end and the metric is inside its
    band. Recorded so the operator can see the detector actually
    checked."""

    DATA_NOT_AVAILABLE = "data_not_available"
    """The relevant ``metric_type`` is absent from the live data
    (e.g. HRV not yet ingested). The detector did not fire and
    will not produce a flag."""

    INSUFFICIENT_SAMPLES = "insufficient_samples"
    """There is some data, but not enough to apply the rule
    reliably (fewer than ``min_current_samples`` in the current
    window or ``min_baseline_samples`` in the baseline)."""


@dataclass(frozen=True)
class Anomaly:
    """A single anomaly check result.

    ``id`` is a stable, human-readable identifier (e.g.
    ``"hrv_drop_2026-06-19_to_2026-06-26"``) so the build-#4
    weekly summary can refer to the same record across renders.

    Window shape (so the operator can reproduce the numbers):

    * ``data_window`` — the *current* window (start, end, days).
    * ``baseline_window`` — the *baseline* window (start, end, days).
    * ``current_value`` — the metric's current stats
      (e.g. ``{"mean_ms": 45, "samples": 5}`` for HRV).
    * ``baseline_value`` — the metric's baseline stats
      (e.g. ``{"mean_ms": 60, "samples": 30}``).
    * ``context`` — co-movement and illness/training-load signals.
    """

    id: str
    rule: str
    metric: str
    status: AnomalyStatus
    severity: AnomalySeverity
    summary: str
    data_window: dict[str, str]
    baseline_window: dict[str, str]
    current_value: dict[str, Any]
    baseline_value: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)
    recommendation: str | None = None
    source: str = "healthquery"

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict (datetimes become ISO 8601 strings)."""
        return asdict(self)


@dataclass(frozen=True)
class AnomalyReport:
    """A complete detector run.

    The weekly summary consumes this directly. ``anomalies`` lists
    every rule outcome (fired, within-threshold, and
    data-not-available), so the summary can render both "what
    tripped" and "what we checked and is fine".
    """

    run_id: str
    window: WindowSpec
    baseline: WindowSpec
    healthquery_batch_id: str | None
    healthquery_base_url: str
    generated_at: str
    anomalies: list[Anomaly]
    thresholds: dict[str, Any]

    @property
    def fired(self) -> list[Anomaly]:
        return [a for a in self.anomalies if a.status == AnomalyStatus.FIRED]

    @property
    def prominent(self) -> list[Anomaly]:
        return [a for a in self.fired if a.severity == AnomalySeverity.PROMINENT]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "data_window": {
                "start": self.window.iso_start,
                "end": self.window.iso_end,
                "days": self.window.days,
            },
            "baseline": {
                "start": self.baseline.iso_start,
                "end": self.baseline.iso_end,
                "days": self.baseline.days,
            },
            "healthquery_batch_id": self.healthquery_batch_id,
            "healthquery_base_url": self.healthquery_base_url,
            "generated_at": self.generated_at,
            "thresholds": self.thresholds,
            "anomalies": [a.to_dict() for a in self.anomalies],
        }
