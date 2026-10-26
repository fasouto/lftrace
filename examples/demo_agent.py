"""A fake trip-planning agent traced with Langfuse's @observe decorator.

It plans a rainy Saturday in Lisbon. It makes a plan, then researches: a
weather call times out and is retried, a retriever finds places and a tool
checks their opening hours. The agent is async, so the weather lookup runs at
the same time as the place finding, and every place is checked at the same
time. A writer sub-agent drafts the day, a guardrail flags a place with no
opening hours, and the writer revises. The steps are fixed, and without an
LLM key every run gives the same trace, with 1 error and 2 warnings. It needs
only your Langfuse keys:

    export LANGFUSE_PUBLIC_KEY=pk-lf-... LANGFUSE_SECRET_KEY=sk-lf-...
    export LANGFUSE_HOST=https://cloud.langfuse.com  # or your region / self-hosted URL
    uv run examples/demo_agent.py

With OPENROUTER_API_KEY set, the LLM steps are real calls through OpenRouter
(OPENROUTER_MODEL picks the model, default openai/gpt-4o-mini).
"""

from __future__ import annotations

import asyncio
import sys
import time

from langfuse import get_client, observe

from shared import complete, embed, run

QUESTION = "It might rain in Lisbon on Saturday. Plan my day."

# Opening hours by place. Tram 28 is a tram line, so it has none.
PLACES = {
    "Pastéis de Belém": "08:00-21:00",
    "MAAT": "10:00-19:00",
    "Tram 28": None,
    "Gulbenkian Museum": "10:00-18:00",
    "LX Factory": "09:00-23:00",
}


@observe(name="trip-planner", as_type="agent")
async def run_agent(question: str) -> str:
    plan(question)
    weather, hours = await research()
    return write_itinerary(question, weather, hours)


@observe(name="plan", as_type="generation")
def plan(question: str) -> str:
    return complete(
        "List the steps you'll take to answer, one short line each.",
        question,
        fake="1. Check the weather\n2. Find places and their opening hours\n3. Write the day",
    )


@observe(name="research", as_type="chain")
async def research() -> tuple[str, dict[str, str | None]]:
    async def find_places() -> dict[str, str | None]:
        # Langfuse stores times to the millisecond, so calls started together would
        # come out in a random order. Start each one a few ms after the previous.
        await asyncio.sleep(0.01)
        places = await search_places("indoor things to do in Lisbon when it rains")
        return await opening_hours(places)

    return await asyncio.gather(weather_with_retry("Lisbon"), find_places())


@observe(name="weather")
async def weather_with_retry(city: str) -> str:
    try:
        return await get_weather(city, attempt=1)
    except TimeoutError:  # @observe already marked the first call as an error
        return await get_weather(city, attempt=2)


@observe(name="get_weather", as_type="tool")
async def get_weather(city: str, attempt: int) -> str:
    if attempt == 1:
        await asyncio.sleep(0.8)
        raise TimeoutError(f"weather API didn't answer for {city} within 0.8s")
    await asyncio.sleep(0.3)
    return "Saturday: 70% chance of rain after 14:00, 16°C"


@observe(name="search_places", as_type="retriever")
async def search_places(query: str) -> list[str]:
    embed(query)
    await asyncio.sleep(0.2)
    return list(PLACES)


@observe(name="opening_hours")
async def opening_hours(places: list[str]) -> dict[str, str | None]:
    async def staggered(i: int, place: str) -> str | None:
        await asyncio.sleep(0.005 * i)  # see find_places: keeps the calls in listed order
        return await check_hours(place)

    found = await asyncio.gather(*(staggered(i, place) for i, place in enumerate(places)))
    return dict(zip(places, found))


@observe(name="check_hours", as_type="tool")
async def check_hours(place: str) -> str | None:
    await asyncio.sleep(0.1)
    hours = PLACES[place]
    if hours is None:
        get_client().update_current_span(
            level="WARNING", status_message=f"no opening hours listed for {place}"
        )
    return hours


@observe(name="itinerary-writer", as_type="agent")
def write_itinerary(question: str, weather: str, hours: dict[str, str | None]) -> str:
    notes = f"Weather: {weather}\nPlaces and opening hours:\n" + "\n".join(
        f"- {place}: {h or 'unknown'}" for place, h in hours.items()
    )
    day = draft(question, notes)
    problems = fact_check(day, hours)
    return revise(day, problems) if problems else day


@observe(name="draft", as_type="generation")
def draft(question: str, notes: str) -> str:
    return complete(
        "Plan the day as a short list of times and places. Put outdoor things before 14:00.",
        f"{question}\n\n{notes}",
        fake="09:00 Pastéis de Belém\n10:00 MAAT\n12:00 Tram 28\n14:00 Gulbenkian Museum\n17:30 LX Factory",
    )


@observe(name="fact-check", as_type="guardrail")
def fact_check(day: str, hours: dict[str, str | None]) -> list[str]:
    time.sleep(0.05)
    problems = [f"{place} has no opening hours" for place, h in hours.items() if h is None and place in day]
    if problems:
        get_client().update_current_span(level="WARNING", status_message="; ".join(problems))
    return problems


@observe(name="revise", as_type="generation")
def revise(day: str, problems: list[str]) -> str:
    return complete(
        "Fix these problems in the plan and keep it short.",
        day + "\n\nProblems:\n" + "\n".join(problems),
        fake="09:00 Pastéis de Belém\n10:00 MAAT\n12:00 Tram 28 (runs all day, no opening hours)\n"
        "14:00 Gulbenkian Museum, indoors when the rain starts\n17:30 LX Factory",
    )


if __name__ == "__main__":
    sys.exit(run(run_agent, QUESTION))
