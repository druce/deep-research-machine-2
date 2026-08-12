---
name: sra-prefetch
description: Cold-start a ticker's evidence base — deterministic provider gather, shared macro gather, seven deep-research topics, then URL harvest and validation. Use when asked to prefetch or gather data for a ticker, to refresh stale bronze, or as the first phase of a build.
---

# sra-prefetch — deterministic gather + deep research + harvest (§11)

Everything mechanical is one `sra.py prefetch` call: the driver owns the fetcher
registry, the dependency waves, the freshness policies and the state commits.
The model work here is exactly seven web-research topics, and then getting their
URLs into bronze.

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

## Step 3 — The seven deep-research topics

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

**Preferred path — the harness `deep-research` Workflow, once per topic:**

```javascript
Workflow({ name: "deep-research", args: <topic prompt + ticker context> })
```

Append to every topic prompt: the ticker and company name, today's date, and this
instruction — *return the research body in markdown, and end with a
`## Sources` list of every URL you used, one per line.* You need those URLs; a
finding whose URL never reaches `cited_urls` never becomes bronze and cannot be
cited by anything downstream.

**Fallback — if `deep-research` is unavailable**, dispatch one `sra-researcher`
subagent per topic instead, all seven in a single message. Give each the topic
prompt, the ticker directory, the repo root, and the answer id
`<TODAY>_prefetch_<topic>`; the agent writes its own answer file and you skip
Step 4 for it. This is the same work with a smaller research budget, not a
degraded mode to apologize for.

## Step 4 — Write each Workflow result as an answer (Bash)

A Workflow returns text to you, so you write the answer file — the researcher
agent does this itself and needs nothing here. Write the body to
`/tmp/<TODAY>_prefetch_<topic>.md`, then, from the repo root:

```python
from datetime import date, datetime, timezone
from pathlib import Path

from lib.provenance import SourceMeta, write_answer

body = Path("/tmp/<TODAY>_prefetch_<TOPIC>.md").read_text(encoding="utf-8")
now = datetime.now(timezone.utc)
print(write_answer(Path("data/<TICKER>"), SourceMeta(
    id="<TODAY>_prefetch_<TOPIC>",
    ticker="<TICKER>",
    kind="research_answer",
    source="deep-research",
    url="",
    fetched_at=now.isoformat(),
    as_of=date.today().isoformat(),
    title="<TICKER> prefetch: <TOPIC>",
    fetch_tool="skills/sra-prefetch",
    fetch_cmd="",
    cited_urls=[
        "https://…",          # every URL from the topic's Sources list
    ],
), body))
```

`write_answer` puts it under `derived/answers/` and refuses to overwrite. The
answer is **never evidence** — it is the audit record of what the topic returned,
and its URLs are what become evidence in the next step.

## Step 5 — Harvest and gate (Bash)

```bash
uv run python sra.py fetch-urls <TICKER>
uv run python sra.py manifest <TICKER>
uv run python sra.py validate <TICKER>
uv run python sra.py wiki-log <TICKER> --entry "prefetch: <n> kinds fetched, <k> degraded, 7 topics, <m> urls harvested"
```

`fetch-urls` runs the driver's hardened, SSRF-controlled fetcher over every
answer's `cited_urls` and writes the URL→id maps the research synthesizer needs.
Individual URL failures are warnings and it still exits 0 — report the count.

`validate` is the fatal gate: exit 1 means a layer or provenance violation (model
text under `sources/`, a citation resolving nowhere, a credential in an
artifact). Fix it before any research runs on top of it.

## Report, then hand off

Report: kinds fetched, kinds degraded and why, topics completed, URLs harvested
versus failed, and `validate`'s result. Degraded kinds are load-bearing
information — a report written over a missing transcript should say so.

Next in a cold build (§23.1): `/sra-peers`, then the research loop
(`/sra-research`).
