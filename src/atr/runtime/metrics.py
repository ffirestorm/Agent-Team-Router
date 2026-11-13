"""Attempt-level model summaries for engineering reports."""

import math


_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens")
_NOTE = (
    "Statistics describe attempts, not independent task outcomes. "
    "Task difficulty is unmatched across models, so comparisons do not establish causation."
)


def _known_number(value):
    """Accept finite nonnegative JSON-style numbers, excluding booleans."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    return isinstance(value, float) and math.isfinite(value) and value >= 0


def _new_row(model):
    return {
        "model": model,
        "tasks": 0,
        "attempts": 0,
        "succeeded_attempts": 0,
        "attempt_success_rate": 0,
        "failures": {},
        "upgraded_attempts": 0,
        "succeeded_upgraded_attempts": 0,
        "elapsed_seconds": 0,
        "seconds_missing_attempts": 0,
        "cost": 0,
        "cost_missing_attempts": 0,
        "token_usage": {
            key: {"known_subtotal": 0, "missing_attempts": 0, "total": None}
            for key in _TOKEN_FIELDS
        },
    }


def summarize_models(records):
    """Summarize valid attempts by model without modifying the supplied records."""
    rows = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("attempts"), list):
            continue
        seen = set()
        for attempt in record["attempts"]:
            if not isinstance(attempt, dict):
                continue
            model = attempt.get("model")
            if not isinstance(model, str) or not model:
                model = "unknown"
            row = rows.setdefault(model, _new_row(model))
            if model not in seen:
                row["tasks"] += 1
                seen.add(model)
            row["attempts"] += 1
            succeeded = attempt.get("status") == "succeeded"
            row["succeeded_attempts"] += succeeded
            if attempt.get("kind") == "escalation":
                row["upgraded_attempts"] += 1
                row["succeeded_upgraded_attempts"] += succeeded
            failure = attempt.get("failure")
            if isinstance(failure, str) and failure:
                row["failures"][failure] = row["failures"].get(failure, 0) + 1
            seconds = attempt.get("seconds")
            if _known_number(seconds):
                row["elapsed_seconds"] += seconds
            else:
                row["seconds_missing_attempts"] += 1
            usage = attempt.get("usage")
            if not isinstance(usage, dict):
                usage = {}
            cost = usage.get("cost")
            if _known_number(cost):
                row["cost"] += cost
            else:
                row["cost_missing_attempts"] += 1
            for key in _TOKEN_FIELDS:
                value = usage.get(key)
                field = row["token_usage"][key]
                if _known_number(value):
                    field["known_subtotal"] += value
                else:
                    field["missing_attempts"] += 1
    for row in rows.values():
        row["attempt_success_rate"] = row["succeeded_attempts"] / row["attempts"]
        if row["cost_missing_attempts"]:
            row["cost"] = None
        for field in row["token_usage"].values():
            if not field["missing_attempts"]:
                field["total"] = field["known_subtotal"]
    return {"models": [rows[model] for model in sorted(rows)], "note": _NOTE}
