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
from .mermaid import COLORS, SYMBOLS, to_mermaid
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
    source = to_mermaid(
        roots, max_depth=args.depth, comments=[plain(header), *(line for _, line in problems)]
    )
    if args.mermaid:
        print(source, end="")
        return 0

    # Colors only reach a terminal; rich drops them for pipes and NO_COLOR.
    console = Console(highlight=False)
    drawing = termaid.render_rich(source, theme="mono", padding_x=2, padding_y=0, gap=3)
    color_symbols(drawing)
    console.print(styled(header), soft_wrap=True)
    console.print()
    console.print(drawing, soft_wrap=True)
    if problems:
        console.print()
        for status, line in problems:
            console.print(Text(line, style=COLORS[status]), soft_wrap=True)
    return 0


def color_symbols(drawing: Text) -> None:
    """Color the ✗ or ! that starts a status line inside a box.

    Termaid 0.9.1 colors a box's border from its classDef, but not its text.
    """
    status_of = {symbol: status for status, symbol in SYMBOLS.items()}
    for match in re.finditer(r"│ *([✗!]) ", drawing.plain):
        drawing.stylize(COLORS[status_of[match[1]]], *match.span(1))


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
