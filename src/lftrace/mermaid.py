"""Turn an observation tree into a left-to-right Mermaid flowchart, or a gantt chart of when the steps ran."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from .tree import Observation, descendants, number_steps, walk

# Termaid 0.10.0 breaks label lines on a literal "\n" and shows Mermaid's
# standard <br> as text, so we use "\n". Switch to "<br>" once Termaid reads it.
LINE_BREAK = "\\n"

# Siblings shown before the middle ones collapse into "+N more". Termaid 0.10.0
# draws crossing lines when a node has more than three children.
MAX_CHILDREN = 3
MAX_NAME = 40
MAX_MESSAGE = 30  # the full message is listed under the diagram
# Termaid 0.10.0 draws gantt charts 80 columns wide, and the longest task name
# takes its width from the bars.
MAX_TASK_NAME = 24

STATUS = {"ERROR": "error", "WARNING": "warning"}
SYMBOLS = {"error": "✗", "warning": "!"}
COLORS = {"error": "#e5484d", "warning": "#f5a524"}

# Tick intervals as Mermaid writes them, from the finest, with their length in
# milliseconds. Each count divides the next unit, so ticks land on round times.
TICKS = [
    (count * size, f"{count}{unit}")
    for unit, size, counts in [
        ("millisecond", 1, (1, 2, 5, 10, 20, 50, 100, 200, 500)),
        ("second", 1000, (1, 2, 5, 10, 15, 30)),
        ("minute", 60_000, (1, 2, 5, 10, 15, 30)),
        ("hour", 3_600_000, (1, 2, 3, 6, 12)),
    ]
    for count in counts
]

# A gantt line that starts like this isn't a task for Mermaid 11 or Termaid
# 0.10.0, whatever follows. Lines start with a step number or a status symbol,
# except for a single root's, which has no number.
KEYWORD = re.compile(
    r".?%|\d{4}-\d\d-\d\d|gantt|inclusiveenddates|topaxis|accdescr|href"
    r"|(title|section|dateformat|axisformat|tickinterval|includes|excludes|todaymarker|weekday|weekend"
    r"|vert|click|call)\s",
    re.IGNORECASE,
)


@dataclass(eq=False)  # identity hashing: to_mermaid keys node ids by item
class Step:
    """A step the diagram shows. Past max_depth its descendants fold into it."""

    obs: Observation
    number: str | None  # None only for a single root, which stands for the trace
    nested: list[Observation]  # descendants folded away by max_depth
    parent: Step | None


@dataclass(eq=False)
class More:
    """Siblings collapsed into one "+N more" item."""

    steps: list[Observation]
    parent: Step | None

    @property
    def hidden(self) -> list[Observation]:
        """The collapsed siblings and everything under them."""
        return [o for step in self.steps for o in (step, *descendants(step))]


def visible(
    roots: list[Observation], *, max_depth: int | None, max_children: int | None
) -> list[Step | More]:
    """What a diagram shows of the tree, each item before its children. Roots are at depth 1."""
    numbers = number_steps(roots)
    items: list[Step | More] = []

    def add_level(observations: list[Observation], parent: Step | None, depth: int) -> None:
        for item in collapse(observations, max_children):
            if isinstance(item, list):
                items.append(More(item, parent))
                continue
            expand = max_depth is None or depth < max_depth
            step = Step(item, numbers.get(item.id), [] if expand else list(descendants(item)), parent)
            items.append(step)
            if expand:
                add_level(item.children, step, depth + 1)

    add_level(roots, None, 1)
    return items


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
    lines = ["graph LR", *comment_lines(comments)]
    items = visible(roots, max_depth=max_depth, max_children=max_children)
    ids = {item: f"n{i}" for i, item in enumerate(items)}
    fan_outs: dict[Step, list[str]] = {}  # each parent with its children's ids
    used: set[str] = set()
    for item in items:
        if isinstance(item, More):
            parts = [f"+{len(item.steps)} more", *problems(item.hidden)]
            status, open_, close = worst(item.hidden), "(", ")"
        else:
            parts = label(item.obs, item.number)
            if item.nested:
                parts += [f"+{len(item.nested)} nested", *problems(item.nested)]
            status, open_, close = STATUS.get(item.obs.level or ""), "[", "]"
        text = LINE_BREAK.join(escape(part) for part in parts)
        lines.append(f'  {ids[item]}{open_}"{text}"{close}' + (f":::{status}" if status else ""))
        if status:
            used.add(status)
        if item.parent:
            fan_outs.setdefault(item.parent, []).append(ids[item])
    for parent, children in fan_outs.items():
        lines += [f"  {ids[parent]} --> {child}" for child in middle_first(children)]
    for status in sorted(used):
        lines.append(f"  classDef {status} stroke:{COLORS[status]},color:{COLORS[status]}")
    return "\n".join(lines) + "\n"


def to_gantt(
    roots: list[Observation],
    *,
    max_depth: int | None = None,
    max_children: int | None = None,
    comments: Iterable[str] = (),
) -> str:
    """Mermaid gantt source: the same steps as to_mermaid(), as bars on a time axis.

    Times are milliseconds since the trace started, read as Unix times
    (dateFormat x), so the axis shows elapsed time. A step without an end runs
    to the end of the chart (the latest start or end) as an active bar, and an event is a milestone.
    """
    observations = list(walk(roots))
    starts = [o.start_time for o in observations if o.start_time]
    if not starts:
        return "\n".join(["gantt", *comment_lines(comments)]) + "\n"
    start = min(starts)
    end = max([*starts, *(o.end_time for o in observations if o.end_time)])

    def ms(t: datetime) -> int:
        return round((t - start).total_seconds() * 1000)

    lines = ["gantt", *comment_lines(comments), "  dateFormat x", *axis(ms(end))]
    for item in visible(roots, max_depth=max_depth, max_children=max_children):
        if isinstance(item, More):
            covered, status, name = item.hidden, worst(item.hidden), f"+{len(item.steps)} more"
        else:
            covered, status = [item.obs], worst([item.obs, *item.nested])
            name = escape_task(truncate(item.obs.name, MAX_TASK_NAME) or item.obs.type)
            if item.number:
                name = f"{item.number}. {name}"
            elif KEYWORD.match(name):
                name = f"{item.obs.type} {name}"
            if item.nested:
                name += f" +{len(item.nested)} nested"
        starts = [o.start_time for o in covered if o.start_time]
        if not starts:
            continue  # nothing to place it by
        ends = [o.end_time for o in covered if o.end_time]
        tags = ["crit"] if status == "error" else []
        if isinstance(item, Step) and item.obs.type == "EVENT":
            tags.append("milestone")
            ends = starts
        elif not ends:
            tags.append("active")
            ends = [end]
        if status:
            name = f"{SYMBOLS[status]} {name}"
        begin, finish = ms(min(starts)), ms(max(ends))
        lines.append(f"  {name} :{''.join(f'{tag}, ' for tag in tags)}{begin}, {finish - begin}ms")
    return "\n".join(lines) + "\n"


def comment_lines(comments: Iterable[str]) -> list[str]:
    # Termaid 0.10.0 splits flowchart lines on ";" before it drops comments, so
    # the rest of a comment after a ";" would be parsed as nodes.
    return [f"%% {' '.join(c.split())}".replace(";", ",") for c in comments]


def axis(span_ms: int) -> list[str]:
    """axisFormat and tickInterval lines: up to five ticks on round times, read as elapsed time.

    Termaid 0.10.0 spreads its default ticks evenly but labels them with whole
    units, so the labels drift from the bars. Ticks at a set interval sit at
    their times.
    """
    step, interval = next(((ms, i) for ms, i in TICKS if span_ms / ms <= 5), TICKS[-1])
    # %Q and %s count from 1970, where the chart starts. Mermaid draws %M and %H
    # in the viewer's timezone, so those labels shift in a browser.
    if step < 1000:
        fmt = "%Qms"
    elif step < 60_000:
        fmt = "%ss"
    else:
        fmt = "%M:%S" if span_ms < 3_600_000 else "%H:%M"
    return [f"  axisFormat {fmt}", f"  tickInterval {interval}"]


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


def collapse(items: list[Observation], max_children: int | None) -> list[Observation | list[Observation]]:
    """Keep the first and last items of a long list; group the middle ones. None keeps them all."""
    if max_children is None or len(items) <= max_children:
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


def escape_task(text: str) -> str:
    """Make text safe as a gantt task name, as Mermaid and Termaid read it.

    Termaid 0.10.0 doesn't decode entity codes in gantt charts, so where escape()
    writes a code this swaps in a look-alike or a space.
    """
    text = " ".join(text.split())  # a newline would end the task
    text = text.replace(":", "∶")  # the first ":" ends the name
    text = re.sub(r"%(?=%)", "% ", text)  # Termaid 0.10.0 starts a comment at any %%
    return re.sub(r"#(?=\w+;)", "# ", text)  # Mermaid would decode it


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
