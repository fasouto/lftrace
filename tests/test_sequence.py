import re
from datetime import datetime, timedelta

import pytest
import termaid

from lftrace.sequence import to_sequence
from lftrace.tree import Observation

T0 = datetime(2026, 1, 1)
MESSAGE = re.compile(r"^(.*?)(->>|-->>|--x|-x)(.*?): (.*)$")


def step(
    id: str, name: str | None, start_ms: float, end_ms: float | None, type: str = "TOOL", **kwargs
) -> Observation:
    end = T0 + timedelta(milliseconds=end_ms) if end_ms is not None else None
    return Observation(
        id=id,
        type=type,
        name=name,
        start_time=T0 + timedelta(milliseconds=start_ms),
        end_time=end,
        **kwargs,
    )


def tree(root: Observation, *children: Observation) -> list[Observation]:
    root.children = list(children)
    return [root]


def lanes(source: str) -> dict[str, str]:
    return {m[1]: m[2] for m in re.finditer(r"participant (p\d+) as (.*)", source)}


def arrow(m: re.Match, names: dict[str, str]) -> str:
    return f"{m[1]}{names[m[2]]}{m[3]}{names[m[4]]}:"


def lines(source: str) -> list[str]:
    """The source's statements, without indentation, comments or participants, with lanes by name."""
    names = lanes(source)
    found = []
    for line in source.splitlines()[1:]:
        if not line.startswith(("%%", "  participant")):
            line = re.sub(r"^(\s*)(p\d+)(->>|-->>|--x|-x)(p\d+):", lambda m: arrow(m, names), line)
            line = re.sub(r"^(\s*Note over )(p\d+):", lambda m: f"{m[1]}{names[m[2]]}:", line)
            found.append(line.strip())
    return found


def messages(source: str) -> list[str]:
    """Each message as "<from lane name><arrow><to lane name>: <text>"."""
    return [line for line in lines(source) if MESSAGE.match(line)]


def agent(*children: Observation, end_ms: float = 1000) -> list[Observation]:
    return tree(step("r", "agent", 0, end_ms, "AGENT"), *children)


def test_source_starts_with_comments_then_lanes_then_calls(roots):
    source = to_sequence(roots, comments=["Trace x · 1s", "✗ failed:\nreason; more"])
    assert source.splitlines()[:3] == ["sequenceDiagram", "%% Trace x · 1s", "%% ✗ failed: reason, more"]
    assert list(lanes(source).values())[:2] == ["research-agent", "openai/gpt-4o-mini"]
    assert source.index("participant") < source.index("->>")


def test_the_root_is_the_first_lane_then_models_then_tools(roots):
    assert list(lanes(to_sequence(roots)).values()) == ["research-agent", "openai/gpt-4o-mini", "tools"]


def test_lanes_are_ordered_by_group_then_first_use():
    sub = step("s", "sub", 0, 900, "AGENT")
    sub.children = [step("m", "think", 0, 100, "GENERATION", model="m2")]
    roots = agent(
        step("a", "a", 0, 50),
        step("b", "b", 60, 100, "GENERATION", model="m1"),
        step("c", "c", 110, 150, "EMBEDDING"),
        sub,
    )
    assert list(lanes(to_sequence(roots)).values()) == ["agent", "sub", "m1", "LLM", "m2", "tools"]


def test_every_kind_of_tool_shares_a_lane():
    roots = agent(
        *(
            step(t, t, i * 100, i * 100 + 50, t)
            for i, t in enumerate(["TOOL", "RETRIEVER", "GUARDRAIL", "EVALUATOR"])
        )
    )
    assert list(lanes(to_sequence(roots)).values()) == ["agent", "tools"]


def test_lane_ids_are_generated_so_names_cannot_clash_with_syntax():
    roots = agent(step("a", "end", 0, 50, "AGENT"), step("b", "loop", 60, 100, "AGENT"))
    roots[0].children[0].children = [step("c", "x", 0, 10)]
    source = to_sequence(roots)
    assert list(lanes(source).values()) == ["agent", "end", "loop", "tools"]
    assert not re.search(r"participant (end|loop)\b", source)
    assert "agent->>end: 1. end" in lines(source)


def test_a_wrapper_around_one_root_is_its_lane_even_if_not_an_agent():
    roots = tree(step("r", "request", 0, 100, "SPAN"), step("a", "tool", 0, 50))
    assert messages(to_sequence(roots)) == ["request->>tools: 1. tool", "tools-->>request: 50ms"]


@pytest.mark.parametrize("type", ["TOOL", "RETRIEVER", "GUARDRAIL", "EVALUATOR", "GENERATION", "EMBEDDING"])
def test_a_root_with_a_shared_lane_gets_its_own_lane_named_after_it(type):
    root = step("r", "outer", 0, 500, type, model="m")
    root.children = [step("t", "inner", 0, 100)]
    assert messages(to_sequence([root])) == ["outer->>tools: 1. inner", "tools-->>outer: 100ms"]
    assert list(lanes(to_sequence([root])).values()) == ["outer", "tools"]


def test_an_agent_root_keeps_its_agent_lane_so_a_sub_agent_of_that_name_is_a_self_call():
    roots = agent(step("s", "agent", 0, 400, "AGENT"))
    assert messages(to_sequence(roots))[0] == "agent->>agent: 1. agent"
    assert list(lanes(to_sequence(roots)).values()) == ["agent"]


@pytest.mark.parametrize("type", ["AGENT", "SPAN", "TOOL"])
@pytest.mark.parametrize(
    ("level", "message", "shown"),
    [
        ("ERROR", "boom", "✗ boom"),
        ("WARNING", "careful", "! careful"),
        ("ERROR", None, "✗ error"),
        ("WARNING", None, "! warning"),
        ("ERROR", "x " * 40, f"✗ {'x ' * 14}x…"),
    ],
)
def test_a_root_that_failed_says_so_in_a_note_after_its_children(type, level, message, shown):
    root = step("r", "run", 0, 500, type, level=level, status_message=message)
    root.children = [step("t", "inner", 0, 100)]
    out = lines(to_sequence([root]))
    assert out[-1] == f"Note over run: run · {shown}"
    assert out[-2] == "tools-->>run: 100ms"


def test_a_root_without_an_error_or_warning_has_no_note():
    roots = agent(step("t", "inner", 0, 100))
    roots[0].level = "DEBUG"
    assert not [line for line in lines(to_sequence(roots)) if line.startswith("Note")]


def test_a_single_leaf_root_is_called_from_the_trace_lane():
    roots = [step("r", "answer", 0, 500, "GENERATION", model="m", usage={"total": 12})]
    assert messages(to_sequence(roots)) == ["trace->>m: answer", "m-->>trace: 500ms · 12 tok"]
    assert list(lanes(to_sequence(roots)).values()) == ["trace", "m"]


def test_several_roots_are_called_from_the_trace_lane_and_numbered():
    roots = [step("a", "first", 0, 100), step("b", "second", 200, 300)]
    assert messages(to_sequence(roots)) == [
        "trace->>tools: 1. first",
        "tools-->>trace: 100ms",
        "trace->>tools: 2. second",
        "tools-->>trace: 100ms",
    ]


def test_a_call_and_its_return_carry_duration_tokens_and_cost():
    roots = agent(
        step(
            "a", "think", 0, 400, "GENERATION", model="m", usage={"input": 10, "output": 5}, total_cost=0.002
        )
    )
    assert messages(to_sequence(roots)) == ["agent->>m: 1. think", "m-->>agent: 400ms · 15 tok · $0.002"]


def test_a_step_without_an_end_returns_no_end_time():
    assert messages(to_sequence(agent(step("a", "hung", 0, None))))[1] == "tools-->>agent: no end time"


def test_the_children_of_a_call_are_drawn_from_its_lane():
    sub = step("s", "sub", 0, 500, "AGENT")
    sub.children = [step("t", "lookup", 0, 100)]
    assert messages(to_sequence(agent(sub))) == [
        "agent->>sub: 1. sub",
        "sub->>tools: 1.1. lookup",
        "tools-->>sub: 100ms",
        "sub-->>agent: 500ms",
    ]


def test_an_error_returns_with_a_cross_and_its_message_cut_short():
    roots = agent(step("a", "run", 0, 100, level="ERROR", status_message="boom " * 20))
    assert messages(to_sequence(roots))[1] == f"tools--xagent: 100ms · ✗ {'boom ' * 5}boom…"


def test_a_warning_returns_with_an_arrow_and_its_message():
    roots = agent(step("a", "run", 0, 100, level="WARNING", status_message="careful"))
    assert messages(to_sequence(roots))[1] == "tools-->>agent: 100ms · ! careful"


def test_a_status_without_a_message_names_itself():
    roots = agent(step("a", "run", 0, 100, level="ERROR"))
    assert messages(to_sequence(roots))[1] == "tools--xagent: 100ms · ✗ error"


def test_work_without_children_is_a_self_message_on_the_callers_lane():
    roots = agent(
        step("a", "compute", 0, 100, "SPAN"),
        step("b", "decided", 200, 200, "EVENT"),
        step("c", "bad span", 300, 400, "CHAIN", level="ERROR", status_message="oops"),
    )
    assert messages(to_sequence(roots)) == [
        "agent->>agent: 1. compute · 100ms",
        "agent->>agent: 2. decided",
        "agent-xagent: 3. bad span · 100ms · ✗ oops",
    ]


def test_a_wrapper_is_drawn_inline_with_its_children_called_from_the_callers_lane():
    wrapper = step("w", "research", 0, 500, "SPAN")
    wrapper.children = [step("a", "one", 0, 100), step("b", "two", 200, 300)]
    out = messages(to_sequence(agent(wrapper)))
    assert out == [
        "agent->>tools: 1.1. one",
        "tools-->>agent: 100ms",
        "agent->>tools: 1.2. two",
        "tools-->>agent: 100ms",
    ]


def test_a_wrapper_with_a_warning_is_a_self_message_followed_by_its_children():
    wrapper = step("w", "research", 0, 500, "SPAN", level="WARNING", status_message="partial")
    wrapper.children = [step("a", "one", 0, 100)]
    assert messages(to_sequence(agent(wrapper)))[:2] == [
        "agent->>agent: 1. research · 500ms · ! partial",
        "agent->>tools: 1.1. one",
    ]


def test_siblings_that_overlap_are_in_par_branches():
    source = to_sequence(agent(step("a", "slow", 0, 500), step("b", "other", 100, 300, "AGENT")))
    assert lines(source) == [
        "par",
        "agent->>tools: 1. slow",
        "tools-->>agent: 500ms",
        "and",
        "agent->>other: 2. other",
        "other-->>agent: 200ms",
        "end",
    ]


def test_a_later_sibling_joins_a_group_while_any_member_is_still_running():
    roots = agent(step("a", "a", 0, 500), step("b", "b", 100, 200), step("c", "c", 300, 400))
    assert lines(to_sequence(roots)).count("and") == 2


def test_siblings_that_ran_one_after_another_have_no_par():
    source = to_sequence(agent(step("a", "a", 0, 100), step("b", "b", 100, 200), step("c", "c", 300, 400)))
    assert not {"par", "loop"} & set(lines(source))


def test_a_step_without_an_end_joins_a_group_but_does_not_extend_it():
    roots = agent(step("a", "a", 0, 100), step("b", "b", 50, None), step("c", "c", 120, 200))
    out = lines(to_sequence(roots))
    assert out.count("par") == 1 and out.count("and") == 1
    assert out.index("agent->>tools: 3. c") > out.index("end")


def test_a_group_led_by_a_step_without_an_end_takes_no_one():
    assert "par" not in lines(to_sequence(agent(step("a", "a", 0, None), step("b", "b", 100, 200))))


def test_a_step_without_a_start_never_joins_a_group():
    late = Observation(id="x", type="TOOL", name="x", end_time=T0 + timedelta(milliseconds=50))
    assert "par" not in lines(to_sequence(agent(step("a", "a", 0, 500), late)))


def test_calls_that_overlapped_are_not_a_loop_when_one_has_no_end():
    roots = agent(step("a", "fetch", 0, 500), step("b", "fetch", 100, None))
    out = lines(to_sequence(roots))
    assert "par" in out and not any(line.startswith("loop") for line in out)


def test_unfinished_calls_still_fold_with_the_rest_of_their_group():
    fetches = [step(str(i), "fetch", i * 10, None if i == 2 else 300 + i * 100) for i in range(5)]
    assert messages(to_sequence(agent(*fetches))) == ["agent->>tools: 1–5. fetch ×5", "tools-->>agent: 660ms"]


def test_a_fold_of_unfinished_calls_returns_no_end_time():
    led = Observation(id="l", type="TOOL", name="fetch", end_time=T0 + timedelta(milliseconds=500))
    roots = agent(led, step("b", "fetch", 10, None), step("c", "fetch", 20, None))
    assert messages(to_sequence(roots)) == ["agent->>tools: 1–3. fetch ×3", "tools-->>agent: no end time"]


def test_par_over_a_wrapper_takes_its_name():
    wrapper = step("w", "research", 0, 500, "SPAN")
    wrapper.children = [step("a", "one", 0, 300), step("b", "two", 100, 200)]
    assert "par research" in lines(to_sequence(agent(wrapper)))


def test_parallel_wrappers_each_label_their_branch():
    workers = []
    for i in range(3):
        worker = step(f"w{i}", "worker", i * 10, 500, "SPAN")
        worker.children = [step(f"c{i}", "job", i * 10, 400)]
        workers.append(worker)
    out = lines(to_sequence(agent(*workers)))
    assert [line for line in out if line.startswith(("par", "and"))] == [
        "par 1. worker",
        "and 2. worker",
        "and 3. worker",
    ]


def test_a_branch_over_a_plain_step_keeps_the_wrapper_name_only_on_the_first_branch():
    wrapper = step("w", "research", 0, 500, "SPAN")
    wrapper.children = [step("a", "one", 0, 300), step("b", "two", 100, 200)]
    out = lines(to_sequence(agent(wrapper)))
    assert [line for line in out if line.startswith(("par", "and"))] == ["par research", "and"]


def test_the_note_branch_of_a_long_par_has_no_label():
    workers = []
    for i in range(6):
        worker = step(f"w{i}", "worker", i, 500, "SPAN")
        worker.children = [step(f"c{i}", "job", i, 400)]
        workers.append(worker)
    out = lines(to_sequence(agent(*workers)))
    assert [line for line in out if line.startswith(("par", "and"))] == [
        "par 1. worker",
        "and",
        "and 6. worker",
    ]


def test_parallel_wrapper_labels_render_and_stay_in_the_diagram():
    workers = []
    for i in range(2):
        worker = step(f"w{i}", "worker", i * 10, 500, "SPAN")
        worker.children = [step(f"c{i}", "job", i * 10, 400)]
        workers.append(worker)
    drawing = termaid.render(to_sequence(agent(*workers)))
    assert "1. worker" in drawing and "2. worker" in drawing


def test_repeats_are_a_loop_that_counts_the_failures():
    roots = agent(
        step("a", "run", 0, 100, level="ERROR", status_message="x"),
        step("b", "run", 100, 200),
        step("c", "run", 200, 300, level="ERROR", status_message="y"),
    )
    source = to_sequence(roots)
    assert "loop run ×3, 2 failed" in lines(source)
    assert sum(m.endswith("--xagent: 100ms · ✗ x") or "✗ y" in m for m in messages(source)) == 2


def test_a_loop_without_failures_says_only_how_many():
    assert "loop run ×2" in lines(to_sequence(agent(step("a", "run", 0, 1), step("b", "run", 2, 3))))


def test_a_failure_inside_a_repeat_counts_as_failed():
    first = step("a", "attempt", 0, 100, "SPAN")
    first.children = [step("x", "inner", 0, 50, level="ERROR", status_message="x")]
    roots = agent(first, step("b", "attempt", 200, 300, "SPAN"))
    assert "loop attempt ×2, 1 failed" in lines(to_sequence(roots))


def test_different_steps_are_not_a_loop():
    assert "loop" not in to_sequence(agent(step("a", "a", 0, 1), step("b", "b", 2, 3)))


def test_a_long_loop_shows_the_first_a_note_and_the_last():
    runs = [step(str(i), "run", i * 100, i * 100 + 50, level="ERROR" if i == 2 else None) for i in range(5)]
    source = to_sequence(agent(*runs))
    assert "loop run ×5, 1 failed" in lines(source)
    assert [m.split(": ")[1] for m in messages(source) if "->>tools" in m] == ["1. run", "5. run"]
    assert "Note over agent: +3 more · ✗ 1 error" in lines(source)


def test_parallel_steps_of_one_kind_fold_into_one_call():
    pages = [
        step(str(i), "fetch", i * 10, 300 + i * 100, level="ERROR" if i == 2 else None, status_message="bad")
        for i in range(4)
    ]
    pages[3].level = "WARNING"
    source = to_sequence(agent(*pages))
    assert "par" not in lines(source)
    assert messages(source) == [
        "agent->>tools: 1–4. fetch ×4",
        "tools--xagent: 570ms · ✗ 1 error · ! 1 warning",
    ]


def test_a_fold_draws_the_first_member_with_a_problem_and_its_subtree():
    pages = [step(str(i), "fetch", i * 10, 400) for i in range(3)]
    pages[1].children = [step("c", "inner", 20, 100, "AGENT", level="WARNING", status_message="w")]
    assert messages(to_sequence(agent(*pages))) == [
        "agent->>tools: 1–3. fetch ×3",
        "tools->>inner: 2.1. inner",
        "inner-->>tools: 80ms · ! w",
        "tools-->>agent: 400ms · ! 1 warning",
    ]


def test_two_parallel_steps_of_one_kind_stay_in_par():
    assert "par" in lines(to_sequence(agent(step("a", "fetch", 0, 100), step("b", "fetch", 10, 100))))


def test_a_long_par_shows_the_first_a_note_branch_and_the_last(roots):
    source = to_sequence(roots)
    assert [line for line in lines(source) if line.startswith("and")] == ["and 4. fetch_pages", "and", "and"]
    # The branch names the fetch_pages wrapper, so its own par doesn't repeat the name.
    assert [line for line in lines(source) if line.startswith("par")] == ["par", "par"]
    assert "Note over research-agent: +7 more · ✗ 1 error" in lines(source)
    called = [m.split(": ")[1] for m in messages(source) if m.startswith("research-agent->>tools")]
    assert called[-2:] == ["4.1. fetch_page_1", "4.9. fetch_page_9"]
    assert not any("fetch_page_5" in m for m in messages(source))


def test_a_long_par_of_different_steps_also_hides_the_middle():
    roots = agent(*(step(str(i), f"s{i}", i, 500) for i in range(6)))
    source = to_sequence(roots)
    assert [m for m in lines(source) if m.startswith("Note")] == ["Note over agent: +4 more"]
    assert [m.split(": ")[1] for m in messages(source) if "-->>" not in m] == ["1. s0", "6. s5"]


def test_loops_and_wrappers_nest_inside_par_branches():
    attempt = [step(str(i), "try", i * 10, i * 10 + 5, "SPAN") for i in range(2)]
    wrapper = step("w", "group", 0, 500, "SPAN")
    wrapper.children = attempt
    source = to_sequence(agent(wrapper, step("o", "other", 20, 100, "AGENT")))
    assert lines(source)[:3] == ["par 1. group", "loop try ×2", "agent->>agent: 1.1. try · 5ms"]
    assert lines(source)[-1] == "end"


def test_depth_folds_levels_and_counts_what_they_hide():
    tool = step("t", "search", 0, 300)
    tool.children = [
        step("a", "deep", 0, 100, level="ERROR", status_message="x"),
        step("b", "deeper", 100, 200, level="WARNING", status_message="y"),
    ]
    out = messages(to_sequence(agent(tool), max_depth=2))
    assert out == ["agent->>tools: 1. search", "tools-->>agent: 300ms · +2 nested · ✗ 1 error · ! 1 warning"]


def test_depth_one_draws_the_root_from_the_trace_lane():
    tool = step("t", "search", 0, 300)
    out = messages(to_sequence(agent(tool), max_depth=1))
    assert out == ["trace->>agent: agent", "agent-->>trace: 1.00s · +1 nested"]


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("a;b", "a,b"),
        ("issue #1 fixed", "issue ♯1 fixed"),
        ("a<br>b <br/> <BR /> List<int>", "a‹br>b ‹br/> ‹BR /> List<int>"),
        ("line\none\n\ttwo", "line one two"),
        ("50%% done", "50% % done"),
        ("keep \\n here", "keep \\n here"),
    ],
)
def test_text_is_safe_in_every_place_it_appears(text, shown):
    loop = [step("a", text, 0, 100, "AGENT", model=text), step("b", text, 200, 300, "AGENT")]
    loop[0].children = [step("c", "t", 0, 10, level="ERROR", status_message=text)]
    source = to_sequence(agent(*loop, step("d", text, 400, 500, level="WARNING", status_message=text)))
    for line in lines(source) + re.findall(r"participant .*", source):
        assert ";" not in line and "#" not in line and "\n" not in line
    assert f"loop {shown} ×2, 1 failed" in lines(source)
    assert shown in lanes(source).values()
    assert f"agent->>{shown}: 1. {shown}" in lines(source)
    assert any(m.endswith(f"· ✗ {shown}") for m in messages(source))
    assert any(m.endswith(f"· ! {shown}") for m in messages(source))


@pytest.mark.parametrize("name", ["wrap:", "nowrap:", "WRAP: x", "NoWrap:x"])
def test_a_name_that_mermaid_reads_as_a_wrap_mode_keeps_its_text(name):
    leaf = [step("l", name, 0, 100, "GENERATION", model=name)]
    root = step("r", name, 0, 500, "AGENT", level="ERROR", status_message="x")
    root.children = [step("a", name, 0, 100), step("b", name, 200, 300), step("w", name, 400, 450, "SPAN")]
    wrapper = step("p", name, 0, 100, "SPAN")
    wrapper.children = [step("c", "c", 0, 50), step("d", "d", 10, 50)]
    for roots in (leaf, [root], agent(wrapper)):
        source = to_sequence(roots)
        texts = re.findall(
            r"(?m)^\s*(?:participant p\d+ as|p\d+(?:->>|-->>|--x|-x)p\d+:|Note over p\d+:|loop|par|and) ?(.*)$",
            source,
        )
        assert texts and not [t for t in texts if re.match(r"(?i)(no)?wrap:", t)]
        assert name.replace(":", "∶") in source


def test_no_message_is_empty(roots):
    sources = [to_sequence(roots), to_sequence(roots, max_depth=1), to_sequence(roots, max_depth=2)]
    sources.append(to_sequence(agent(step("a", None, 0, None, "EVENT"), step("b", "  ", 1, 2, "SPAN"))))
    for source in sources:
        found = messages(source)
        assert found and all(MESSAGE.match(line)[4].strip() for line in found)


@pytest.mark.parametrize("max_depth", [None, 1, 2, 3])
def test_the_fixture_renders(roots, max_depth):
    drawing = termaid.render(to_sequence(roots, max_depth=max_depth))
    assert "research-agent" in drawing
