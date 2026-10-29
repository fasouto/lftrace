"""lftrace: show a Langfuse trace as a diagram in the terminal."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import termaid
from rich.console import Console
from rich.text import Text

from .fetch import FetchError, client, fetch_observations, latest_trace_id, load_file
from .mermaid import COLORS, SYMBOLS, to_gantt, to_mermaid
from .sequence import to_sequence
from .summary import headline, issues, plain
from .tree import Observation, build_tree


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    trace_id = args.trace_id
    try:
        if args.file:
            raw = load_file(args.file)
        else:
            api = client()
            trace_id = trace_id or latest_trace_id(api)
            raw = fetch_observations(api, trace_id)
    except FetchError as e:
        print(f"lftrace: {e}", file=sys.stderr)
        return 1

    if not raw:
        where = args.file or f"trace {trace_id}"
        print(
            f"lftrace: no observations in {where} (new traces can take a few seconds to show up)",
            file=sys.stderr,
        )
        return 1

    observations = [Observation.from_dict(o) for o in raw]
    trace_id = trace_id or next((o.trace_id for o in observations if o.trace_id), None)
    roots = build_tree(observations)
    header = headline(trace_id, observations)
    problems = issues(roots)
    draw, color = VIEWS[args.view]
    source = draw(roots, max_depth=args.depth, comments=[plain(header), *(line for _, line in problems)])
    if args.mermaid:
        print(source, end="")
        return 0

    # Colors only reach a terminal; rich drops them for pipes and NO_COLOR.
    console = Console(highlight=False)
    drawing = termaid.render_rich(source, theme="mono", padding_x=2, padding_y=0, gap=3)
    color(drawing)
    console.print(styled(header), soft_wrap=True)
    console.print()
    console.print(drawing, soft_wrap=True)
    if problems:
        console.print()
        for status, line in problems:
            console.print(Text(line, style=COLORS[status]), soft_wrap=True)
    return 0


def color_symbols(drawing: Text) -> None:
    """Color the ✗ or ! that starts a status line inside a box, or a timeline row.

    Termaid 0.10.0 colors a box's border from its classDef, but not its text,
    and draws crit bars like the others. It draws a timeline's task names dim.
    """
    status_of = {symbol: status for status, symbol in SYMBOLS.items()}
    for match in re.finditer(r"(?m)(?:│|^) *([✗!]) ", drawing.plain):
        drawing.stylize(f"not dim {COLORS[status_of[match[1]]]}", *match.span(1))


def color_sequence(drawing: Text) -> None:
    """Color a status's symbol and message in a label or note, and the x that ends a failed call.

    The message follows "· " and ends at two spaces, a lifeline, a box edge, the
    end of the line or the next status. Termaid 0.10.0 doesn't color text, so this does.
    """
    status_of = {symbol: status for status, symbol in SYMBOLS.items()}
    for match in re.finditer(r"(?m)· ([✗!] .*?)(?= *(?:  |[┆│]|$| · [✗!] ))", drawing.plain):
        drawing.stylize(f"not dim {COLORS[status_of[match[1][0]]]}", *match.span(1))
    for match in re.finditer(r"(?<=[┄─])x|x(?=[┄─])", drawing.plain):
        drawing.stylize(f"not dim {COLORS['error']}", *match.span())


# Each view's writer and how its drawing is colored.
VIEWS = {
    "tree": (to_mermaid, color_symbols),
    "timeline": (to_gantt, color_symbols),
    "sequence": (to_sequence, color_sequence),
}


def styled(parts: list[tuple[str, str | None]]) -> Text:
    text = Text()
    for i, (part, status) in enumerate(parts):
        if i:
            text.append(" · ", style="dim")
        text.append(part, style=COLORS[status] if status else "bold" if i == 0 else "")
    return text


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lftrace",
        description="Show a Langfuse trace as a diagram in the terminal.",
        epilog="Credentials come from LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("trace_id", nargs="?", help="ID of the trace to show")
    source.add_argument("--latest", action="store_true", help="show the most recent trace")
    source.add_argument(
        "--file", type=Path, metavar="PATH", help="render observations saved as JSON, without network"
    )
    parser.add_argument(
        "--mermaid", action="store_true", help="print the Mermaid source instead of the drawing"
    )
    views = parser.add_mutually_exclusive_group()
    views.add_argument(
        "--timeline",
        dest="view",
        action="store_const",
        const="timeline",
        help="draw the steps on a time axis instead of as a tree",
    )
    views.add_argument(
        "--sequence",
        dest="view",
        action="store_const",
        const="sequence",
        help="draw who called whom, in the order they ran",
    )
    parser.set_defaults(view="tree")
    parser.add_argument(
        "--depth", type=positive_int, metavar="N", help="show only the first N levels of the tree"
    )
    return parser.parse_args(argv)


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


if __name__ == "__main__":
    sys.exit(main())
