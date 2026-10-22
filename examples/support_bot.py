"""A fake support bot that hands billing questions to a sub-agent.

A triage step routes the question to a billing agent. The billing agent looks
up the account, searches the refund policy and asks for a refund, which needs
a human's approval, so the trace has 1 warning. A final step writes the reply.
It needs only your Langfuse keys:

    uv run examples/support_bot.py

With OPENROUTER_API_KEY set, the LLM steps are real calls through OpenRouter.
"""

from __future__ import annotations

import sys
import time

from langfuse import get_client, observe

from shared import complete, embed, run

QUESTION = "Why was I charged twice this month?"
REFUND_LIMIT = 20  # euros the bot may refund without a human


@observe(name="support-bot", as_type="agent")
def run_agent(question: str) -> str:
    team = triage(question)
    notes = billing_agent(question) if "billing" in team.lower() else ""
    return reply(question, notes)


@observe(name="triage", as_type="generation")
def triage(question: str) -> str:
    return complete("Which team should answer: billing, tech or sales? One word.", question, fake="billing")


@observe(name="billing-agent", as_type="agent")
def billing_agent(question: str) -> str:
    account = lookup_account("cus_1042")
    policy = search_policy(question)
    charges = account["charges"]
    duplicate = next((charge for i, charge in enumerate(charges) if charge in charges[:i]), None)
    status = refund(duplicate) if duplicate else "no duplicate charge"
    return f"Charges: {account['charges']}\nPolicy: {policy}\nRefund: {status}"


@observe(name="lookup_account", as_type="tool")
def lookup_account(customer_id: str) -> dict:
    time.sleep(0.3)
    return {"id": customer_id, "plan": "Pro", "charges": [("2026-10-01", 29.0), ("2026-10-01", 29.0)]}


@observe(name="search_policy", as_type="retriever")
def search_policy(question: str) -> str:
    embed("embed_query", question)
    time.sleep(0.2)
    return "Duplicate charges are refunded in full within 5 working days."


@observe(name="refund", as_type="tool")
def refund(charge: tuple[str, float]) -> str:
    time.sleep(0.2)
    day, amount = charge
    if amount > REFUND_LIMIT:
        get_client().update_current_span(
            level="WARNING", status_message=f"€{amount:.0f} refund needs approval"
        )
        return f"€{amount:.2f} from {day} sent for approval"
    return f"€{amount:.2f} from {day} refunded"


@observe(name="reply", as_type="generation")
def reply(question: str, notes: str) -> str:
    return complete(
        "Answer the customer in two short sentences using the notes.",
        f"{question}\n\nNotes:\n{notes}",
        fake="You were charged €29 twice on 1 October by mistake. "
        "A colleague is approving the refund, and you'll get it within 5 working days.",
    )


if __name__ == "__main__":
    sys.exit(run(run_agent, QUESTION))
