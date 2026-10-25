import httpx
import pytest
from conftest import FIXTURE
from rich.text import Text
from test_fetch import TRACE_ID, make_api

from lftrace import cli
from lftrace.fetch import load_file
from lftrace.mermaid import COLORS


def test_draws_a_saved_trace(capsys):
    assert cli.main(["--file", str(FIXTURE)]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == f"Trace {TRACE_ID} · 6.21s · 2,522 tokens · $0.0005 · 2 errors · 1 warning"
    assert "research-agent" in out and "3. get_weather" in out and "+4 more" in out
    assert "┌" in out and "►" in out
    assert lines[-3:] == [
        "✗ 3. get_weather (TOOL): TimeoutError: weather API timed out after 2s",
        "✗ 4.5. fetch_page_5 (TOOL): HTTP 503 from example.org",
        "! 5. answer (GENERATION): answered without weather data",
    ]
    assert "\x1b[" not in out  # no colors when the output isn't a terminal


def test_status_symbols_inside_boxes_are_colored():
    drawing = Text("│  ✗ timed out  │\n│ ! no weather │\n│ hi! there ✗ │")
    cli.color_symbols(drawing)
    colored = [(drawing.plain[s.start : s.end], s.style) for s in drawing.spans]
    assert colored == [("✗", f"not dim {COLORS['error']}"), ("!", f"not dim {COLORS['warning']}")]


def test_status_symbols_starting_timeline_rows_are_colored():
    drawing = Text("   ✗ 3. get_weather  │████\n   5. answer!        │  ██")
    cli.color_symbols(drawing)
    assert [(drawing.plain[s.start : s.end], s.style) for s in drawing.spans] == [
        ("✗", f"not dim {COLORS['error']}")
    ]


def test_timeline_draws_bars_on_a_time_axis(capsys):
    assert cli.main(["--file", str(FIXTURE), "--timeline"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"Trace {TRACE_ID} · 6.21s · 2,522 tokens · $0.0005 · 2 errors · 1 warning"
    assert any(line.startswith("   ✗ 4.5. fetch_page_5 ") and "█" in line for line in lines)
    assert any(line.split() == ["0s", "2s", "4s", "6s"] for line in lines)
    assert lines[-3:] == [
        "✗ 3. get_weather (TOOL): TimeoutError: weather API timed out after 2s",
        "✗ 4.5. fetch_page_5 (TOOL): HTTP 503 from example.org",
        "! 5. answer (GENERATION): answered without weather data",
    ]


def test_timeline_depth_folds_levels_like_the_tree(capsys):
    assert cli.main(["--file", str(FIXTURE), "--timeline", "--depth", "2"]) == 0
    out = capsys.readouterr().out
    assert "✗ 4. fetch_pages +9 nested" in out
    assert "fetch_page_1" not in out.split("✗ 3. get_weather (TOOL)")[0]


def test_timeline_mermaid_prints_gantt_source_with_totals_and_full_errors(capsys):
    assert cli.main(["--file", str(FIXTURE), "--timeline", "--mermaid", "--depth", "1"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:3] == [
        "gantt",
        f"%% Trace {TRACE_ID} · 6.21s · 2,522 tokens · $0.0005 · 2 errors · 1 warning",
        "%% ✗ 3. get_weather (TOOL): TimeoutError: weather API timed out after 2s",
    ]
    assert lines[-1] == "  ✗ research-agent +17 nested :crit, 0, 6215ms"


def test_mermaid_flag_prints_the_source_with_totals_and_full_errors(capsys):
    assert cli.main(["--file", str(FIXTURE), "--mermaid", "--depth", "1"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:5] == [
        "graph LR",
        f"%% Trace {TRACE_ID} · 6.21s · 2,522 tokens · $0.0005 · 2 errors · 1 warning",
        "%% ✗ 3. get_weather (TOOL): TimeoutError: weather API timed out after 2s",
        "%% ✗ 4.5. fetch_page_5 (TOOL): HTTP 503 from example.org",
        "%% ! 5. answer (GENERATION): answered without weather data",
    ]
    assert not any("┌" in line for line in lines)


def test_needs_a_trace_id_latest_or_file(capsys):
    with pytest.raises(SystemExit) as exit:
        cli.main([])
    assert exit.value.code == 2
    with pytest.raises(SystemExit):
        cli.main(["abc", "--latest"])


def test_depth_must_be_positive(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--file", str(FIXTURE), "--depth", "0"])


def test_missing_keys_are_reported_without_printing_secrets(monkeypatch, capsys):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-secret")
    assert cli.main(["some-trace"]) == 1
    captured = capsys.readouterr()
    assert "LANGFUSE_PUBLIC_KEY" in captured.err
    assert "sk-lf-secret" not in captured.out + captured.err


def test_latest_fetches_the_newest_trace(monkeypatch, capsys):
    observations = load_file(FIXTURE)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("isRootObservation") == "true":
            data = [o for o in observations if o["parentObservationId"] is None]
        else:
            assert request.url.params["traceId"] == TRACE_ID
            data = observations
        return httpx.Response(200, json={"data": data, "meta": {"cursor": None}})

    monkeypatch.setattr(cli, "client", lambda: make_api(handler))
    assert cli.main(["--latest"]) == 0
    assert f"Trace {TRACE_ID}" in capsys.readouterr().out


def test_trace_without_observations(monkeypatch, capsys):
    api = make_api(lambda request: httpx.Response(200, json={"data": [], "meta": {}}))
    monkeypatch.setattr(cli, "client", lambda: api)
    assert cli.main(["unknown-trace"]) == 1
    assert "no observations in trace unknown-trace" in capsys.readouterr().err
