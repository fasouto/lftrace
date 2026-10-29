"""Turn an observation tree into a Mermaid sequence diagram: who called whom, in the order they ran."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .mermaid import (
    MAX_CHILDREN,
    MAX_MESSAGE,
    STATUS,
    SYMBOLS,
    Step,
    comment_lines,
    format_cost,
    format_duration,
    format_tokens,
    problems,
    truncate,
    visible,
)
from .tree import Observation, descendants

# Observation types that get a lane of their own, with the group the lane is in.
# Anything else is a wrapper around its children, or local work on the caller's lane.
LANES = {
    "AGENT": "agent",
    "GENERATION": "model",
    "EMBEDDING": "model",
    "TOOL": "tool",
    "RETRIEVER": "tool",
    "GUARDRAIL": "tool",
    "EVALUATOR": "tool",
}
# Lane names by group. The groups sit left to right in this order, after the caller.
LANE_NAMES = {
    "agent": lambda obs: truncate(obs.name) or obs.type,
    "model": lambda obs: truncate(obs.model) or "LLM",
    "tool": lambda obs: "tools",
}
GROUPS = list(LANE_NAMES)

Lane = tuple[str, str]  # (group, name)


@dataclass
class Segment:
    """Siblings drawn together: one step, a run of repeats, or steps that ran at the same time."""

    kind: str  # "single", "repeat" or "parallel"
    steps: list[Step]


def to_sequence(
    roots: list[Observation], *, max_depth: int | None = None, comments: Iterable[str] = ()
) -> str:
    """Mermaid sequence diagram source: the same steps as to_mermaid(), as calls between lanes.

    A lane is an agent, a model, or the shared "tools" lane. Each call has an
    arrow and a return that carries its duration, tokens, cost and status. Steps
    that ran at the same time go in a par block and retries in a loop block,
    and long runs show their first and last steps.
    """
    items = visible(roots, max_depth=max_depth, max_children=None)
    return Diagram([item for item in items if isinstance(item, Step)]).source(comments)


class Diagram:
    def __init__(self, steps: list[Step]) -> None:
        self.children: dict[Step | None, list[Step]] = {}
        for step in steps:
            self.children.setdefault(step.parent, []).append(step)
        self.ids: dict[Lane, str] = {}
        self.lines: list[str] = []
        self.depth = 1

    def source(self, comments: Iterable[str]) -> str:
        top = self.children.get(None, [])
        if len(top) == 1 and self.children.get(top[0]):
            root = top[0].obs
            name = truncate(root.name) or root.type
            caller = lane_key(root) if root.type == "AGENT" else ("caller", name)
            self.body(self.lane(caller), self.children[top[0]])
            if status := status_part(root):
                self.add(f"Note over {self.lane(caller)}: {escape(f'{name} · {status}')}")
        else:
            caller = ("caller", "trace")
            self.body(self.lane(caller), top)
        ordered = sorted(
            self.ids, key=lambda key: (key != caller, GROUPS.index(key[0]) if key[0] in GROUPS else 0)
        )
        participants = [f"  participant {self.ids[key]} as {escape(key[1])}" for key in ordered]
        return "\n".join(["sequenceDiagram", *comment_lines(comments), *participants, *self.lines]) + "\n"

    def lane(self, key: Lane) -> str:
        return self.ids.setdefault(key, f"p{len(self.ids)}")

    def add(self, line: str) -> None:
        self.lines.append("  " * self.depth + line)

    def message(self, source: str, arrow: str, target: str, text: str) -> None:
        self.add(f"{source}{arrow}{target}: {escape(text)}")

    def note(self, caller: str, hidden: list[Step], text: str) -> None:
        parts = [text, *problems(o for step in hidden for o in subtree(step))]
        self.add(f"Note over {caller}: {escape(' · '.join(parts))}")

    def open(self, head: str, label: str | None = None) -> None:
        self.add(f"{head} {escape(label)}" if label else head)
        self.depth += 1

    def branch(self, label: str | None = None) -> None:
        self.depth -= 1
        self.add(f"and {escape(label)}" if label else "and")
        self.depth += 1

    def close(self) -> None:
        self.depth -= 1
        self.add("end")

    def mode(self, step: Step) -> str:
        """ "call" for a step with a lane, "inline" for a wrapper, "local" for work on the caller's lane."""
        if step.obs.type in LANES:
            return "call"
        if self.children.get(step) and not STATUS.get(step.obs.level or ""):
            return "inline"
        return "local"

    def body(self, caller: str, steps: list[Step], wrapper: str | None = None) -> None:
        for segment in segments(steps):
            if segment.kind == "repeat":
                self.repeat(caller, segment.steps)
            elif segment.kind == "parallel":
                self.parallel(caller, segment.steps, wrapper)
            else:
                self.step(caller, segment.steps[0])

    def step(
        self,
        caller: str,
        step: Step,
        *,
        text: str | None = None,
        parts: list[str] | None = None,
        failed: bool | None = None,
    ) -> None:
        """Draw one step with whatever it ran. The overrides draw a fold of several steps as this one."""
        obs, kids, mode = step.obs, self.children.get(step, []), self.mode(step)
        name = truncate(obs.name) or obs.type
        if mode == "inline":
            self.body(caller, kids, name)
            return
        text = text or title(step)
        parts = step_parts(step) if parts is None else parts
        failed = obs.level == "ERROR" if failed is None else failed
        if mode == "local":
            self.message(caller, "-x" if failed else "->>", caller, " · ".join([text, *parts]))
            self.body(caller, kids, name)
            return
        callee = self.lane(lane_key(obs))
        self.message(caller, "->>", callee, text)
        self.body(callee, kids)
        self.message(callee, "--x" if failed else "-->>", caller, " · ".join(parts))

    def repeat(self, caller: str, steps: list[Step]) -> None:
        failed = sum(any(o.level == "ERROR" for o in subtree(step)) for step in steps)
        name = truncate(steps[0].obs.name) or steps[0].obs.type
        self.open("loop", f"{name} ×{len(steps)}" + (f", {failed} failed" if failed else ""))
        self.run(caller, steps)
        self.close()

    def parallel(self, caller: str, steps: list[Step], wrapper: str | None) -> None:
        if len(steps) > 2 and len({kind(s) for s in steps}) == 1 and "inline" not in map(self.mode, steps):
            self.fold(caller, steps)
            return
        for i, item in enumerate(shown(steps)):
            inline = isinstance(item, Step) and self.mode(item) == "inline"
            label = title(item) if inline else wrapper if i == 0 and isinstance(item, Step) else None
            if i == 0:
                self.open("par", label)
            else:
                self.branch(label)
            if inline:
                self.body(caller, self.children[item])  # the branch label names the wrapper already
            else:
                self.draw(caller, item)
        self.close()

    def run(self, caller: str, steps: list[Step]) -> None:
        """Draw steps in turn. Past MAX_CHILDREN the middle ones become a note."""
        for item in shown(steps):
            self.draw(caller, item)

    def draw(self, caller: str, item: Step | list[Step]) -> None:
        if isinstance(item, list):
            self.note(caller, item, f"+{len(item)} more")
        else:
            self.step(caller, item)

    def fold(self, caller: str, steps: list[Step]) -> None:
        """Steps of one kind that ran together, drawn as a single call: the first with a problem, or the first."""
        first, last = steps[0], steps[-1]
        durations = [s.obs.duration for s in steps if s.obs.duration is not None]
        parts = [
            format_duration(max(durations)) if durations else "no end time",
            *problems(o for step in steps for o in subtree(step)),
        ]
        name = truncate(first.obs.name) or first.obs.type
        self.step(
            caller,
            next((s for s in steps if problems(subtree(s))), first),
            text=f"{first.number}–{last.number}. {name} ×{len(steps)}",
            parts=parts,
            failed=any(s.obs.level == "ERROR" for s in steps),
        )


def segments(steps: list[Step]) -> list[Segment]:
    """Group siblings, in start order, into parallel runs, repeats and single steps.

    A step that starts before the latest end of the current group joins it, and
    one without an end time doesn't push that end later. A step without a start
    never joins. Consecutive steps that don't overlap and share a name and type
    are repeats.
    """
    groups: list[list[Step]] = []
    latest = None
    for step in steps:
        start, end = step.obs.start_time, step.obs.end_time
        if groups and start and latest and start < latest:
            groups[-1].append(step)
            latest = max(latest, end) if end else latest
        else:
            groups.append([step])
            latest = end
    result: list[Segment] = []
    for group in groups:
        previous = result[-1] if result else None
        if len(group) > 1:
            result.append(Segment("parallel", group))
        elif previous and previous.kind != "parallel" and kind(previous.steps[-1]) == kind(group[0]):
            previous.kind = "repeat"
            previous.steps.append(group[0])
        else:
            result.append(Segment("single", group))
    return result


def shown(steps: list[Step]) -> list[Step | list[Step]]:
    """The steps to draw: all of them, or the first and last with the middle ones as one list."""
    if len(steps) > MAX_CHILDREN:
        return [steps[0], steps[1:-1], steps[-1]]
    return list(steps)


def title(step: Step) -> str:
    name = truncate(step.obs.name) or step.obs.type
    return f"{step.number}. {name}" if step.number else name


def kind(step: Step) -> tuple[str | None, str]:
    return (step.obs.name, step.obs.type)


def lane_key(obs: Observation) -> Lane:
    group = LANES[obs.type]
    return (group, LANE_NAMES[group](obs))


def subtree(step: Step) -> list[Observation]:
    """The step and everything under it, including what --depth folded away."""
    return [step.obs, *descendants(step.obs)]


def step_parts(step: Step) -> list[str]:
    """What a return or self-message says after the name: duration, tokens, cost, folds and status."""
    obs = step.obs
    parts = []
    if obs.type != "EVENT":
        parts.append(format_duration(obs.duration) if obs.duration is not None else "no end time")
    parts += [x for x in (format_tokens(obs.usage), format_cost(obs.total_cost)) if x]
    if step.nested:
        parts += [f"+{len(step.nested)} nested", *problems(step.nested)]
    if status := status_part(obs):
        parts.append(status)
    return parts


def status_part(obs: Observation) -> str | None:
    """The symbol and message of an error or warning, or None."""
    if status := STATUS.get(obs.level or ""):
        return f"{SYMBOLS[status]} {truncate(obs.status_message, MAX_MESSAGE) or status}"
    return None


def escape(text: str) -> str:
    """Make text safe in a message, alias, block label or note, as Mermaid and Termaid read it.

    Termaid 0.10.0 doesn't decode entity codes in sequence diagrams, so where
    Mermaid would read markup this writes a look-alike character.
    """
    text = " ".join(text.split())  # a newline would end the statement
    text = text.replace(";", ",")  # Mermaid ends the statement at any ";"
    text = text.replace("#", "♯")  # Mermaid starts a comment at "#"
    text = re.sub(r"^((?:no)?wrap):", r"\1∶", text, flags=re.IGNORECASE)  # Mermaid reads these as a mode
    text = re.sub(r"<(?=br\s*/?>)", "‹", text, flags=re.IGNORECASE)  # Termaid shows <br> as text
    return re.sub(r"%(?=%)", "% ", text)
