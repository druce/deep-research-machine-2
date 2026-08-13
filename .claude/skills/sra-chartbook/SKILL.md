---
name: sra-chartbook
description: Choose which rendered charts go in the report, where, and with what captions. Renders both candidate passes deterministically, then dispatches one subagent to select 10-16 exhibits into charts/chartbook.json. Use when asked to pick charts, build the chartbook, or after the polish chain has produced a verdict.
---

# sra-chartbook — salience selection (§16.2)

Rendering is deterministic and already done by `sra.py charts`. The only model
work here is judgment: which exhibits carry an argument the report is actually
making, in what order, with what captions. One subagent, one pass.

## Usage

`/sra-chartbook TICKER`

## Prerequisites — the phase order is not optional (§16.4)

```text
write wave → polish chain → verdict.json → charts → charts --verdict
→ /sra-chartbook → sra.py assemble
```

The verdict-dependent exhibits read `verdict.json`, so running this before the
polish chain gives you a chartbook missing exactly the charts the conclusion
needs.

## Step 1 — Render both passes (Bash)

```bash
uv run python sra.py charts <TICKER>
uv run python sra.py charts <TICKER> --verdict
```

Each prints `{"rendered": [...], "skipped": [...], "errors": {...}}`.

- **`skipped`** is normal: a renderer returns `None` when its inputs are not on
  disk (§16.1). Note which, so you can say what the report could not show.
- **exit 2** means a renderer raised. The other exhibits still rendered; report
  the failure and continue.
- **exit 1 on the `--verdict` pass** means there is no `verdict.json` yet. Stop
  and run the polish chain first — do not select from the first pass alone and
  call it a chartbook.

## Step 2 — Select (ONE subagent)

Dispatch via the Agent tool with `subagent_type: "sra-writer"` — this is
judgment over files, with no web or MCP needed — and this prompt:

> Read `<repo>/prompts/chartbook.md`. It is the selection rubric; follow it.
>
> Your inputs, all under `<abs ticker dir>`:
> - `charts/candidates/*.json` — one manifest per rendered candidate
> - `wiki/00_index.md`, and the wiki pages it lists — what the research
>   established, which is the argument the exhibits have to serve
> - `reports/latest/verdict.json` — the conclusion
>
> Select 10-16 exhibits and write `charts/chartbook.json` in exactly the shape
> the rubric specifies. Every `name` must match a candidate manifest that
> exists; `order` starts at 1 and strictly increases.
>
> **Cover every numbered section.** A section with no exhibit is a stretch of
> the report a reader cannot scan, and the last SPCX run left four of them —
> §§1, 2 and 4 and the conclusion — while shipping only 8 exhibits against this
> floor of 10. Before you finish, list the seven sections and check each has at
> least one. Where no candidate can honestly serve a section, do not force one:
> name the section in your log and say which renderer is missing. A missing
> producer is a fixable gap; a chart that does not carry that section's
> argument is not.
>
> You cannot see the PNGs. Judge from the manifests and the wiki.
>
> Your task log (§23.4): stamp `date -u +%Y-%m-%dT%H:%M:%SZ` before you start
> and again when you finish, then write ONE log to
> `<abs run dir>/log/<NN>_chart-select_chartbook.md` with frontmatter
> `purpose: chart-select`, `section: null`, `round: 1`,
> `label: "chart-select"`, `started_at`, `finished_at`, `status`, `outputs`,
> and body headings `## Inputs`, `## Outputs`, `## Notes`. In `## Notes`, name
> the candidates you rejected and why — the chartbook records only what you
> kept. Write the log even if the selection failed.

## Step 3 — Verify the selection (Bash)

A name that resolves to no PNG is a hole in the assembled report, so check
before the assembler does:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

d = Path("data/<TICKER>")
book = json.loads((d / "charts" / "chartbook.json").read_text())
selected = book["selected"]
missing = [s["name"] for s in selected
           if not (d / "charts" / "candidates" / f"{s['name']}.png").exists()]
orders = [s["order"] for s in selected]
from lib.sections import load_sections
wanted = list(load_sections()["sections"])
covered = {s["section"] for s in selected}
print(json.dumps({
    "selected": len(selected),
    "missing_png": missing,
    "orders_strictly_increasing": orders == sorted(set(orders)),
    "sections": sorted(covered),
    "sections_without_exhibit": [s for s in wanted if s not in covered],
}, indent=2))
PY
```

Anything wrong — a missing PNG, a repeated `order`, an unknown section — goes
back to the same subagent to fix. A non-empty `sections_without_exhibit` goes
back too, unless the subagent's log already names that section and says which
renderer is missing. Do not hand-edit `chartbook.json`; the
selection is the subagent's work and a silent repair hides a rubric it
misunderstood.

## Step 4 — Bookkeeping (Bash)

```bash
uv run python sra.py wiki-log <TICKER> \
    --entry "chartbook: <n> of <m> candidates selected across <k> sections" \
    --agents 1 --tokens <T> --minutes <M> --run <RUN>
```

## Report

Say how many candidates rendered, how many were skipped and why, how many were
selected, which sections got no exhibit, and anything notable the selector
rejected. A section with a quantitative argument and no chart is worth naming —
it is usually a missing producer, not a missing judgment.
