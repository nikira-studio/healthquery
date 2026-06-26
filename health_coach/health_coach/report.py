"""Markdown renderer and report builder for the Health Coach summary.

The renderer is a deterministic function from :class:`RenderContext`
to a Markdown string. No I/O. The same context produces the same bytes.

The Privacy Promise
-------------------

Every value rendered into the Markdown body is one of:

* a named aggregate (mean, min, max, count, percentage, etc.),
* a coarse stage-mix share (the row share of each sleep stage),
* the source ``batch_id`` (or its sha256 when disclosure is too granular),
* the run id, the data window, the read-token alias,
* a "data not available" note for an absent metric.

The body never contains:

* the raw bearer token (``HEALTHQUERY_READ_TOKEN``),
* a raw ``metric_points`` row,
* a raw ``sleep_sessions`` row,
* a raw ``workouts`` row,
* the raw ``payload_json`` of an ingest batch.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Mapping, Sequence

from .analyzer import (
    AnalysisInputs,
    AnomalyFinding,
    WeeklyTrend,
    compute_wins,
    compute_weekly_trends,
    detect_anomalies,
    next_week_focus,
)
from .vocabulary import describe_vocabulary


def deterministic_window_key(window_start: date, window_end: date) -> str:
    """A short key that uniquely names the report window.

    Used as the head of the rendered report's "window" line and in
    idempotency checks. Stable across runs.
    """
    return f"{window_start.isoformat()}_to_{window_end.isoformat()}"


def sha256_batch_id(batch_id: str | None) -> str:
    """8-char sha256 prefix; the disclosure-controlled lineage stamp."""
    if not batch_id:
        return "unknown"
    return hashlib.sha256(batch_id.encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True)
class RenderContext:
    """Everything the renderer needs to produce a Markdown summary.

    Built by :func:`build_render_context`. The renderer treats the
    context as immutable; idempotency relies on it.
    """

    window_start: date
    window_end: date
    window_label: str
    source_batch_id: str | None
    source_batch_id_sha256: str
    run_id: str
    read_token_alias: str
    api_base: str
    trends: tuple[WeeklyTrend, ...]
    wins: tuple[str, ...]
    findings: tuple[AnomalyFinding, ...]
    focus: str
    vocabulary_snapshot: Mapping[str, object]
    client_version: str = "unknown"


def build_render_context(
    inputs: AnalysisInputs,
    *,
    source_batch_id: str | None,
    run_id: str,
    read_token_alias: str,
    api_base: str,
    client_version: str = "healthquery_client",
) -> RenderContext:
    """Compute trends, findings, and wins; package them for the renderer.

    The renderer cannot run analyzer logic; this function is the only
    call site that computes the values the body renders. It is pure
    with respect to its inputs (no I/O, no clock reads).
    """
    window_label = deterministic_window_key(inputs.window_start, inputs.window_end)
    trends = compute_weekly_trends(inputs)
    findings = detect_anomalies(inputs)
    wins = compute_wins(inputs, trends)
    focus = next_week_focus(trends, findings)
    return RenderContext(
        window_start=inputs.window_start,
        window_end=inputs.window_end,
        window_label=window_label,
        source_batch_id=source_batch_id,
        source_batch_id_sha256=sha256_batch_id(source_batch_id),
        run_id=run_id,
        read_token_alias=read_token_alias,
        api_base=api_base,
        trends=tuple(trends),
        wins=tuple(wins),
        findings=tuple(findings),
        focus=focus,
        vocabulary_snapshot=describe_vocabulary(),
        client_version=client_version,
    )


def _format_aggregate(agg) -> str:
    if agg.comparator is not None and agg.comparator_label:
        return (
            f"{agg.label}: **{agg.value:g} {agg.unit}** "
            f"({agg.comparator:+.1f} {agg.comparator_label}; "
            f"n={agg.sample_size})"
        )
    return f"{agg.label}: **{agg.value:g} {agg.unit}** (n={agg.sample_size})"


def _render_trend(trend: WeeklyTrend) -> list[str]:
    lines: list[str] = [f"### {trend.name}"]
    if not trend.data_available:
        for note in trend.notes:
            lines.append(f"- {note}")
        return lines
    for agg in trend.aggregates:
        lines.append(f"- {_format_aggregate(agg)}")
    for note in trend.notes:
        lines.append(f"- {note}")
    return lines


def _render_finding(finding: AnomalyFinding) -> list[str]:
    lines = [
        f"### {finding.rule}",
        f"- severity: **{finding.severity}**",
        f"- metric: {finding.metric}",
        f"- window: {finding.window}",
        f"- finding: {finding.finding}",
    ]
    if finding.context_window:
        lines.append("- context:")
        for ctx in finding.context_window:
            lines.append(f"  - {ctx}")
    return lines


def render_weekly_summary_markdown(ctx: RenderContext) -> str:
    """Render the AGENTS.md output-bar-compliant Markdown body.

    Deterministic for the same context. Re-running with the same
    ``source_batch_id`` and window yields the same bytes (the only
    line that differs across runs is the run id footer; the caller
    passes that in explicitly).
    """
    sections: list[str] = []
    sections.append("# Weekly health summary")
    sections.append("")

    sections.append("## Window")
    sections.append("")
    sections.append(
        f"- data window: **{ctx.window_start.isoformat()} → {ctx.window_end.isoformat()}** "
        f"({ctx.window_label})"
    )
    sections.append(f"- run id: `{ctx.run_id}`")
    if ctx.source_batch_id:
        sections.append(
            f"- source batch_id: `{ctx.source_batch_id}` "
            f"(sha256: `{ctx.source_batch_id_sha256}`)"
        )
    else:
        sections.append(
            f"- source batch_id: unknown (sha256: `{ctx.source_batch_id_sha256}`)"
        )
    sections.append(f"- read token alias: `{ctx.read_token_alias}`")
    sections.append(f"- api base: `{ctx.api_base}`")
    sections.append(f"- client version: `{ctx.client_version}`")
    sections.append("")

    sections.append("## Headline")
    sections.append("")
    if ctx.findings:
        headline = ctx.findings[0].finding
    elif ctx.wins:
        headline = (
            f"Steady week — {len(ctx.wins)} wins, "
            "no rule-fired anomalies."
        )
    else:
        headline = "Quiet week — no rule-fired anomalies, no tracked wins."
    sections.append(headline)
    sections.append("")

    if ctx.wins:
        sections.append("## Wins")
        sections.append("")
        for w in ctx.wins:
            sections.append(f"- {w}")
        sections.append("")

    sections.append("## Trends")
    sections.append("")
    for trend in ctx.trends:
        sections.extend(_render_trend(trend))
        sections.append("")

    if ctx.findings:
        sections.append("## Anomalies with context")
        sections.append("")
        for finding in ctx.findings:
            sections.extend(_render_finding(finding))
            sections.append("")

    sections.append("## Next-week focus")
    sections.append("")
    sections.append(f"- {ctx.focus}")
    sections.append("")

    sections.append("## Vocabulary snapshot (for reproducibility)")
    sections.append("")
    sections.append("```")
    for key, value in sorted(ctx.vocabulary_snapshot.items()):
        sections.append(f"{key}={value}")
    sections.append("```")
    sections.append("")

    sections.append("## Data source and verification")
    sections.append("")
    sections.append(
        "The summary body is computed from HealthQuery reads only. "
        "The operator can reproduce every aggregate by running the "
        "same SQL via `POST /api/health/query` with the read token "
        f"against the api at `{ctx.api_base}` and confirming the "
        "values against the source `batch_id` above."
    )
    sections.append("")

    return "\n".join(sections).rstrip() + "\n"


def build_window_dates(
    today: date,
    *,
    window_days: int = 7,
) -> tuple[date, date]:
    """End-exclusive 7-day window: [today-7, today).

    Matches the convention the plan rev 3 §11.5.5 used for the most
    recent 7 daily_summaries: today minus 7 days (UTC) through today.
    """
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    window_start = today - timedelta(days=window_days)
    window_end = today
    return window_start, window_end
