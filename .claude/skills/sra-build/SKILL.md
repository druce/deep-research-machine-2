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

## Steps 1–2 — Initialize and gather

```bash
uv run python sra.py init <TICKER>
uv run python sra.py status <TICKER>
```

`status` is the resume oracle for this phase. If nothing is stale and
`sources/` already holds documents, the gather is done — skip to step 3.
Otherwise:

Invoke `/sra-prefetch TICKER [--peers "<CSV>"]`. It runs the deterministic
gather, the seven deep-research topics and the URL harvest.

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

## Step 7 — Research

Invoke `/sra-research TICKER all`. This is the largest phase — roughly 23
answerers and 15 synthesizers across up to three rounds — and the primary lever
if the run is over budget: §23.3 names dropping to two rounds as the first cut.

**Resume:** skip when every section's wiki page exists and the open-question
count is below the round's stopping threshold. Check with
`sra.py questions <TICKER> --status open`.

## Steps 8–10 — Lint and gate again

```bash
uv run python sra.py wiki-lint <TICKER>
```

Then invoke `/sra-lint TICKER`, which is the model-judgment half and runs only
after the deterministic pass (§22.1). Its findings become ledger questions with
`--origin lint`.

If the lint raises questions that would change a section's argument, run one
more `/sra-research TICKER <section>` round before writing. If it raises only
`partial` citations, note them and continue — writing will not make them worse.

```bash
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
  "char_caps": "<{section: max characters} = word_target x 8>"
}
```

The run directory is the newest `reports/<date>/` with **no `snapshot.json`** in
it, or today's date if there is none. A stamped run is immutable.

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

## Report

Open with the verdict — rating, fair value, implied return — because that is
what the user asked for, then:

- what the report is built on: source count, sections written, exhibits selected,
  citations resolved;
- what degraded: stale or failed kinds, sections whose wiki page was thin,
  questions left open or deferred;
- the run against §23.3's ceilings (80 subagents, 6M tokens, 60 minutes) and any
  `check_budgets` violation, verbatim;
- the snapshot name and where the PDF is.

If any phase was skipped by resume, say which — a reader has to know whether
this run researched anything or just re-assembled yesterday's evidence.
