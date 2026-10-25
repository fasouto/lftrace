from datetime import datetime, timedelta

import pytest
import termaid

from lftrace.mermaid import axis, to_gantt, to_mermaid
from lftrace.tree import Observation

T0 = datetime(2026, 1, 1)


def rows(source: str) -> list[tuple[str, str]]:
    """Each row of the drawing as (name, bar), split at the axis."""
    lines = termaid.render(source).splitlines()
    return [
        (name.strip(), bar.rstrip()) for name, bar in (line.split("│", 1) for line in lines if "│" in line)
    ]


def bar_span(bar: str) -> range:
    cells = [i for i, ch in enumerate(bar) if ch in "█▓◆"]
    return range(cells[0], cells[-1] + 1)


def step(id: str, name: str, start_ms: float, end_ms: float | None, **kwargs) -> Observation:
    end = T0 + timedelta(milliseconds=end_ms) if end_ms is not None else None
    return Observation(
        id=id,
        type="TOOL",
        name=name,
        start_time=T0 + timedelta(milliseconds=start_ms),
        end_time=end,
        **kwargs,
    )


def test_steps_that_ran_at_the_same_time_overlap(roots):
    drawn = dict(rows(to_gantt(roots)))
    weather, pages = bar_span(drawn["✗ 3. get_weather"]), bar_span(drawn["4. fetch_pages"])
    assert weather.start == pages.start and weather.stop < pages.stop


def test_bars_start_and_last_as_the_steps_did(roots):
    lines = to_gantt(roots).splitlines()
    assert "  research-agent :0, 6215ms" in lines  # the trace starts at 0
    assert "  ✗ 3. get_weather :crit, 1430, 2011ms" in lines
    assert "  4. fetch_pages :1431, 3549ms" in lines


def test_rows_have_the_tree_numbers_and_every_sibling(roots):
    names = [name for name, _ in rows(to_gantt(roots))]
    assert names[:4] == ["research-agent", "1. plan", "2. search_docs", "2.1. cache-miss"]
    assert [n for n in names if "fetch_page_" in n] == [
        f"{'✗ ' if i == 5 else ''}4.{i}. fetch_page_{i}" for i in range(1, 10)
    ]
    assert names[-2:] == ["! 5. answer", '6. format "final" [answer]…']


def test_depth_folds_levels_like_the_tree(roots):
    names = [name for name, _ in rows(to_gantt(roots, max_depth=2))]
    assert names == [
        "research-agent",
        "1. plan",
        "2. search_docs +2 nested",
        "✗ 3. get_weather",
        # fetch_page_5 failed inside it, so the folded row is marked and its bar is crit.
        "✗ 4. fetch_pages +9 nested",
        "! 5. answer",
        '6. format "final" [answer]…',
    ]
    assert "  ✗ 4. fetch_pages +9 nested :crit, 1431, 3549ms" in to_gantt(roots, max_depth=2).splitlines()


def test_collapsed_siblings_span_the_steps_they_hide(roots):
    lines = to_gantt(roots, max_children=3).splitlines()
    assert "  ✗ +4 more :crit, 950, 5230ms" in lines  # 2. search_docs starts, 5. answer ends


def test_events_are_milestones(roots):
    assert "  2.1. cache-miss :milestone, 955, 0ms" in to_gantt(roots).splitlines()
    drawn = dict(rows(to_gantt(roots)))
    assert "◆" in drawn["2.1. cache-miss"]


def test_steps_without_an_end_run_to_the_end_as_active():
    root = step("r", "agent", 0, 1000)
    root.children = [step("a", "done", 0, 400), step("b", "still running", 500, None)]
    lines = to_gantt([root]).splitlines()
    assert "  2. still running :active, 500, 500ms" in lines
    assert "▓" in dict(rows(to_gantt([root])))["2. still running"]


def test_steps_without_a_start_are_left_out():
    root = step("r", "agent", 0, 1000)
    root.children = [Observation(id="x", type="SPAN", name="never started")]
    assert "never started" not in to_gantt([root])


def test_a_trace_without_times_has_no_tasks():
    assert (
        to_gantt([Observation(id="r", type="SPAN", name="agent")], comments=["Trace x"])
        == "gantt\n%% Trace x\n"
    )


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("tool:search", "1. tool∶search"),  # the first ":" would end the name
        ("50%% done", "1. 50% % done"),  # %% would start a comment
        ("#123; tag", "1. # 123; tag"),  # Mermaid would decode it, Termaid wouldn't
        ('say "hi" <b>', '1. say "hi" <b>'),
    ],
)
def test_task_names_show_as_written_or_close(name, shown):
    root = step("r", "agent", 0, 1000)
    root.children = [step("c", name, 0, 500)]
    assert [n for n, _ in rows(to_gantt([root]))] == ["agent", shown]


@pytest.mark.parametrize(
    "name", ["title page", "Section 2", "call center", "click here", "%x", "2024-01-01 run"]
)
def test_a_root_named_like_a_directive_is_still_a_task(name):
    root = step("r", name, 0, 1000)
    root.type = "AGENT"
    root.children = [step("c", "child", 0, 500)]
    assert [n for n, _ in rows(to_gantt([root]))] == [f"AGENT {name}", "1. child"]


def test_steps_without_an_end_are_active_bars_that_run_to_the_last_start():
    root = step("a", "root", 0, None)
    root.children = [step("b", "child", 400, None, parent_id="a")]
    source = to_gantt([root])
    assert source.count("active") == 2
    drawn = dict(rows(source))
    assert "▓" in drawn["root"] and "▓" in drawn["1. child"]
    assert bar_span(drawn["root"]).stop == bar_span(drawn["1. child"]).start


def test_comments_go_after_the_header(roots):
    lines = to_gantt(roots, comments=["Trace x · 1s", "✗ step failed:\nreason; more"]).splitlines()
    assert lines[:4] == ["gantt", "%% Trace x · 1s", "%% ✗ step failed: reason, more", "  dateFormat x"]


@pytest.mark.parametrize(
    ("span_ms", "lines"),
    [
        (50, ["  axisFormat %Qms", "  tickInterval 10millisecond"]),
        (1620, ["  axisFormat %Qms", "  tickInterval 500millisecond"]),
        (3502, ["  axisFormat %ss", "  tickInterval 1second"]),
        (45_000, ["  axisFormat %ss", "  tickInterval 10second"]),
        (1_200_000, ["  axisFormat %M:%S", "  tickInterval 5minute"]),
        (7_500_000, ["  axisFormat %H:%M", "  tickInterval 30minute"]),
    ],
)
def test_axis_has_a_few_round_ticks(span_ms, lines):
    assert axis(span_ms) == lines


def test_ticks_sit_at_their_times():
    root = step("r", "agent", 0, 3500)
    drawing = termaid.render(to_gantt([root]))
    axis_line, labels = drawing.splitlines()[-2:]
    start, end = axis_line.index("└") + 1, len(axis_line.rstrip())
    one_second = start + round(1000 / 3500 * (end - start))
    tick = axis_line.index("┬", start + 1)
    assert abs(tick - one_second) <= 1
    assert labels[tick - 1 : tick + 1] == "1s"


@pytest.mark.parametrize("max_depth", [None, 1, 2])
@pytest.mark.parametrize("max_children", [3, 9])
def test_rows_name_the_steps_the_tree_draws_in_the_same_order(roots, max_children, max_depth):
    def first_word(text: str) -> str:
        return text.removeprefix("✗ ").removeprefix("! ").split()[0]

    tree = termaid.parse(to_mermaid(roots, max_depth=max_depth, max_children=max_children))
    boxes = [first_word(node.label.split("\\n")[0]) for node in tree.nodes.values()]
    timeline = to_gantt(roots, max_depth=max_depth, max_children=max_children)
    assert [first_word(name) for name, _ in rows(timeline)] == boxes
