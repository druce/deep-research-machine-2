---
name: sra-prefetch
description: Cold-start a ticker's evidence base — deterministic provider gather, shared macro gather, seven budgeted research topics, then URL harvest and validation. Use when asked to prefetch or gather data for a ticker, to refresh stale bronze, or as the first phase of a build.
---

# sra-prefetch — deterministic gather + topic research + harvest (§11)

Everything mechanical is one `sra.py prefetch` call: the driver owns the fetcher
registry, the dependency waves, the freshness policies and the state commits.
The model work here is exactly seven web-research topics, and then getting their
URLs into bronze.

**Seven subagents. Not seventy, not seven hundred.** This phase used to dispatch
the harness `deep-research` Workflow once per topic, and the TOST build measured
what that cost: **728 subagents, 20.65M input tokens, 34 minutes — 93% of the
entire run**, to produce 202 URLs, of which the harvest then kept 126. The
Workflow is harness-owned, so no budget, model or effort could be imposed on it.
It is retired here (§11.2) and must not come back.

## Usage

`/sra-prefetch TICKER [--stale-only] [--peers "AAA,BBB"]`

- `--stale-only` re-fetches only kinds whose freshness policy has expired — the
  right default for an incremental update, wrong for a cold build.
- `--peers` passes the user's own comparables through to the peers gather.
  Selection itself is `/sra-peers`, a separate phase.

## Step 1 — Initialize and gather (Bash)

```bash
uv run python sra.py init <TICKER>
uv run python sra.py prefetch <TICKER> [--stale-only] [--peers "<CSV>"]
```

`init` is idempotent and leaves an existing tree's state untouched.

`prefetch` prints `{"fetched": …, "skipped": …, "warnings": …, "errors": …}` and
its exit code is the thing to read:

- **0** — everything wanted succeeded.
- **2** — one or more kinds failed. This is DEGRADED, not fatal: the build
  continues without them (§11), and you report which kinds are missing so the
  gap is visible in the report rather than silent. Re-run just those later with
  `--kinds`.
- **1** — nothing ran (unknown kind, uninitialized ticker, held lock). Fix and
  retry; do not proceed to the research topics against an empty corpus.

## Step 2 — Shared macro (Bash)

Macro lives once, under `data/_MACRO/`, and is shared by every ticker:

```bash
uv run python sra.py init _MACRO
uv run python sra.py prefetch-macro --stale-only
```

A dead series is a warning and exits 0 by design — macro is context for every
ticker, and one broken series must not block a build (§12.3).

## Step 3 — The seven research topics

Topics, one per prompt file in `prompts/prefetch_research/`:

```text
news  business_profile  executives  business_model  competitive  risk  thesis
```

Each prompt has `{company}` and `{symbol}` placeholders. Get the company name the
profile fetcher recorded (fall back to the ticker if it is null):

```bash
uv run python - <<'PY'
from pathlib import Path
from lib.statefile import load_state

state = load_state(Path("data/<TICKER>"))
print(state.get("company_name") or state["ticker"])
PY
```

**Dispatch seven `sra-researcher` subagents, all in ONE message** — one per
topic, so they run concurrently. There is no other path; the `deep-research`
Workflow is retired.

Give each agent:

- the two prompt files to read — `prompts/prefetch_research/_shared.md` (the
  budget and the citation contract) and `prompts/prefetch_research/<topic>.md`
  (the seed queries), with `{company}` and `{symbol}` substituted;
- the absolute ticker directory and the repo root;
- the answer id it must use verbatim: `<TODAY>_prefetch_<topic>`;
- its `{log_path}`: `reports/<RUN>/log/<NN>_deep-research_<topic>.md`.

Each agent writes its own answer file under `derived/answers/` and its own task
log. You write neither — which is the point: relaying seven full research bodies
through your context was itself a cost, and the answer is not evidence anyway.

**Do not raise the budget.** `_shared.md` caps each topic at 14 searches and
8–12 page reads, and tells the agent to take every figure from `structured/` and
the filings rather than re-verifying it on the web. That last rule is where the
old cost lived: the numbers are already gathered deterministically, and
adversarially re-checking them against the web bought nothing. If a topic comes
back thin, the fix is a better seed query in its prompt file, or a question in
the ledger for `/sra-research` to pick up — not a bigger budget here.

`sra-researcher` runs at `effort: medium` from its own frontmatter (§21.1). The
Agent tool takes no effort parameter, so that frontmatter is the only dial; do
not try to pass one.

**Task logs (§23.4).** The agents write their own, so give each its `{log_path}`
and require the standard frontmatter — `purpose: deep-research`,
`section: <topic>`, `round: 0`, `label: "deep-research:<topic>"`,
`started_at`/`finished_at`, `status`, `outputs`. After Step 4, append to each
log which of its URLs survived the harvest: that is the one place harvest loss
is recorded, and it is the number to watch.

## Step 4 — Harvest and gate (Bash)

```bash
uv run python sra.py fetch-urls <TICKER>
uv run python sra.py manifest <TICKER>
uv run python sra.py validate <TICKER>
uv run python sra.py wiki-log <TICKER> \
    --entry "prefetch: <n> kinds fetched, <k> degraded, 7 topics, <m> urls harvested" \
    --agents 7 --tokens <T> --minutes <M> --run <RUN>
```

`fetch-urls` runs the driver's hardened, SSRF-controlled fetcher over every
answer's `cited_urls` and writes the URL→id maps the research synthesizer needs.
Individual URL failures are warnings and it still exits 0 — report the count.

It escalates each URL through three transports — httpx, then a headless
browser, then Bright Data (§8.3.2) — because most harvest failures are publisher
bot-blocking rather than dead links. Read its output, not just its exit code:

- `errors` are URLs no tier could retrieve. Each claim resting on one is now
  uncitable, so the count matters.
- `truncated` are pages stored but cut at the character cap. A partial capture
  is more dangerous than a failed one, because it gets cited with full
  confidence.

A body that comes back thin or reads as a bot wall is recorded as a failure
rather than stored — a publisher's "Access denied" page in bronze under a
plausible id is worse than an honest gap.

`validate` is the fatal gate: exit 1 means a layer or provenance violation (model
text under `sources/`, a citation resolving nowhere, a credential in an
artifact). Fix it before any research runs on top of it.

## Report, then hand off

Report: kinds fetched, kinds degraded and why, topics completed, URLs harvested
versus failed, and `validate`'s result. Degraded kinds are load-bearing
information — a report written over a missing transcript should say so.

Next in a cold build (§23.1): `/sra-peers`, then the research loop
(`/sra-research`).
