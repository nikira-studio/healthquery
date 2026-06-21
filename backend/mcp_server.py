from __future__ import annotations

from datetime import date

from fastmcp import FastMCP

from healthquery_client import AsyncHealthQueryClient


DEFAULT_TIMEOUT_SECONDS = 30.0


def _new_client() -> AsyncHealthQueryClient:
    return AsyncHealthQueryClient()


def _parse_iso_date(value: date | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    value = value.strip()
    if not value:
        return None
    return date.fromisoformat(value)


def _date_range_clause(field_name: str, start_date: date | str | None, end_date: date | str | None) -> str:
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    clauses: list[str] = []
    if start is not None:
        clauses.append(f"date({field_name}) >= date('{start.isoformat()}')")
    if end is not None:
        clauses.append(f"date({field_name}) <= date('{end.isoformat()}')")
    return " AND ".join(clauses) if clauses else "1 = 1"


def _requested_range(start_date: date | str | None, end_date: date | str | None) -> dict[str, str | None]:
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    return {
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
    }


mcp = FastMCP(
    "HealthQuery",
    instructions=(
        "Read-only HealthQuery agent tools. "
        "Use these tools to inspect summaries, trends, reports, and safe SQL output. "
        "Do not use this server for ingest or mutation."
    ),
)


@mcp.tool
async def get_health_overview(days: int = 14) -> dict[str, object]:
    """Return the overview dashboard data plus a recent timeline window."""
    client = _new_client()
    overview = await client.get_overview()
    timeline = await client.get_timeline(days=days)
    return {
        "days": days,
        "overview": overview,
        "timeline": timeline,
    }


@mcp.tool
async def get_daily_activity(start_date: date | str | None = None, end_date: date | str | None = None) -> dict[str, object]:
    """Return activity summaries for a date range."""
    client = _new_client()
    result = await client.post_query(
        sql=(
            "SELECT summary_date, steps, active_minutes, sleep_minutes, workouts, updated_at "
            "FROM daily_summaries "
            f"WHERE {_date_range_clause('summary_date', start_date, end_date)} "
            "ORDER BY summary_date ASC"
        )
    )
    return {
        **_requested_range(start_date, end_date),
        "daily_summaries": result["rows"],
    }


@mcp.tool
async def get_sleep_summary(start_date: date | str | None = None, end_date: date | str | None = None) -> dict[str, object]:
    """Return sleep sessions and stage rows for a date range."""
    client = _new_client()
    sessions = await client.post_query(
        sql=(
            "SELECT session_key, start_time, end_time, duration_minutes, efficiency_pct "
            "FROM sleep_sessions "
            f"WHERE {_date_range_clause('start_time', start_date, end_date)} "
            "ORDER BY start_time ASC"
        )
    )
    stages = await client.post_query(
        sql=(
            "SELECT stage_key, session_key, stage_type, start_time, end_time, duration_seconds "
            "FROM sleep_stages "
            f"WHERE {_date_range_clause('start_time', start_date, end_date)} "
            "ORDER BY start_time ASC"
        )
    )
    return {
        **_requested_range(start_date, end_date),
        "sessions": sessions["rows"],
        "stages": stages["rows"],
    }


@mcp.tool
async def get_vitals_summary(start_date: date | str | None = None, end_date: date | str | None = None) -> dict[str, object]:
    """Return vitals point metrics for a date range."""
    client = _new_client()
    result = await client.post_query(
        sql=(
            "SELECT record_key, metric_type, recorded_at, numeric_value, text_value, unit "
            "FROM metric_points "
            "WHERE metric_type IN ("
            "'heart_rate', 'resting_heart_rate', 'heart_rate_variability', "
            "'oxygen_saturation', 'body_temperature', 'respiratory_rate', "
            "'blood_pressure', 'blood_glucose'"
            f") AND {_date_range_clause('recorded_at', start_date, end_date)} "
            "ORDER BY recorded_at ASC"
        )
    )
    return {
        **_requested_range(start_date, end_date),
        "point_metrics": result["rows"],
    }


@mcp.tool
async def get_body_summary(start_date: date | str | None = None, end_date: date | str | None = None) -> dict[str, object]:
    """Return body metrics for a date range."""
    client = _new_client()
    result = await client.post_query(
        sql=(
            "SELECT record_key, metric_type, recorded_at, numeric_value, text_value, unit "
            "FROM metric_points "
            "WHERE metric_type IN ('weight', 'height', 'body_fat', 'lean_body_mass', 'bone_mass', 'body_water_mass') "
            f"AND {_date_range_clause('recorded_at', start_date, end_date)} "
            "ORDER BY recorded_at ASC"
        )
    )
    return {
        **_requested_range(start_date, end_date),
        "point_metrics": result["rows"],
    }


@mcp.tool
async def get_health_timeline(days: int = 14) -> dict[str, object]:
    """Return recent timeline events."""
    client = _new_client()
    return await client.get_timeline(days=days)


@mcp.tool
async def generate_doctor_visit_report(start_date: date | str | None = None, end_date: date | str | None = None) -> dict[str, object]:
    """Return the deterministic doctor-visit report."""
    client = _new_client()
    return await client.generate_doctor_visit_report(
        start_date=_parse_iso_date(start_date).isoformat() if _parse_iso_date(start_date) else None,
        end_date=_parse_iso_date(end_date).isoformat() if _parse_iso_date(end_date) else None,
        stream=False,
    )


@mcp.tool
async def ask_health_question(
    question: str,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
) -> dict[str, object]:
    """Ask the backend LLM-backed health assistant a question."""
    client = _new_client()
    return await client.ask_health_question(
        question=question,
        start_date=_parse_iso_date(start_date).isoformat() if _parse_iso_date(start_date) else None,
        end_date=_parse_iso_date(end_date).isoformat() if _parse_iso_date(end_date) else None,
    )


@mcp.tool
async def execute_health_query(sql: str) -> dict[str, object]:
    """Execute a read-only SQL query through the backend guard."""
    client = _new_client()
    return await client.post_query(sql=sql)


def main() -> None:
    _new_client()
    mcp.run()


if __name__ == "__main__":
    main()