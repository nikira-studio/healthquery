"""Tests for the renderer and the privacy contract."""

from __future__ import annotations

from datetime import date, datetime, timezone

from health_coach import (
    AnalysisInputs,
    build_render_context,
    deterministic_window_key,
    render_weekly_summary_markdown,
    sha256_batch_id,
)


def test_window_key_is_stable() -> None:
    a = deterministic_window_key(date(2026, 6, 19), date(2026, 6, 25))
    b = deterministic_window_key(date(2026, 6, 19), date(2026, 6, 25))
    assert a == b
    assert a == "2026-06-19_to_2026-06-25"


def test_sha256_batch_id_is_short_and_stable() -> None:
    a = sha256_batch_id("batch_140edd3fa87c4b0b8f461bdbe51e9960")
    b = sha256_batch_id("batch_140edd3fa87c4b0b8f461bdbe51e9960")
    assert a == b
    assert len(a) == 8


def test_render_output_includes_every_agends_md_section() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
        daily_summaries=[],
    )
    ctx = build_render_context(
        inputs,
        source_batch_id="batch_test",
        run_id="run_test",
        read_token_alias="operator",
        api_base="http://example",
    )
    body = render_weekly_summary_markdown(ctx)
    for header in (
        "## Window",
        "## Headline",
        "## Trends",
        "## Next-week focus",
        "## Vocabulary snapshot (for reproducibility)",
        "## Data source and verification",
    ):
        assert header in body, header
    assert "batch_test" in body
    assert "run_test" in body
    assert "operator" in body


def test_render_output_never_contains_token_or_raw_rows() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
        daily_summaries=[],
        sleep_sessions=[
            {
                "session_key": "session_with_token",
                "start_time": "2026-06-20T03:00:00Z",
                "end_time": "2026-06-20T11:00:00Z",
                "duration_minutes": 480.0,
                "efficiency_pct": None,
            }
        ],
        heart_rate_points=[
            {
                "metric_type": "heart_rate",
                "recorded_at": "2026-06-20T04:00:00Z",
                "numeric_value": 72.0,
            }
        ],
    )
    ctx = build_render_context(
        inputs,
        source_batch_id="batch_test",
        run_id="run_test",
        read_token_alias="operator",
        api_base="http://example",
    )
    body = render_weekly_summary_markdown(ctx)
    # Privacy contract: no raw row identifiers and no token strings.
    assert "session_with_token" not in body
    assert "72.0 bpm" not in body  # raw HR point would be a privacy leak


def test_render_output_uses_sha256_when_batch_id_set() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
    )
    ctx = build_render_context(
        inputs,
        source_batch_id="batch_XYZ",
        run_id="run_test",
        read_token_alias="operator",
        api_base="http://example",
    )
    body = render_weekly_summary_markdown(ctx)
    assert "batch_XYZ" in body
    assert ctx.source_batch_id_sha256 in body


def test_render_output_is_deterministic_for_same_inputs() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
        daily_summaries=[],
    )
    ctx_a = build_render_context(
        inputs,
        source_batch_id="batch_test",
        run_id="run_test",
        read_token_alias="operator",
        api_base="http://example",
    )
    ctx_b = build_render_context(
        inputs,
        source_batch_id="batch_test",
        run_id="run_test",
        read_token_alias="operator",
        api_base="http://example",
    )
    a = render_weekly_summary_markdown(ctx_a)
    b = render_weekly_summary_markdown(ctx_b)
    assert a == b


def test_render_output_uses_data_not_available_for_absent_metrics() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
    )
    ctx = build_render_context(
        inputs,
        source_batch_id=None,
        run_id="run_test",
        read_token_alias="operator",
        api_base="http://example",
    )
    body = render_weekly_summary_markdown(ctx)
    # HRV section is present and shows data-not-available
    assert "HRV (heart rate variability)" in body
    assert "data not available" in body
    assert "Body composition" in body


def test_render_output_stamps_today_aware_dates_via_metadata() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
    )
    ctx = build_render_context(
        inputs,
        source_batch_id="batch_test",
        run_id="run_test",
        read_token_alias="operator",
        api_base="http://example",
    )
    body = render_weekly_summary_markdown(ctx)
    assert "2026-06-19 → 2026-06-25" in body
