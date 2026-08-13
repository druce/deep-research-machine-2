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
→ sra.py lint-render T   + read the rendered pages (§22.4)
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

Then check the peer table has data, before spending a polish chain on the run:

```bash
uv run python sra.py prefetch-peers <TICKER> --stale-only
```

Assembly emits a `peer table:` warning when most comparable cells read `N/A`. If
that warning survives this command, say so when you report — the table will ship
empty, and the caption will make it look intentional.

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

## Step 3b — Clarity pass over the assembled report

This is the only stage that reads the finished document. Every earlier critic
saw one section at a time, which is why cross-section referent ambiguity and
compressed-to-nothing sentences have always survived to the reader.

Snapshot the current drafts first — the fix pass measures against them:

```bash
cp -R <RUN_DIR>/sections <RUN_DIR>/sections_preclarity
```

1. **Critique.** Dispatch one `sra-writer` with `prompts/polish/clarity.md`,
   filling `{report_path}` = `<RUN_DIR>/report.md`, `{sections_dir}` =
   `<RUN_DIR>/sections`, `{clarity_path}` = `<RUN_DIR>/clarity.md`. Leave the
   model alone — this is the only first-reader pass over the assembled
   document, and the ambiguities it hunts are the ones every section-local
   critic already missed.

2. **Fix.** Dispatch one `sra-writer` with `model: "sonnet"` to apply that
   worklist to the section files, checking each one it touches:

```bash
uv run python -m lib.hard_checks <RUN_DIR>/sections/<section>.md \
    --rules-json '["report_not_longer_than: <RUN_DIR>/sections_preclarity 1.03"]'
```

   This is the one pass permitted to GROW the report, because every item it
   applies is an explanation the reader needed and did not get. 3% is the whole
   budget across all items; a fixer that cannot fit one must declare the skip
   with its reason rather than dropping it silently.

   `model: "sonnet"` is deliberate and is the only model downgrade in the
   pipeline (§21.1). `prompts/polish/clarity.md` requires the critic to "quote
   the span and write the replacement — not a description of the replacement",
   so this agent receives the finished prose and applies it. That is execution
   against a written patch list, which is the one task shape here that carries
   no judgment the critic did not already make. If a run shows this stage
   *rewriting* rather than applying, the defect is a critique that described
   its fixes instead of writing them — fix the critique, not the model.

3. **Re-assemble.** The fixes landed in section files, not in `report.md`:

```bash
uv run python sra.py assemble <TICKER>
```

Skipping the re-assemble ships the unfixed report with a clarity worklist
sitting beside it. If the clarity pass returned zero items, skip steps 2 and 3.

## Step 3c — Look at what the reader gets (§22.4)

Every gate before this one reads the report as *source* — drafts, markdown,
citations against bronze. Nothing had looked at the rendered deliverable, and
that gap shipped a report with its own stylesheet printed above the masthead in
both the HTML and the PDF.

**The deterministic half runs first, and it is a gate.** `assemble` already ran
it on what it just wrote, so this is the re-check after the clarity pass:

```bash
uv run python sra.py lint-render <TICKER>
```

Exit 1 means the document contains the machinery that produced it — leaked CSS,
an unrendered `{{ variable }}`, a literal HTML comment. Every one of those is a
template defect with a named cause. **Fix the template and re-assemble; never
hand-edit `report.html`.** A hand-edit leaves `report.pdf` beside it untouched,
which is exactly how a run came to have a clean HTML and a corrupt PDF.

It reads BOTH deliverables off disk. A `skipped` entry (no `pdftotext`) is a
degradation to report, not a failure.

**Then look at it**, because a regex cannot see a table split across a page
break, an exhibit that rendered blank, or a column of numbers running off the
plate. Turn the real PDF into images and read them:

```bash
pdftoppm -png -r 100 -f 1 -l 2 <RUN_DIR>/report.pdf /tmp/<TICKER>_pg
```

Read the generated PNGs. Sample rather than sweep: **page 1** (masthead, KPI
strip, verdict card — where leaked template text lands), **a body page carrying
an exhibit**, and **the first references page**. On a 50-page report that is
three images, not fifty.

What to look for, in order of how often it has actually gone wrong: text above
the masthead that is not part of the report; an exhibit that is blank, clipped,
or captioned with a filename; a peer table whose cells all read `N/A`; a table
or figure orphaned from its heading across a page break; numbers overflowing
their column.

For the HTML specifically — hover states, and whether the citation markers
actually jump to the reference and back — open it with Playwright instead:

```
browser_navigate  file://<abs RUN_DIR>/report.html
browser_snapshot
```

Anything you find is a template or chartbook defect. Fix the cause and
re-assemble; the deliverables are generated files and editing one is a repair
that the next `assemble` silently discards.

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
were selected, how many clarity items were raised and how many were applied,
how many citations the report carries, which render formats were produced, any
`warnings` assembly reported (a `peer table:` warning means the comparison table
shipped empty), and the snapshot name. If `validate` found anything, name the
finding rather than summarizing it as "some issues".

Say which pages you actually looked at, and what you saw. "Rendered pages
checked" with no page numbers is the report of someone who skipped the step —
name the pages and say the masthead was clean, or name what was not.
