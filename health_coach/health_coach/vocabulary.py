"""Live-verified vocabulary for the HealthQuery read API.

The vocabulary tables below are sourced from STA-48's live introspection
pass (run id ``dc49fede-a6cb-4682-97e4-919400263b61``) and the plan rev 3
``§11.5`` live evidence. They are the *narrow* set of values the
operator's HealthQuery currently carries — narrower than the schema
*permits*.

Keeping these tables in one place means the analyzer never has to guess
about a ``metric_type`` string, a sleep ``stage_type`` label, or a
``workouts.activity_type`` integer code. When the operator's HealthQuery
begins emitting a wider vocabulary (the 8-class sleep set, additional
metric types, etc.), the tables here grow before the analyzer does.
"""

from __future__ import annotations

# The distinct ``metric_type`` strings the analyzer can expect to find
# in ``metric_points`` for the operator's HealthQuery today (STA-48).
# All other Health Connect vitals the schema permits are listed under
# :data:`ABSENT_METRIC_TYPES` so the analyzer renders a "data not
# available" note rather than fabricating a value.
KNOWN_METRIC_TYPES: frozenset[str] = frozenset(
    {
        "heart_rate",
        "oxygen_saturation",
        "resting_heart_rate",
    }
)

# The distinct ``metric_type`` strings the analyzer can expect to find
# in ``metric_intervals`` for the operator's HealthQuery today.
KNOWN_INTERVAL_TYPES: frozenset[str] = frozenset(
    {
        "steps",
        "distance",
        "total_calories",
    }
)

# Sleep stage vocabulary in live use today (4-class set observed by
# STA-48; the wider 8-class set is permitted by the schema but the
# operator's Health Connect does not yet emit it).
KNOWN_SLEEP_STAGES: frozenset[str] = frozenset(
    {
        "awake",
        "light",
        "deep",
        "rem",
    }
)

# Distinct metric types the AGENTS.md output bar requires the summary
# to mention, but the operator's HealthQuery does not yet carry.
# ``data not available`` notes are emitted for these so the reader does
# not infer a missing-trend signal.
ABSENT_METRIC_TYPES: frozenset[str] = frozenset(
    {
        # Vitals
        "heart_rate_variability",
        "body_temperature",
        "respiratory_rate",
        "blood_pressure",
        "blood_glucose",
        # Body comp (per AGENTS.md §"Output bar")
        "weight",
        "height",
        "body_fat",
        "lean_body_mass",
        "bone_mass",
        "body_water_mass",
        # Intervals
        "active_calories",
        "hydration",
        "nutrition",
        "mindfulness",
    }
)

# Health Connect ``ACTIVITY_TYPE_*`` integer codes observed in
# ``workouts.activity_type``. The companion app or HealthQuery's
# mapping layer should translate these to lowercase snake_case before
# insert, but it does not. ``UNKNOWN`` is the safe label for any code
# not in this table; never invent a label.
WORKOUT_CODE_TO_LABEL: dict[str, str] = {
    "1": "bike",
    "8": "running",
    "9": "walking",
    "10": "hiking",
    "11": "running_treadmill",
    "37": "elliptical",
    "48": "strength_training",
    "52": "yoga",
    "55": "pilates",
    "71": "swimming",
    "74": "rowing",
    "79": "other",
}

_UNKNOWN_WORKOUT_LABEL = "unknown"


def label_workout_code(code: str | int | None) -> str:
    """Map a Health Connect ``activity_type`` integer code to a label.

    Returns ``"unknown"`` for codes not in
    :data:`WORKOUT_CODE_TO_LABEL`. The caller may render the raw code
    alongside the label if disclosure is appropriate (the summary body
    does not, for now).
    """
    if code is None:
        return _UNKNOWN_WORKOUT_LABEL
    key = str(code).strip()
    return WORKOUT_CODE_TO_LABEL.get(key, _UNKNOWN_WORKOUT_LABEL)


def describe_vocabulary() -> dict[str, list[str] | int]:
    """Return a small JSON-serializable snapshot of the live vocabulary.

    Used by the report header so the operator can verify the analyzer
    is reading the same vocabulary the introspection pass recorded.

    The per-stage sleep labels are intentionally **not** included —
    STA-53 privacy review strips them from the report body even as
    schema metadata. The count is enough to confirm the analyzer's
    mapping table is the same length the operator's HealthQuery
    currently emits, without enumerating the labels in narrative.
    """
    return {
        "metric_point_types": sorted(KNOWN_METRIC_TYPES),
        "metric_interval_types": sorted(KNOWN_INTERVAL_TYPES),
        "absent_metric_types": sorted(ABSENT_METRIC_TYPES),
        "workout_codes_mapped": len(WORKOUT_CODE_TO_LABEL),
        "sleep_stage_label_count": len(KNOWN_SLEEP_STAGES),
    }
