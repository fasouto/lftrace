"""A fake coding agent that needs five attempts to fix a failing test.

Each attempt asks the LLM for an edit and runs the tests. The first four
attempts fail, so lftrace folds the middle ones into "+3 more" and counts
their errors. Every prompt carries the earlier failures, so the token counts
grow with each attempt. It needs only your Langfuse keys:

    uv run examples/coding_agent.py

With OPENROUTER_API_KEY set, the LLM steps are real calls through OpenRouter.
"""

from __future__ import annotations

import sys
import time

from langfuse import observe

from shared import complete, run

TASK = "Make test_invoice_total pass."
SOURCE = "def invoice_total(lines):\n    return lines[0].amount\n"
ATTEMPTS = 5
# Why test_invoice_total fails after each attempt; the last attempt passes.
FAILURES = [
    "expected 58.00, got 29.00",
    "expected 58.00, got 0.00",
    "TypeError: unsupported operand type(s) for +: 'float' and 'str'",
    "expected 58.00, got 57.99",
]


class TestFailure(Exception):
    pass


@observe(name="fix-tests", as_type="agent")
def run_agent(task: str) -> str:
    plan(task)
    attempts = fix(task)
    return open_pr(attempts)


@observe(name="plan", as_type="generation")
def plan(task: str) -> str:
    return complete(
        "Say in one line how you'll fix it.", task, fake="Sum the invoice lines in invoice_total()."
    )


@observe(name="attempts", as_type="chain")
def fix(task: str) -> int:
    history: list[str] = []
    for number in range(1, ATTEMPTS + 1):
        failure = attempt(task, number, history)
        if failure is None:
            return number
        history.append(failure)
    raise RuntimeError(f"tests still fail after {ATTEMPTS} attempts")


@observe(name="attempt")
def attempt(task: str, number: int, history: list[str]) -> str | None:
    """Edit the code and run the tests. Returns the failure, or None when they pass."""
    edit_code(task, history)
    try:
        run_tests(number)
    except TestFailure as e:  # @observe already marked the test run as an error
        return str(e)
    return None


@observe(name="edit_code", as_type="generation")
def edit_code(task: str, history: list[str]) -> str:
    return complete(
        "Return a unified diff for invoices.py that fixes the failure.",
        f"{task}\n\ninvoices.py:\n{SOURCE}" + "".join(f"\nAn earlier attempt failed: {f}" for f in history),
        fake="-    return lines[0].amount\n+    return round(sum(line.amount for line in lines), 2)",
    )


@observe(name="run_tests", as_type="tool")
def run_tests(number: int) -> str:
    time.sleep(0.3)
    if number <= len(FAILURES):
        raise TestFailure(FAILURES[number - 1])
    return "12 passed"


@observe(name="open_pr", as_type="tool")
def open_pr(attempts: int) -> str:
    time.sleep(0.2)
    return f"Opened PR #128: fix invoice_total (tests pass after {attempts} attempts)"


if __name__ == "__main__":
    sys.exit(run(run_agent, TASK))
