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
> You cannot see the PNGs. Judge from the manifests and the wiki.

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
print(json.dumps({
    "selected": len(selected),
    "missing_png": missing,
    "orders_strictly_increasing": orders == sorted(set(orders)),
    "sections": sorted({s["section"] for s in selected}),
}, indent=2))
PY
```

Anything wrong — a missing PNG, a repeated `order`, an unknown section — goes
back to the same subagent to fix. Do not hand-edit `chartbook.json`; the
selection is the subagent's work and a silent repair hides a rubric it
misunderstood.

## Step 4 — Bookkeeping (Bash)

```bash
uv run python sra.py wiki-log <TICKER> --entry "chartbook: <n> of <m> candidates selected across <k> sections"
```

## Report

Say how many candidates rendered, how many were skipped and why, how many were
selected, which sections got no exhibit, and anything notable the selector
rejected. A section with a quantitative argument and no chart is worth naming —
it is usually a missing producer, not a missing judgment.
