"""Get a trace's observations from Langfuse or from a saved JSON file.

Both paths return observations in the JSON shape of the public API
(GET /api/public/v2/observations), so the rest of lftrace only knows one format.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langfuse.api import LangfuseAPI

FIELDS = "core,basic,model,usage"  # everything the diagram shows; no input/output
PAGE_SIZE = 1000  # the API maximum
LATEST_WINDOW = timedelta(days=7)
DEFAULT_HOST = "https://cloud.langfuse.com"


class FetchError(Exception):
    """A problem worth showing to the user as a one-line message."""


def client() -> LangfuseAPI:
    """Langfuse REST client from environment variables.

    Uses the SDK's API client directly rather than Langfuse(), which would also
    start an OpenTelemetry exporter that a read-only CLI doesn't need.
    """
    from langfuse.api import LangfuseAPI

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    missing = [
        name
        for name, value in [("LANGFUSE_PUBLIC_KEY", public_key), ("LANGFUSE_SECRET_KEY", secret_key)]
        if not value
    ]
    if missing:
        raise FetchError(f"set {' and '.join(missing)} (and LANGFUSE_HOST if you self-host)")
    return LangfuseAPI(
        base_url=host(),
        username=public_key,
        password=secret_key,
        x_langfuse_public_key=public_key,
        timeout=30,
    )


def host() -> str:
    # Same precedence as the Langfuse SDK: the newer LANGFUSE_BASE_URL wins.
    url = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or DEFAULT_HOST
    return url.rstrip("/")


def fetch_observations(api: LangfuseAPI, trace_id: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    cursor = None
    while True:
        page = _call(
            api.observations.get_many,
            trace_id=trace_id,
            fields=FIELDS,
            limit=PAGE_SIZE,
            cursor=cursor,
        )
        observations.extend(json.loads(page.json())["data"])
        cursor = page.meta.cursor
        if not cursor or not page.data:
            return observations


def latest_trace_id(api: LangfuseAPI) -> str:
    # Results come newest first. The docs recommend bounding queries in time.
    page = _call(
        api.observations.get_many,
        is_root_observation=True,
        from_start_time=datetime.now(timezone.utc) - LATEST_WINDOW,
        fields="core",
        limit=1,
    )
    if not page.data or not page.data[0].trace_id:
        raise FetchError(
            f"no traces in the last {LATEST_WINDOW.days} days on {host()} "
            "(new traces can take a few seconds to show up)"
        )
    return page.data[0].trace_id


def load_file(path: Path) -> list[dict[str, Any]]:
    """Read observations saved as JSON: an API response ({"data": [...]}) or a list."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise FetchError(f"can't read {path}: {e}") from None
    if isinstance(data, dict):
        data = data.get("data", data.get("observations"))
    if not isinstance(data, list) or not all(isinstance(o, dict) and "id" in o for o in data):
        raise FetchError(f'{path} isn\'t a list of observations (expected {{"data": [...]}})')
    return data


def _call(method: Any, **kwargs: Any) -> Any:
    import httpx
    from langfuse.api.core import ApiError

    try:
        return method(**kwargs)
    except ApiError as e:
        if e.status_code in (401, 403):
            raise FetchError(
                f"Langfuse at {host()} rejected the keys "
                "(check LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST)"
            ) from None
        detail = " ".join(str(e.body).split())[:200]  # the body may be a whole HTML page
        raise FetchError(f"Langfuse at {host()} answered {e.status_code}: {detail}") from None
    except httpx.HTTPError as e:
        raise FetchError(f"can't reach Langfuse at {host()}: {e}") from None
