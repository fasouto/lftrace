import pytest
import termaid

from lftrace.mermaid import (
    collapse,
    escape,
    format_cost,
    format_duration,
    format_tokens,
    middle_first,
    to_mermaid,
)
from lftrace.tree import Observation

ALL = 9  # the fixture's longest sibling list: with this many children, nothing collapses
NASTY_NAMES = [
    'say "hi"',
    'x"]; y["z',
    "arr[0] (x) {y} |z| <b>",
    "line one\nline two\r\nthree",
    "50%% done %%%",
    "C:\\new\\temp",
    "ns:::name",
    "a; b --> c & d",
    "`code` **bold**",
    "graph TD",
]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('say "hi"', "say 'hi'"),
        ("line one\nline  two", "line one line two"),
        ("C:\\new", "C:/new"),
        ("50%% done", "50% % done"),
        ("a:::b", "a: ::b"),
        ("arr[0] {x} (y) |z|", "arr[0] {x} (y) |z|"),
    ],
)
def test_escape(text, expected):
    assert escape(text) == expected


@pytest.mark.parametrize("name", NASTY_NAMES)
def test_nasty_names_do_not_break_mermaid(name):
    parent = Observation(id="p", type="AGENT", name="agent")
    parent.children = [Observation(id="c", type="TOOL", name=name, level="ERROR", status_message=name)]
    source = to_mermaid([parent], comments=[name])

    graph = termaid.parse(source)

    assert list(graph.nodes) == ["n0", "n1"]
    assert [(e.source, e.target) for e in graph.edges] == [("n0", "n1")]
    assert f"1. {escape(name)}" in graph.nodes["n1"].label
    assert graph.nodes["n1"].style_class == "error"
    termaid.render(source)  # and it draws without raising


def test_each_step_points_to_its_children(roots):
    graph = termaid.parse(to_mermaid(roots, max_children=6))
    number = {i: node.label.split("\\n")[0].split(" ")[0] for i, node in graph.nodes.items()}
    edges = [(number[e.source], number[e.target]) for e in graph.edges]
    assert sorted(edges) == sorted(
        [
            ("research-agent", "1."),
            ("research-agent", "2."),
            ("2.", "2.1."),
            ("2.", "2.2."),
            ("research-agent", "3."),
            ("research-agent", "4."),
            ("4.", "4.1."),
            ("4.", "4.2."),
            ("4.", "4.3."),
            ("4.", "+4"),
            ("4.", "4.8."),
            ("4.", "4.9."),
            ("research-agent", "5."),
            ("research-agent", "6."),
        ]
    )


def test_edges_come_after_the_nodes_middle_child_first(roots):
    lines = to_mermaid(roots).splitlines()
    edges = [line for line in lines if "-->" in line]
    assert lines.index(edges[0]) > max(i for i, line in enumerate(lines) if '"]' in line or '")' in line)
    assert edges[:3] == ["  n0 --> n2", "  n0 --> n1", "  n0 --> n3"]  # +4 more, 1. plan, 6. format


def test_middle_first():
    assert middle_first(["a", "b", "c"]) == ["b", "a", "c"]
    assert middle_first(["a", "b"]) == ["a", "b"]
    assert middle_first(list("abcdef")) == list("cdbeaf")


def test_steps_are_numbered_name_first(roots):
    source = to_mermaid(roots, max_children=ALL)
    assert '["research-agent\\nAGENT · 6.21s"]' in source  # the trace itself has no number
    assert '["2. search_docs\\nTOOL · 470ms"]' in source
    assert '["2.1. cache-miss\\nEVENT"]' in source  # events have no duration
    assert '["2.2. vector-search\\nRETRIEVER · 244ms"]' in source


def test_generation_label_shows_model_tokens_and_cost(roots):
    source = to_mermaid(roots, max_children=ALL)
    assert '["1. plan\\nGENERATION · 870ms\\nopenai/gpt-4o-mini\\n470 tok · $0.000097"]' in source


def test_status_comes_last_with_a_symbol_and_a_short_message(roots):
    source = to_mermaid(roots, max_children=ALL)
    assert '["3. get_weather\\nTOOL · 2.01s\\n✗ TimeoutError: weather API tim…"]:::error' in source
    assert '\\n! answered without weather data"]:::warning' in source


def test_every_step_has_the_same_shape(roots):
    nodes = [line for line in to_mermaid(roots, max_children=6).splitlines() if '"' in line]
    for line in nodes:
        assert '["' in line or '("+' in line  # rectangles, rounded only for "+N more"


def test_status_colors_are_defined_once_when_used(roots):
    lines = to_mermaid(roots, max_children=ALL).splitlines()
    assert lines[-2:] == [
        "  classDef error stroke:#e5484d,color:#e5484d",
        "  classDef warning stroke:#f5a524,color:#f5a524",
    ]
    calm = Observation(id="a", type="SPAN", name="calm")
    assert "classDef" not in to_mermaid([calm])


def test_comments_go_after_the_header_on_one_line_each(roots):
    source = to_mermaid(roots, comments=["Trace x · 1s", "✗ step failed:\nreason"])
    assert source.splitlines()[:3] == ["graph LR", "%% Trace x · 1s", "%% ✗ step failed: reason"]
    assert len(termaid.parse(source).nodes) == len(termaid.parse(to_mermaid(roots)).nodes)


def test_three_children_are_shown_by_default(roots):
    source = to_mermaid(roots)
    assert '["1. plan\\n' in source and '["6. format' in source
    for hidden in ("2. search_docs", "3. get_weather", "4. fetch_pages", "5. answer"):
        assert hidden not in source
    # The hidden steps hold both errors and the warning, so the collapsed node counts them.
    assert '("+4 more\\n✗ 2 errors\\n! 1 warning"):::error' in source


def test_long_sibling_lists_collapse_in_the_middle(roots):
    source = to_mermaid(roots, max_children=6)
    for shown in (1, 2, 3, 8, 9):
        assert f'["4.{shown}. fetch_page_{shown}\\n' in source
    for hidden in (4, 5, 6, 7):
        assert f"fetch_page_{hidden}" not in source
    # fetch_page_5 failed, so the collapsed node says so and is colored as an error.
    assert '("+4 more\\n✗ 1 error"):::error' in source


def test_short_lists_are_not_collapsed(roots):
    assert "more" not in to_mermaid(roots, max_children=ALL)


def test_collapse():
    items = list(range(10))
    assert collapse(items, 3) == [0, [1, 2, 3, 4, 5, 6, 7, 8], 9]
    assert collapse(items, 6) == [0, 1, 2, [3, 4, 5, 6, 7], 8, 9]
    assert collapse(items, 10) == items
    assert collapse(items, 1) == [items]


def test_depth_1_shows_only_the_root_with_a_summary(roots):
    source = to_mermaid(roots, max_depth=1)
    assert source.splitlines()[1:] == [
        '  n0["research-agent\\nAGENT · 6.21s\\n+17 nested\\n✗ 2 errors\\n! 1 warning"]'
    ]


def test_depth_2_summarizes_grandchildren(roots):
    source = to_mermaid(roots, max_depth=2, max_children=ALL)
    assert '"4. fetch_pages\\nSPAN · 3.55s\\n+9 nested\\n✗ 1 error"' in source
    assert '"2. search_docs\\nTOOL · 470ms\\n+2 nested"' in source
    assert "fetch_page_1" not in source


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0.0042, "4ms"), (0.87, "870ms"), (2.014, "2.01s"), (75.4, "1m 15s"), (3600, "60m 00s")],
)
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


@pytest.mark.parametrize(
    ("cost", "text"),
    [
        (0.000403, "$0.0004"),
        (0.0000966, "$0.000097"),
        (0.0123, "$0.01"),
        (1.5, "$1.50"),
        (0, None),
        (None, None),
    ],
)
def test_format_cost(cost, text):
    assert format_cost(cost) == text


def test_format_tokens():
    assert format_tokens({"input": 10, "output": 5, "total": 15}) == "15 tok"
    assert format_tokens({"input": 1200, "output": 300}) == "1,500 tok"
    assert format_tokens({}) is None
