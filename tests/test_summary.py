from lftrace.summary import headline, issues, plain
from lftrace.tree import Observation


def test_headline_has_the_totals(roots, observations):
    parts = headline(observations[0].trace_id, observations)
    assert plain(parts) == (
        "Trace 7f3e0c2a9b1d4e6f8a0b2c4d6e8f0a1b · 6.21s · 2,522 tokens · $0.0005 · 2 errors · 1 warning"
    )
    assert parts[-2:] == [("2 errors", "error"), ("1 warning", "warning")]


def test_headline_without_cost_problems_or_tokens():
    calm = [Observation(id="a", type="SPAN", name="calm")]
    assert plain(headline("abc", calm)) == "Trace abc · 0 tokens · $0.00"


def test_issues_list_every_error_and_warning_in_full(roots):
    assert issues(roots) == [
        ("error", "✗ 3. get_weather (TOOL): TimeoutError: weather API timed out after 2s"),
        # Hidden in the diagram's "+4 more" node, but listed with its number.
        ("error", "✗ 4.5. fetch_page_5 (TOOL): HTTP 503 from example.org"),
        ("warning", "! 5. answer (GENERATION): answered without weather data"),
    ]


def test_issue_without_a_message():
    root = Observation(id="r", type="AGENT", name="agent", level="ERROR")
    assert issues([root]) == [("error", "✗ agent (AGENT): no error message")]
