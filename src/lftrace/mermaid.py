"""Turn an observation tree into a left-to-right Mermaid flowchart."""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable

from .tree import Observation, descendants, number_steps

# Termaid 0.10.0 breaks label lines on a literal "\n" and shows Mermaid's
# standard <br> as text, so we use "\n". Switch to "<br>" once Termaid reads it.
LINE_BREAK = "\\n"

# Siblings shown before the middle ones collapse into "+N more". Termaid 0.10.0
# draws crossing lines when a node has more than three children.
MAX_CHILDREN = 3
MAX_NAME = 40
MAX_MESSAGE = 30  # the full message is listed under the diagram

STATUS = {"ERROR": "error", "WARNING": "warning"}
SYMBOLS = {"error": "✗", "warning": "!"}
COLORS = {"error": "#e5484d", "warning": "#f5a524"}


def to_mermaid(
    roots: list[Observation],
    *,
    max_depth: int | None = None,
    max_children: int = MAX_CHILDREN,
    comments: Iterable[str] = (),
) -> str:
    """Mermaid source for the tree, drawn left to right. Roots are at depth 1.

    Left to right, siblings stack down the terminal and only depth adds width,
    so even long traces fit.

    Comments go right after the header as %% lines, so they travel with the
    source without changing the drawing. The edges come after all the nodes.
    """
    # Termaid 0.10.0 splits lines on ";" before it drops comments, so the rest
    # of a comment after a ";" would be parsed as nodes.
    lines = ["graph LR", *(f"%% {' '.join(c.split())}".replace(";", ",") for c in comments)]
    numbers = number_steps(roots)
    ids = (f"n{i}" for i in itertools.count())
    used: set[str] = set()
    fan_outs: list[tuple[str, list[str]]] = []  # each parent with its children's ids

    def add_node(parts: list[str], status: str | None, more: bool = False) -> str:
        node_id = next(ids)
        open_, close = ("(", ")") if more else ("[", "]")
        text = LINE_BREAK.join(escape(part) for part in parts)
        lines.append(f'  {node_id}{open_}"{text}"{close}' + (f":::{status}" if status else ""))
        if status:
            used.add(status)
        return node_id

    def add_level(observations: list[Observation], parent: str | None, depth: int) -> None:
        children: list[str] = []
        if parent:
            fan_outs.append((parent, children))
        for item in collapse(observations, max_children):
            if isinstance(item, list):
                hidden = [o for h in item for o in (h, *descendants(h))]
                children.append(add_node([f"+{len(item)} more", *problems(hidden)], worst(hidden), more=True))
                continue
            parts = label(item, numbers.get(item.id))
            expand = max_depth is None or depth < max_depth
            if item.children and not expand:
                hidden = list(descendants(item))
                parts += [f"+{len(hidden)} nested", *problems(hidden)]
            node_id = add_node(parts, STATUS.get(item.level or ""))
            children.append(node_id)
            if expand:
                add_level(item.children, node_id, depth + 1)

    add_level(roots, None, 1)
    for parent, children in fan_outs:
        lines += [f"  {parent} --> {child}" for child in middle_first(children)]
    for status in sorted(used):
        lines.append(f"  classDef {status} stroke:{COLORS[status]},color:{COLORS[status]}")
    return "\n".join(lines) + "\n"


def label(obs: Observation, number: str | None = None) -> list[str]:
    """Label lines: the step, its type and duration, LLM numbers, then status."""
    name = truncate(obs.name) or obs.type
    parts = [f"{number}. {name}" if number else name]
    if obs.duration is None or obs.type == "EVENT":
        parts.append(obs.type)
    else:
        parts.append(f"{obs.type} · {format_duration(obs.duration)}")
    if obs.model:
        parts.append(truncate(obs.model))
    usage = [x for x in (format_tokens(obs.usage), format_cost(obs.total_cost)) if x]
    if usage:
        parts.append(" · ".join(usage))
    if status := STATUS.get(obs.level or ""):
        parts.append(f"{SYMBOLS[status]} {truncate(obs.status_message, MAX_MESSAGE) or status}")
    return parts


def middle_first(items: list[str]) -> list[str]:
    """Order a fan-out's edges from the middle child outwards.

    Termaid 0.10.0 routes edges in the order they're listed. When the middle edge
    goes first it runs straight, and the others branch off it without detours.
    """
    center = (len(items) - 1) / 2
    return [item for _, item in sorted(enumerate(items), key=lambda pair: abs(pair[0] - center))]


def worst(observations: Iterable[Observation]) -> str | None:
    levels = {o.level for o in observations}
    return "error" if "ERROR" in levels else "warning" if "WARNING" in levels else None


def problems(observations: Iterable[Observation]) -> list[str]:
    """Count lines for errors and warnings hidden in a collapsed part."""
    levels = [o.level for o in observations]
    lines = []
    for level, status in STATUS.items():
        if count := levels.count(level):
            lines.append(f"{SYMBOLS[status]} {count} {status}{'s' if count > 1 else ''}")
    return lines


def collapse(items: list[Observation], max_children: int) -> list[Observation | list[Observation]]:
    """Keep the first and last items of a long list; group the middle ones."""
    if len(items) <= max_children:
        return list(items)
    keep = max_children - 1  # one slot goes to the "+N more" node
    head, tail = keep - keep // 2, keep // 2
    end = len(items) - tail
    return [*items[:head], items[head:end], *items[end:]]


def escape(text: str) -> str:
    """Make text safe inside a double-quoted label, as Mermaid and Termaid read it.

    What either would read as markup becomes a decimal entity code, which both
    decode: quotes, the start of an HTML tag or of a Markdown label, and anything
    that looks like an entity code already, so it shows as written.
    """
    text = " ".join(text.split())  # a newline would end the statement
    # Termaid 0.10.0 decodes entity codes before it breaks lines on a literal
    # "\n", so even "#92;n" would become a line break.
    text = text.replace("\\", "/")
    return re.sub(r'^`|["<]|[#&](?=\w+;)', lambda m: f"#{ord(m[0])};", text)


def truncate(text: str | None, limit: int = MAX_NAME) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, seconds = divmod(round(seconds), 60)
    return f"{minutes}m {seconds:02d}s"


def total_tokens(usage: dict[str, int]) -> int | None:
    total = usage.get("total")
    if total is None and ("input" in usage or "output" in usage):
        total = usage.get("input", 0) + usage.get("output", 0)
    return total


def format_tokens(usage: dict[str, int]) -> str | None:
    total = total_tokens(usage)
    return f"{total:,} tok" if total else None


def format_cost(cost: float | None) -> str | None:
    if not cost:
        return None
    if cost >= 0.01:
        return f"${cost:.2f}"
    # Two significant digits without scientific notation: $0.0004, $0.000097
    return f"${float(f'{cost:.2g}'):.6f}".rstrip("0").rstrip(".")
