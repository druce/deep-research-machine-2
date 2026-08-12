---
name: sra-assemble
description: Turn finished section drafts into a snapshotted report — polish chain, chart selection, deterministic assembly, validation, snapshot. Use when asked to assemble, finalize, render or snapshot a report, or as the last phase of a build or an update.
---

# sra-assemble — polish, select, assemble, snapshot (§15.3, §16.4)

Everything mechanical here is a `sra.py` subcommand. The only judgment this
skill exercises is **how much polish the run needs** (§23.2) — and then it gets
out of the way: `sra.py assemble` is deterministic and launches no agent.

## Usage

`/sra-assemble TICKER`

## The phase order is not optional (§16.4)

```text
write wave
→ polish chain (produces verdict.json)
→ sra.py charts T
→ sra.py charts T --verdict
→ /sra-chartbook
→ sra.py assemble T
→ sra.py validate T
→ sra.py snapshot T
```

Running `charts --verdict` before the polish chain gives you a football field
with nothing plotted on it; running `assemble` before `/sra-chartbook` gives you
a report with no exhibits. Neither fails loudly. Keep the order.

## Step 0 — Find the run and what changed (Bash)

```bash
uv run python sra.py status <TICKER>
```

The run directory is the newest `data/<TICKER>/reports/<date>/` that has **no
`snapshot.json`** in it. A run that has been snapshotted is immutable — if the
newest run is stamped, the write wave has not run yet for this build and there
is nothing here to assemble.

Read `report.sections_dirty` from `data/<TICKER>/.state.json`: that list is what
decides the next step.

## Step 1 — Polish, at the shape the run earns (§23.2)

This is the one decision that is yours:

| Dirty sections | Polish shape |
|:---|:---|
| 3 or more, or a cold build | the full five-stage chain |
| fewer than 3 | cross-section check + conclusion/verdict only |

The chain is a workflow script, not a shell command — there is no `node` here.
Run it through the **Workflow tool** with `scriptPath: "workflows/polish_chain.js"`
and args `{ticker, company, workdir, report_date, sections, char_caps}` — the
same argument shape the write wave takes. For the reduced shape, pass
`stages: ["cross_section", "conclusion"]` so the critique, polish and evaluation
stages are skipped; a two-section edit does not earn a five-stage rewrite of the
whole document.

Either way the chain must leave `reports/<run>/verdict.json` and
`reports/<run>/conclusion.md` behind. If it did not, stop — assembly refuses a
run with no verdict, and it is right to.

## Step 2 — Charts and chartbook

```bash
uv run python sra.py charts <TICKER>
uv run python sra.py charts <TICKER> --verdict
```

Then invoke `/sra-chartbook TICKER`, which dispatches the one selection subagent
and writes `charts/chartbook.json`. Do not hand-write that file.

## Step 3 — Assemble (Bash, deterministic)

```bash
uv run python sra.py assemble <TICKER>
```

It prints `{run, markdown, html, pdf, citations, exhibits, render_errors}`.

- **exit 1** means a contract failed: a missing section draft, a chartbook
  naming an exhibit that never rendered, an internal filename in report prose,
  or a citation that resolves to nothing or to silver. Every one of these is a
  build defect with a named cause — fix the cause, do not work around the
  assembler.
- **`render_errors` with exit 0** means the markdown and references are on disk
  but pandoc or weasyprint degraded (§22.3). Report it; the run is still
  snapshottable.

## Step 4 — Validate, then snapshot

```bash
uv run python sra.py validate <TICKER>
uv run python sra.py run-log <TICKER> --run <RUN>
uv run python sra.py snapshot <TICKER>
```

`validate` is fatal and has no `--force` (§8.4). Do not snapshot over a failing
gate: the snapshot is what `reports/latest` points at and what the next run is
diffed against.

`run-log` goes **before** `snapshot`: `run_log.md` is a snapshot deliverable
(§15.3), so assembling it afterwards would stamp a run whose own audit trail is
missing from its manifest. It is deterministic and takes no lock, so re-running
it is always safe.

`snapshot` stamps the run, repoints `reports/latest`, clears the consumed
`sections_dirty` list and appends a wiki-log entry. A second run on the same day
becomes `<date>_2` — the first snapshot stays exactly as it was, which is what
makes §23.3's incremental gate ("only affected section files may change")
checkable at all.

## Report

Say which polish shape you chose and why (the dirty count), how many exhibits
were selected, how many citations the report carries, which render formats were
produced, and the snapshot name. If `validate` found anything, name the finding
rather than summarizing it as "some issues".
