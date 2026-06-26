# health-coach-anomaly

Anomaly detector for the Health Coach, scoped to [STA-5 plan rev 3 §7 #5](https://example.invalid/STA-5#document-plan).

This package reads from the HealthQuery read API (via the [`healthquery-client`](../healthquery_client) package) and applies the AGENTS.md anomaly thresholds to the live metrics. Each flag includes a **context window** — what else moved in the same range — not just a raw deviation.

## Initial thresholds (from AGENTS.md)

| Rule | Trigger | Baseline |
| --- | --- | --- |
| HRV drop | `> 15%` over the past 7 days | prior 28 days |
| RHR rise | `> 10%` over the past 7 days | prior 28 days |
| Sleep collapse | total sleep minutes drop `> 30%` week-over-week, or nightly `< 6h` for `3+` consecutive nights | prior 28 days |
| Steps collapse | 7-day mean steps `< 50%` of the 28-day baseline | prior 28 days |

When the relevant `metric_type` is not present in the live data (e.g. HRV not yet ingested), the detector returns a `status='data_not_available'` record and does **not** fire a false flag.

## Quick start

```python
from health_coach_anomaly import AnomalyDetector
from healthquery_client import HealthQueryClient

with HealthQueryClient() as hq:
    detector = AnomalyDetector(hq)
    report = detector.detect(window_days=7, baseline_days=28)

for anomaly in report.anomalies:
    print(anomaly.severity, anomaly.metric, anomaly.summary)
```

`AnomalyDetector.detect(...)` is idempotent — re-running against the same `batch_id` returns the same output bytes for a given `HealthQuery` snapshot.

## Layout

```
health_coach_anomaly/
  thresholds.py   # default thresholds (AGENTS.md) + TunableThresholds dataclass
  windows.py      # window/baseline date math, mean/percent-change helpers
  output.py       # Anomaly, AnomalyReport dataclasses
  rules.py        # the four default rules (HRV / RHR / Sleep / Steps)
  context.py      # context-window co-movement + sleep-sickness + training-load
  detector.py     # orchestrator (queries HealthQuery, runs rules, returns report)
```

## Out of scope (deliberately)

* The detector does **not** fire when the trend is within threshold (no false-positive spam).
* The detector does **not** fire when the relevant `metric_type` is absent — it returns `data_not_available`.
* The detector does **not** write back to HealthQuery, the vendor API, or the raw export path.
* The detector does **not** make clinical or prescriptive recommendations. Every output is an observation; the operator is the decision-maker.
