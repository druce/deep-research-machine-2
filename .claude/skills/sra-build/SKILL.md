---
name: sra-build
description: Cold-build an equity research report for a ticker end to end — gather, peers, research, lint, write wave, polish, charts, assemble, snapshot. Resumable, so a re-run skips phases whose output is already fresh. Use when asked to build, research or produce a report for a ticker from scratch.
---

# sra-build — the cold-build orchestrator (§23.1)

Eighteen phases. Every mechanical one is a `sra.py` subcommand and every model
one is a skill or a workflow that already exists — this skill's whole job is
order, resume, and the accounting at the end.

Two rules that govern the whole run:

- **Ask about peers exactly once**, at step 0, before anything else. Never again,
  in any later phase.
- **A phase whose output exists and is fresh is skipped.** §23.1: "re-running
  `/sra-build` skips completed fresh phases." A cold build that dies at step 12
  must not re-fetch bronze on the retry.

## Usage

`/sra-build TICKER [--length short|standard|long] [--peers "AAA,BBB,CCC"]`

`--length` picks the `length_presets` multiplier in `sections.yaml`
(`short` 0.40, `standard` 0.75, `long` 1.00) and defaults to `standard`. It is
the one knob that changes the shape of the report rather than its content.

## Step 0 — Peers, once

If `--peers` was supplied, use it and **do not ask**. Otherwise ask the user
once, in one message:

> Which companies do you consider this one's closest comparables? Name as many
> as you like, or say "you choose" and I will select them from the data.

Record the answer and move on. Do not block on it: "you choose" is a complete
answer, and `/sra-peers` handles both paths.

## Steps 1–2 — Initialize, open the run, gather

```bash
uv run python sra.py init <TICKER>
uv run python sra.py status <TICKER>
```

Open the run directory **before anything dispatches**. Every agent from here on
writes its own task log into `reports/<RUN>/log/` (§23.4), and an agent with
nowhere to log leaves a hole in the run log that cannot be filled later — the
information existed only in that agent's context.

```bash
uv run python - <<'PY'
from datetime import datetime, timezone
from pathlib import Path
from lib.render.runs import current_run
from lib.run_stats import start_run, write_run_stats

d = Path("data/<TICKER>")
run = current_run(d, datetime.now(timezone.utc).date())
(run / "log").mkdir(parents=True, exist_ok=True)
write_run_stats(run, start_run(datetime.now(timezone.utc).isoformat()))
print(run)
PY
```

Use that directory as `<RUN>` for the rest of the build. `current_run` returns
the newest `reports/<date>/` with **no `snapshot.json`** in it, or today's date
if there is none: a stamped run is immutable, so a second build on the same day
gets `<date>_2` rather than writing into yesterday's report.

**Resume:** a run whose `run_stats.json` already has a `started_at` is the run in
progress — keep it, and do not restamp.

`status` is the resume oracle for this phase. If nothing is stale and
`sources/` already holds documents, the gather is done — skip to step 3.
Otherwise:

Invoke `/sra-prefetch TICKER [--peers "<CSV>"]`. It runs the deterministic
gather, the seven budgeted research topics and the URL harvest. That phase is
**seven subagents**, not seven hundred: if it reports more, the retired
`deep-research` Workflow has crept back in (§11.2) and the build should stop.

A failure of `profile`, `prices` or `financials` is fatal (§11.1 minimum viable
input) — stop the build and report it. Every other kind degrades: record it in
`degraded_kinds` and continue.

## Steps 3–5 — Macro, manifest, gate

```bash
uv run python sra.py prefetch-macro --stale-only
uv run python sra.py manifest <TICKER>
uv run python sra.py validate <TICKER>
```

Macro is shared across tickers, so `--stale-only` is right here: another
ticker's build may already have fetched today's series. A macro-series failure
is non-fatal (§22.3).

`validate` is fatal and has no `--force`. Exit 1 here means the gather produced
something malformed — fix it before spending a single research agent on it.

## Step 6 — Peers

Invoke `/sra-peers TICKER [--peers "<CSV>"]` with the answer from step 0.

**Resume:** skip when `derived/peers/peers_selected.json` exists and postdates
`peers_candidates.json`.

## Step 6b — Peer fundamentals (Bash, deterministic)

```bash
uv run python sra.py prefetch-peers <TICKER> --stale-only
```

The peer comparison table reads each comparable's OWN bronze. Without this every
cell renders `N/A`, which reads to a reader as a data limitation and is not one.

It must run AFTER step 6: `prefetch` cannot gather the winners before they are
chosen, and `prefetch --peers` only feeds the candidate list.

**Resume:** skip when every symbol in `derived/peers/peers_selected.json` has a
`structured/key_ratios_computed.json` under its own `data/<PEER>/` tree.

Report the `warnings` list. A peer whose provider failed is not fatal — say
which one, and note that its row will read N/A.

## Step 7 — Research

Invoke `/sra-research TICKER all`. This is the largest phase — roughly 23
answerers and 15 synthesizers across up to three rounds — and the primary lever
if the run is over budget: §23.3 names dropping to two rounds as the first cut.

**Resume:** skip when every section's wiki page exists and the open-question
count is below the round's stopping threshold. Check with
`sra.py questions <TICKER> --status open`.

## Steps 8–10 — Lint and gate again

```bash
uv run python sra.py wiki-index <TICKER>
uv run python sra.py wiki-lint <TICKER>
```

`wiki-index` first: the lint checks that every page is listed in it, and any
page edited outside `/sra-research` leaves the index describing the previous
version.

Then invoke `/sra-lint TICKER`, which is the model-judgment half and runs only
after the deterministic pass (§22.1). Its findings become ledger questions with
`--origin lint`.

If the lint raises questions that would change a section's argument, run one
more `/sra-research TICKER <section>` round before writing. If it raises only
`partial` citations, note them and continue — writing will not make them worse.

`missing-summary` warnings are worth clearing before you write: the wiki index
is what the chartbook and lint agents read to find their way around, and a page
with no `summary:` shows up there as whatever its prose happened to open with.

```bash
uv run python sra.py wiki-index <TICKER>   # again, if the lint pass edited pages
uv run python sra.py validate <TICKER>
```

## Step 11 — Write wave

Run `workflows/write_wave.js` through the **Workflow tool** (it is a workflow
script, not a shell command) with args:

```json
{
  "ticker": "<TICKER>",
  "company": "<company name from profile_yahoo>",
  "workdir": "<absolute path to data/<TICKER>>",
  "report_date": "<the run directory name>",
  "sections": "<[{id, title, wiki_page, word_target, hard_checks}] from sections.yaml>",
  "char_caps": "<{section: max characters} = word_target x 10>"
}
```

`report_date` is the `<RUN>` opened at step 1. A stamped run is immutable.

`char_caps` is a **runaway guard, not the target**. At ×10 it sits roughly 25%
above `word_target`, and the write wave applies it as `max_length_prose`, which
excludes draft citation ids. It used to be ×8 counting everything, and every
SPCX section came in within 1.3% of its cap — the writers were shaving
characters, not writing to a target. The section critic enforces the actual
word target; this only catches a draft that has run away.

The seven sections run as seven independent chains launched together, ordered
longest-first, each grouped in the progress display under its own section title.
Six run at once and one queues — the workflow concurrency cap is
`min(16, cores - 2)` and has no setting (§23.1). Do not try to raise it, and do
not split the wave into two Workflow calls to get around it.

**Resume:** skip sections whose draft already exists and passes its hard checks.

## Steps 12–18 — Polish, charts, assemble, snapshot

Invoke `/sra-assemble TICKER`. It owns exactly these seven steps in §16.4's
order — polish chain, `charts`, `charts --verdict`, `/sra-chartbook`,
`assemble`, `validate`, `snapshot` — and it is the only place the polish-shape
decision is made. Do not run those commands here as well.

## Accounting — and it is not optional

Record every agent as it completes into `reports/<run>/run_stats.json` (§23.4):

```bash
uv run python - <<'PY'
from pathlib import Path
from lib.run_stats import load_run_stats, record_subagent, write_run_stats

run = Path("data/<TICKER>/reports/<RUN>")
stats = load_run_stats(run)
record_subagent(stats, purpose="answerer", section="valuation", round_=1,
                input_tokens=61200, output_tokens=3100)
write_run_stats(run, stats)
PY
```

`purpose` must come from §23.4's vocabulary — the module rejects anything else,
which is the point. Where a phase reports one total for several agents rather
than per-agent counts, apportion it and pass `estimated=True` so the record says
which figures were measured.

**Use the real numbers where they exist.** Every Agent-tool dispatch returns
`subagent_tokens` and `duration_ms` in its task result. Use them — guessing is
what made every entry of the last PANW run `estimated`.

But `subagent_tokens` is a **combined** total, not an input/output split, and
`record_subagent` wants both. So split it at the observed ratio for that agent
type (prefetch topics and answerers run about 92% input / 8% output; writers
nearer 90/10) and pass `estimated=True`. The TOTAL is measured and the split is
apportioned — that is exactly the distinction the flag records, and claiming a
measured split would be worse than admitting an apportioned one.

`duration_ms` is exact; it needs no flag. Workflow agents return no usage at
all, so those are apportioned end to end.

`section` and `round` are not decoration: they are the key the run log joins
task logs on. An entry recorded without them cannot be matched to the agent that
wrote the log, and shows up under "Unattributed".

At the end of the build, stamp the finish and check the budgets:

```bash
uv run python - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path
from lib.run_stats import check_budgets, finish_run, load_run_stats, write_run_stats

run = Path("data/<TICKER>/reports/<RUN>")
stats = finish_run(load_run_stats(run), datetime.now(timezone.utc).isoformat())
write_run_stats(run, stats)
print(json.dumps({"totals": stats["totals"],
                  "violations": check_budgets(stats)}, indent=2))
PY
```

Then assemble the audit log and close the journal:

```bash
uv run python sra.py run-log <TICKER> --run <RUN>
uv run python sra.py wiki-log <TICKER> \
    --entry "build: <n> sources, 7 sections, <k> exhibits, <rating>" \
    --agents <N> --tokens <T> --minutes <M> --run <RUN>
```

`run-log` runs **after** `finish_run`, so the log carries the wall clock and any
budget violation. Read its "Unattributed" section before you report: agents
listed there either wrote no task log or were recorded without a matching
`section`/`round`, and both are accounting defects worth naming.

## Report

Open with the verdict — rating, fair value, implied return — because that is
what the user asked for, then:

- what the report is built on: source count, sections written, exhibits selected,
  citations resolved;
- what degraded: stale or failed kinds, sections whose wiki page was thin,
  questions left open or deferred;
- the run against §23.3's ceilings (100 subagents, 6M tokens, 60 minutes) and any
  `check_budgets` violation, verbatim;
- the snapshot name, where the PDF is, and the path to `run_log.md`.

If any phase was skipped by resume, say which — a reader has to know whether
this run researched anything or just re-assembled yesterday's evidence.
