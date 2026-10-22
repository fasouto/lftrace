# lftrace

[![Tests](https://github.com/fasouto/lftrace/actions/workflows/tests.yml/badge.svg)](https://github.com/fasouto/lftrace/actions/workflows/tests.yml)

Your app's agent leaves a trace in [Langfuse](https://langfuse.com). lftrace turns that
trace into text in your terminal, so you can read it, and so can your coding agent.

```sh
lftrace --latest             # draw the most recent trace
lftrace --latest --mermaid   # the same trace as Mermaid text, for Claude Code or any LLM
```

Paste the `--mermaid` output into Claude Code, or let it run the command itself, and it
sees what your app's agent did: the steps in the order they ran, with durations, tokens,
cost and every error message in full. No screenshots, no browser.

lftrace is a prototype. See [Limitations](#limitations) for what it doesn't do yet.

![lftrace demo](docs/demo.gif)

```
$ lftrace --latest
Trace c8747b0761d23f523151d65142f00555 · 1.63s · 132 tokens · $0.00 · 1 warning

                       ┌────────────────────┐      ┌─────────────────────┐
                       │     1. triage      │      │                     │
                       │ GENERATION · 404ms │      │ 2.1. lookup_account │
                  ╭───►│      fake-llm      │ ╭───►│    TOOL · 301ms     │
                  │    │       24 tok       │ │    │                     │
                  │    └────────────────────┘ │    └─────────────────────┘
                  │                           │
                  │                           │
┌───────────────┐ │    ┌────────────────────┐ │    ┌─────────────────────┐   ┌────────────────────┐
│               │ │    │                    │ │    │                     │   │ 2.2.1. embed_query │
│  support-bot  │ │    │  2. billing-agent  │ │    │ 2.2. search_policy  │   │ EMBEDDING · 106ms  │
│ AGENT · 1.63s ├─┼───►│   AGENT · 816ms    ├─┼───►│  RETRIEVER · 308ms  ├──►│   fake-embedding   │
│               │ │    │                    │ │    │                     │   │       8 tok        │
└───────────────┘ │    └────────────────────┘ │    └─────────────────────┘   └────────────────────┘
                  │                           │
                  │                           │
                  │    ┌────────────────────┐ │    ┌─────────────────────┐
                  │    │      3. reply      │ │    │     2.3. refund     │
                  │    │ GENERATION · 406ms │ │    │    TOOL · 206ms     │
                  ╰───►│      fake-llm      │ ╰───►│ ! €29 refund needs  │
                       │      100 tok       │      │      approval       │
                       └────────────────────┘      └─────────────────────┘

! 2.3. refund (TOOL): €29 refund needs approval
```

In a terminal, errors are red and warnings yellow.

## Install

```sh
uv tool install git+https://github.com/fasouto/lftrace   # puts lftrace on your PATH
```

Or, from a clone of this repo, run it in place with `uv run lftrace`. It needs Python 3.10+.

## Quick start

Run the demo agent (it needs only Langfuse keys, no LLM key), then look at its trace:

```sh
export LANGFUSE_PUBLIC_KEY=pk-lf-... LANGFUSE_SECRET_KEY=sk-lf-... LANGFUSE_HOST=https://cloud.langfuse.com
uv run examples/demo_agent.py
uv run lftrace --latest
```

If you'd rather keep the keys in a file, `cp .env.example .env`, fill it in, and use
`uv run --env-file .env ...`. A new trace can take a few seconds to show up.

`examples/` has three fake agents to try it on. Each one sends a trace with a different
shape:

- `demo_agent.py`: a trip planner for a rainy day in Lisbon. A weather call times out and
  is retried, a retriever finds places and a tool checks their opening hours. A writer
  sub-agent drafts the day, a guardrail flags a place with no opening hours, and the
  writer revises.
- `support_bot.py`: a support bot hands a billing question to a sub-agent, which looks up
  the account, searches the refund policy and asks for a refund that needs a human's
  approval.
- `coding_agent.py`: a coding agent needs five attempts to fix a failing test. lftrace
  folds the middle attempts into `+3 more` and counts their errors.

They need only Langfuse keys. With `OPENROUTER_API_KEY` set, their LLM steps are real calls
through [OpenRouter](https://openrouter.ai).

## With a coding agent

In Claude Code, `!` runs a command and adds its output to the conversation:

```
> !lftrace --latest --mermaid
> Why did get_weather fail the first time, and why does the fact-check warn about Tram 28?
```

The Mermaid output starts with `%%` comment lines that carry the totals and every error
and warning in full, so nothing is lost when a box shortens a message. It's still valid
Mermaid, so you can also paste it into a Mermaid viewer such as
[mermaid.live](https://mermaid.live).

## Usage

```
lftrace <trace_id>           show a trace
lftrace --latest             show the most recent trace (from the last 7 days)
lftrace --file trace.json    show a trace saved as JSON, without network

  --mermaid    print the Mermaid source instead of the drawing
  --depth N    show only the first N levels
```

The header has the trace's totals: duration, tokens, cost, errors and warnings. Each box is
one step, with arrows from each step to the steps it ran, left to right. Steps are
numbered in the order they ran: `2.1` is the first step inside step 2. A box shows the
step's name, its type and duration, and for LLM calls the model, tokens and cost when
Langfuse has them. A failed step ends with `✗` and the start of its message, a warning
with `!`; the full messages are listed under the diagram.

To keep big traces readable, a step with more than three children shows the first and the
last with a `+N more` box in between, and `--depth` folds deeper levels into a `+N nested`
line. Hidden errors and warnings are still counted in those boxes and listed under the
diagram with their step numbers.

### Credentials

lftrace reads `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_HOST` (EU cloud,
`https://cloud.langfuse.com`, if unset). Like the Langfuse SDK, it also accepts
`LANGFUSE_BASE_URL`, which wins over `LANGFUSE_HOST`. It only reads data and never prints
the keys.

### Saving a trace

`--file` takes the JSON that the Langfuse API returns for a trace's observations, so you
can save one with curl:

```sh
curl -s -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
  "$LANGFUSE_HOST/api/public/v2/observations?traceId=<trace_id>&fields=core,basic,model,usage&limit=1000" \
  > trace.json
lftrace --file trace.json
```

## How it works

1. Fetch the trace's observations with the Langfuse Python SDK
   (`api.observations.get_many(trace_id=...)`, all pages).
2. Build a tree from each observation's parent id, with siblings ordered by start time.
3. Write it as a left-to-right Mermaid flowchart (`--mermaid` shows it).
4. Draw it with [Termaid](https://github.com/fasouto/termaid) (`termaid.render_rich()`).

lftrace works with Langfuse Cloud and recent self-hosted Langfuse versions (it uses the
v2 observations API). On Python 3.10 it installs Langfuse SDK v4; on 3.11+, v5.

## Limitations

lftrace is a prototype. Before it's ready for production, Termaid, the library that
draws the diagrams, needs a few changes: drawing steps with many children without
crossing lines, reading `<br>` line breaks in labels, and coloring label text. Until then,
lftrace works around them, and some of the limits below come from that.

- **Three children per step.** A step with more children shows the first and the last,
  with `+N more` in between, because Termaid 0.9.1 draws crossing lines when a node has
  more than three children. Errors and warnings in the hidden steps are still counted
  and listed.
- **Deep traces get wide.** Each level adds about 35 columns, so four levels fit in 120.
  For deeper traces, use a wider terminal or `--depth`.
- **No parallelism.** Steps are ordered by start time, so steps that ran at the same time
  look like they ran one after another.
- **No inputs, outputs or scores.** The diagram shows each step's place in the tree, its
  timing, tokens, cost and status. Open the trace in Langfuse for the rest.
- **Short labels.** Boxes cut names at 40 characters and messages at 30. The full
  messages are listed under the diagram and in the `--mermaid` comments.
- **`\n` line breaks.** Labels break lines with `\n` because Termaid 0.9.1 doesn't read
  `<br>`. mermaid.live and GitHub show them as line breaks, but some viewers show `\n`
  as text.
- **`--latest` looks back 7 days**, and a new trace can take a few seconds to show up.

## Development

```sh
uv sync
uv run pytest
uvx ruff check && uvx ruff format --check
```

The tests use a saved trace (`tests/fixtures/trace.json`) and a fake HTTP server, so they
don't need network or keys.

The GIF is recorded with [vhs](https://github.com/charmbracelet/vhs): `vhs docs/demo.tape`
runs the three examples against the Langfuse project in your `.env`.

## License

MIT
