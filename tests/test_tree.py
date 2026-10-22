import random
from datetime import timedelta

from conftest import names

from lftrace.tree import Observation, build_tree, descendants, number_steps


def test_one_root_with_all_observations_below_it(roots, observations):
    assert names(roots) == ["research-agent"]
    assert len(list(descendants(roots[0]))) == len(observations) - 1


def test_children_are_ordered_by_start_time(roots):
    # The fixture lists observations newest first, as the API returns them.
    agent = roots[0]
    assert names(agent.children) == [
        "plan",
        "search_docs",
        "get_weather",
        "fetch_pages",
        "answer",
        'format "final" [answer] {v2}\nstep (2/2) 50%% done | C:\\new',
    ]
    search_docs = agent.children[1]
    assert names(search_docs.children) == ["cache-miss", "vector-search"]
    fetch_pages = agent.children[3]
    assert names(fetch_pages.children) == [f"fetch_page_{i}" for i in range(1, 10)]


def test_order_does_not_depend_on_input_order(observations):
    expected = _shape(build_tree(observations))
    shuffled = observations[:]
    random.Random(0).shuffle(shuffled)
    assert _shape(build_tree(shuffled)) == expected


def test_observation_with_unknown_parent_becomes_a_root(observations):
    orphan = Observation(
        id="orphan",
        type="SPAN",
        name="late-span",
        parent_id="not-ingested-yet",
        start_time=observations[0].start_time + timedelta(seconds=1),
    )
    roots = build_tree([*observations, orphan])
    assert names(roots) == ["research-agent", "late-span"]


def test_reads_api_fields(roots):
    plan = roots[0].children[0]
    assert plan.type == "GENERATION"
    assert plan.model == "openai/gpt-4o-mini"
    assert plan.usage["total"] == 470
    assert plan.total_cost == 0.0000966
    assert plan.duration == 0.87
    weather = roots[0].children[2]
    assert weather.level == "ERROR"
    assert weather.status_message.startswith("TimeoutError")


def test_steps_are_numbered_in_execution_order(roots):
    numbers = number_steps(roots)
    agent = roots[0]
    assert agent.id not in numbers  # a single root is the trace itself
    by_name = {o.name: numbers[o.id] for o in descendants(agent)}
    assert by_name["plan"] == "1"
    assert by_name["cache-miss"] == "2.1"
    assert by_name["vector-search"] == "2.2"
    assert by_name["fetch_page_9"] == "4.9"
    assert by_name["answer"] == "5"


def test_several_roots_are_numbered_too():
    a, b = Observation(id="a", type="SPAN"), Observation(id="b", type="SPAN")
    a.children = [Observation(id="c", type="SPAN")]
    assert number_steps([a, b]) == {"a": "1", "c": "1.1", "b": "2"}


def _shape(roots):
    return [(o.id, _shape(o.children)) for o in roots]
