"""Turn the flat list of observations in a trace into a tree."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Observation:
    id: str
    type: str
    name: str | None = None
    trace_id: str | None = None
    parent_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    level: str | None = None
    status_message: str | None = None
    model: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    total_cost: float | None = None
    children: list[Observation] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Observation:
        """Build from one item of /api/public/v2/observations (camelCase keys)."""
        return cls(
            id=data["id"],
            type=str(data.get("type") or "SPAN").upper(),
            name=data.get("name"),
            trace_id=data.get("traceId"),
            parent_id=data.get("parentObservationId"),
            start_time=_parse_time(data.get("startTime")),
            end_time=_parse_time(data.get("endTime")),
            level=data.get("level"),
            status_message=data.get("statusMessage"),
            model=data.get("model"),
            usage=data.get("usageDetails") or {},
            total_cost=data.get("totalCost"),
        )

    @property
    def duration(self) -> float | None:
        """Duration in seconds, or None if the observation has no end time."""
        if self.start_time is None or self.end_time is None:
            return None
        return (self.end_time - self.start_time).total_seconds()


def build_tree(observations: list[Observation]) -> list[Observation]:
    """Attach each observation to its parent and return the roots.

    Siblings are ordered by start time. An observation whose parent isn't in
    the list (for example, still being ingested) becomes a root.
    """
    by_id = {obs.id: obs for obs in observations}
    roots: list[Observation] = []
    for obs in observations:
        obs.children = []
    for obs in observations:
        parent = by_id.get(obs.parent_id) if obs.parent_id else None
        if parent is None or parent is obs:
            roots.append(obs)
        else:
            parent.children.append(obs)
    _sort(roots)
    return roots


def descendants(obs: Observation) -> Iterator[Observation]:
    for child in obs.children:
        yield child
        yield from descendants(child)


def walk(roots: list[Observation]) -> Iterator[Observation]:
    """Every observation in the tree, each one before its children."""
    for root in roots:
        yield root
        yield from descendants(root)


def number_steps(roots: list[Observation]) -> dict[str, str]:
    """Outline numbers in execution order (1, 2, 2.1, 2.2, 3), keyed by id.

    A single root stands for the whole trace and gets no number, so the
    steps under it are 1, 2, 3.
    """
    numbers: dict[str, str] = {}

    def visit(observations: list[Observation], prefix: str) -> None:
        for i, obs in enumerate(observations, 1):
            numbers[obs.id] = f"{prefix}{i}"
            visit(obs.children, f"{prefix}{i}.")

    visit(roots[0].children if len(roots) == 1 else roots, "")
    return numbers


def _sort(observations: list[Observation]) -> None:
    observations.sort(key=_start_key)
    for obs in observations:
        _sort(obs.children)


def _start_key(obs: Observation) -> tuple[bool, float, str]:
    # Observations without a start time go last; the id makes ties deterministic.
    start = obs.start_time.timestamp() if obs.start_time else 0.0
    return (obs.start_time is None, start, obs.id)


def _parse_time(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    # fromisoformat() only accepts a trailing "Z" from Python 3.11 on.
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
