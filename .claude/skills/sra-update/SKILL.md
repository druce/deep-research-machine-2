---
name: sra-update
description: Incrementally update an existing report — refresh stale evidence, run directed research on a specific question, or redraft a section, then re-assemble. Never re-asks for peers. Use when asked to update, refresh, or dig into something on a ticker that already has a report.
---

# sra-update — the incremental orchestrator (§23.2)

Three flows, chosen by what the user said, not by what the ticker looks like:

| The user said | Flow |
|:---|:---|
| "update PANW", "refresh it" | **bare refresh** |
| "research X", "what about Y?" (quoted instructions) | **directed research** |
| "rewrite the valuation section", "fix the conclusion" | **report-only edit** |

An invocation may carry several quoted instructions; they batch into one
research round.

**Never ask about peers.** The peer set was chosen when the report was built and
is durable silver (§13.5). If it genuinely needs to change, the user says so and
the answer is `/sra-peers TICKER --peers "..."`, not a question from this skill.

## Usage

`/sra-update TICKER ["instruction" ...]`

## Ceilings, and they bind (§23.2)

```text
bare refresh or directed research   ≤30 minutes
directed research                   ≤8 model subagents
```

The 8 is `MAX_INCREMENTAL_SUBAGENTS` — a SPEND limit for this flow, and a
different thing from `MAX_PARALLEL_AGENTS` (16), which is how wide one wave
runs. A larger open set runs as successive waves until the clock or the token
budget is reached; whatever is not reached stays `open` for the next run. Do
not widen the fan-out to finish the backlog in one go.

## Flow A — Bare refresh

```bash
uv run python sra.py status <TICKER>
uv run python sra.py prefetch <TICKER> --stale-only
uv run python sra.py prefetch-peers <TICKER> --stale-only
uv run python sra.py invalidate <TICKER>            # dry run first, always
uv run python sra.py invalidate <TICKER> --apply
```

`invalidate` without `--apply` is a dry run (§22.3). Read what it proposes to
reopen before applying it — a wide subscription match reopens a section's whole
question set, and that is a research round you may not have meant to buy.

Then, only if questions were reopened:

```bash
uv run python sra.py questions <TICKER> --status reopened
```

Invoke `/sra-research TICKER <section>` for each section carrying reopened
questions, one round each.

Then write the dirty sections — `report.sections_dirty` in `.state.json` — with
`/sra-write TICKER <section>`, one section at a time. Finish with
`/sra-assemble TICKER`.

## Flow B — Directed research

Each quoted instruction becomes **one** `add-questions` call. The instruction
boundaries come from the shell — the quotes the user typed — and are never
inferred by splitting prose (§3): a model deciding where one question ends and
the next begins is a model editing the user's request.

```bash
uv run python sra.py add-questions <TICKER> --section <SECTION> --origin user \
  --question "<the instruction, as written>"
```

Pick the section the question belongs to from `sections.yaml`'s ownership map.
When an instruction genuinely spans two sections, file it under the one that
will have to state the conclusion, and note the other in your report.

Then:

1. `/sra-research TICKER <section>` — **one round**, not three.
2. The research skill harvests URLs and synthesizes into the wiki itself; do not
   run `fetch-urls` separately unless it reports unharvested URLs.
3. `/sra-write TICKER <section>` for each section the new evidence touched.
4. `/sra-assemble TICKER`.

Stop at 8 subagents or 30 minutes, whichever comes first, and say what is still
open. An unfinished question stays `open` in the ledger and costs nothing to
leave there — that is what the ledger is for (§14.0).

## Flow C — Report-only edit

No new research. `/sra-write TICKER <section>` for each named section, then
`/sra-assemble TICKER`. If the user's edit implies a fact the wiki does not
carry, stop and say so rather than writing an uncited claim — that is flow B.

## The incremental gate (§23.3)

After a directed-research update, **only affected section files may change.**
Check it before reporting success:

```bash
diff -rq data/<TICKER>/reports/latest/sections \
         data/<TICKER>/reports/<NEW_RUN>/sections
```

`reports/latest` still points at the previous snapshot until the new run is
snapshotted, which is exactly what makes this diff possible. A section you did
not touch showing up in that diff is a defect worth reporting, not a rounding
error — it usually means the polish chain rewrote more than the shape called
for.

## Accounting

Record agents into `reports/<run>/run_stats.json` exactly as `/sra-build` does,
and check the incremental ceilings rather than the cold-build ones:

```bash
uv run python - <<'PY'
import json
from pathlib import Path
from lib.run_stats import check_budgets, load_run_stats

stats = load_run_stats(Path("data/<TICKER>/reports/<RUN>"))
print(json.dumps(check_budgets(stats, max_subagents=8, max_minutes=30), indent=2))
PY
```

Every agent this flow dispatches writes its own task log into
`reports/<RUN>/log/` (§23.4) — same contract as the cold build. Create that
directory before the first dispatch, and close out with:

```bash
uv run python sra.py wiki-index <TICKER>
uv run python sra.py run-log <TICKER> --run <RUN>
uv run python sra.py wiki-log <TICKER> --entry "update: <what changed>" \
    --agents <N> --tokens <T> --minutes <M> --run <RUN>
```

An incremental run's log is the cheapest way to answer "what did this actually
touch?" — which is exactly the question §23.3's incremental gate asks.

## Report

Say which flow you ran and why, what changed in the evidence (new sources,
reopened questions, answers added), which sections were rewritten, and the
result of the incremental diff. Then the new verdict — and if it moved, say what
moved it. A refresh that changed no numbers is a useful result: report it as
one, rather than padding it.
