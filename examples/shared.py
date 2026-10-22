"""Helpers the example agents share: the LLM call and running an agent with tracing."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from collections.abc import Callable

from langfuse import Langfuse, get_client

OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")


def complete(system: str, prompt: str, fake: str) -> str:
    """Answer with the LLM and record its model, tokens and cost on the current generation.

    Without OPENROUTER_API_KEY it returns `fake` and records made-up token counts.
    """
    langfuse = get_client()
    if not os.environ.get("OPENROUTER_API_KEY"):
        time.sleep(0.4)
        langfuse.update_current_generation(
            model="fake-llm",
            usage_details={"input": count_tokens(system + prompt), "output": count_tokens(fake)},
        )
        return fake
    text, model, usage = ask_openrouter(system, prompt)
    langfuse.update_current_generation(
        model=model,
        usage_details={"input": usage.get("prompt_tokens", 0), "output": usage.get("completion_tokens", 0)},
        cost_details={"total": usage["cost"]} if "cost" in usage else None,
    )
    return text


def embed(name: str, text: str) -> list[float]:
    """A fake embedding, traced as an embedding observation."""
    # update_current_generation() would turn the embedding into a generation,
    # so this observation sets its model and usage when it starts.
    with get_client().start_as_current_observation(
        name=name,
        as_type="embedding",
        input=text,
        model="fake-embedding",
        usage_details={"input": count_tokens(text)},
    ):
        time.sleep(0.1)
        return [0.0] * 8


def count_tokens(text: str) -> int:
    return max(1, len(text) // 4)  # about four characters per token


def ask_openrouter(system: str, prompt: str) -> tuple[str, str, dict]:
    body = {
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.load(response)
    return (
        data["choices"][0]["message"]["content"],
        data.get("model", OPENROUTER_MODEL),
        data.get("usage", {}),
    )


def run(agent: Callable[..., str], question: str) -> int:
    """Run an @observe-decorated agent in a new trace, print its answer and the trace id."""
    missing = [name for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY") if not os.environ.get(name)]
    if missing:
        print(
            f"Set {' and '.join(missing)} first (and LANGFUSE_HOST if you're not on EU cloud).",
            file=sys.stderr,
        )
        return 1

    trace_id = Langfuse.create_trace_id()
    try:
        print(agent(question, langfuse_trace_id=trace_id))
    finally:
        get_client().flush()
    print(f"\nTrace {trace_id} sent. Show it with `uv run lftrace --latest`.")
    return 0
