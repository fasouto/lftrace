"""Exercise the real Langfuse SDK client against a fake HTTP server."""

import base64
import json

import httpx
import pytest
from conftest import FIXTURE

from lftrace import fetch
from lftrace.fetch import FetchError, fetch_observations, latest_trace_id, load_file
from lftrace.tree import Observation, build_tree

TRACE_ID = "7f3e0c2a9b1d4e6f8a0b2c4d6e8f0a1b"


def make_api(handler):
    from langfuse.api import LangfuseAPI

    return LangfuseAPI(
        base_url="https://langfuse.test",
        username="pk-lf-test",
        password="sk-lf-test",
        httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_fetches_every_page_of_a_trace():
    observations = load_file(FIXTURE)
    pages = {None: (observations[:10], "page-2"), "page-2": (observations[10:], None)}
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        data, cursor = pages[request.url.params.get("cursor")]
        return httpx.Response(200, json={"data": data, "meta": {"cursor": cursor}})

    result = fetch_observations(make_api(handler), TRACE_ID)

    assert len(requests) == 2
    first = requests[0]
    assert first.url.path == "/api/public/v2/observations"
    assert first.url.params["traceId"] == TRACE_ID
    assert first.url.params["fields"] == "core,basic,model,usage"
    assert requests[1].url.params["cursor"] == "page-2"
    expected_auth = "Basic " + base64.b64encode(b"pk-lf-test:sk-lf-test").decode()
    assert first.headers["authorization"] == expected_auth
    # The SDK models come back as the same JSON shape a saved file has.
    assert sorted(o["id"] for o in result) == sorted(o["id"] for o in observations)
    roots = build_tree([Observation.from_dict(o) for o in result])
    assert roots[0].name == "research-agent"
    assert roots[0].children[0].duration == 0.87


def test_latest_trace_is_the_newest_root_observation():
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        assert params["isRootObservation"] == "true"
        assert params["limit"] == "1"
        assert "fromStartTime" in params
        root = next(o for o in load_file(FIXTURE) if o["parentObservationId"] is None)
        return httpx.Response(200, json={"data": [root], "meta": {"cursor": None}})

    assert latest_trace_id(make_api(handler)) == TRACE_ID


def test_no_recent_traces():
    api = make_api(lambda request: httpx.Response(200, json={"data": [], "meta": {}}))
    with pytest.raises(FetchError, match="no traces in the last 7 days"):
        latest_trace_id(api)


def test_rejected_keys_give_a_short_message_without_the_keys():
    api = make_api(lambda request: httpx.Response(401, json={"message": "Invalid credentials"}))
    with pytest.raises(FetchError) as error:
        fetch_observations(api, TRACE_ID)
    assert "rejected the keys" in str(error.value)
    assert "sk-lf-test" not in str(error.value)


def test_missing_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-secret")
    with pytest.raises(FetchError, match="set LANGFUSE_PUBLIC_KEY") as error:
        fetch.client()
    assert "sk-lf-secret" not in str(error.value)


def test_host_follows_sdk_precedence(monkeypatch):
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    assert fetch.host() == "https://cloud.langfuse.com"
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000/")
    assert fetch.host() == "http://localhost:3000"
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    assert fetch.host() == "https://us.cloud.langfuse.com"


def test_load_file_accepts_a_bare_list(tmp_path):
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(load_file(FIXTURE)))
    assert len(load_file(path)) == 18


@pytest.mark.parametrize("content", ["not json", '{"data": "nope"}', "[1, 2]"])
def test_load_file_rejects_other_json(tmp_path, content):
    path = tmp_path / "trace.json"
    path.write_text(content)
    with pytest.raises(FetchError):
        load_file(path)
