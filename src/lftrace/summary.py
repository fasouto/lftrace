"""The header line with a trace's totals, and the list of its errors and warnings."""

from __future__ import annotations

from .mermaid import STATUS, SYMBOLS, format_cost, format_duration, total_tokens
from .tree import Observation, number_steps, walk


def headline(trace_id: str | None, observations: list[Observation]) -> list[tuple[str, str | None]]:
    """Header parts as (text, status) pairs, for example ("1 error", "error")."""
    parts: list[tuple[str, str | None]] = [(f"Trace {trace_id}" if trace_id else "Trace", None)]
    starts = [o.start_time for o in observations if o.start_time]
    ends = [o.end_time for o in observations if o.end_time]
    if starts and ends:
        parts.append((format_duration((max(ends) - min(starts)).total_seconds()), None))
    tokens = sum(total_tokens(o.usage) or 0 for o in observations)
    parts.append((f"{tokens:,} token{'' if tokens == 1 else 's'}", None))
    parts.append((format_cost(sum(o.total_cost or 0 for o in observations)) or "$0.00", None))
    for level, status in STATUS.items():
        if count := sum(o.level == level for o in observations):
            parts.append((f"{count} {status}{'s' if count > 1 else ''}", status))
    return parts


def issues(roots: list[Observation]) -> list[tuple[str, str]]:
    """(status, line) for every error and warning, in execution order.

    Lines carry the full status message and the step number from the diagram,
    including steps the diagram collapsed.
    """
    numbers = number_steps(roots)
    result = []
    for obs in walk(roots):
        if status := STATUS.get(obs.level or ""):
            step = obs.name or obs.type
            if obs.id in numbers:
                step = f"{numbers[obs.id]}. {step}"
            message = " ".join((obs.status_message or "").split()) or f"no {status} message"
            result.append((status, f"{SYMBOLS[status]} {step} ({obs.type}): {message}"))
    return result


def plain(parts: list[tuple[str, str | None]]) -> str:
    return " · ".join(text for text, _ in parts)
