---
name: sra-status
description: Report a ticker's state in plain language — bronze freshness, wiki coverage, open research questions, the latest report run and what it cost. Read-only. Use when asked how a ticker is doing, what is stale, what is left to research, or whether a report is current.
---

# sra-status — freshness and wiki status (§10.1, §23.4)

Read-only. This skill runs no agent, writes nothing, and takes no lock — running
it while a build is in flight is exactly the intended use. Its whole job is to
turn four JSON outputs into three paragraphs someone can act on.

## Usage

`/sra-status TICKER`

## Step 1 — Gather (Bash)

```bash
uv run python sra.py status <TICKER>
uv run python sra.py questions <TICKER> --status open
uv run python sra.py wiki-lint <TICKER>
```

```bash
uv run python - <<'PY'
import json
from pathlib import Path
from lib.render.runs import is_snapshotted, run_dirs
from lib.run_stats import check_budgets, load_run_stats

d = Path("data/<TICKER>")
runs = run_dirs(d)
latest = runs[-1] if runs else None
print(json.dumps({
    "runs": [r.name for r in runs],
    "latest_run": latest.name if latest else None,
    "latest_snapshotted": bool(latest and is_snapshotted(latest)),
    "state_report": json.loads((d / ".state.json").read_text())["report"],
    "wiki_pages": sorted(p.name for p in (d / "wiki").glob("*.md")),
    "budget_violations": check_budgets(load_run_stats(latest)) if latest else [],
    "totals": load_run_stats(latest)["totals"] if latest else None,
}, indent=2))
PY
```

`status` exits 1 when the ticker is not initialized — say so and stop; the fix
is `sra.py init <TICKER>` followed by `/sra-prefetch`.

## Step 2 — Report

Three short paragraphs, in this order. No tables unless a list genuinely has
more than about six rows.

**Evidence.** What is stale and what that costs. Name the stale kinds and their
policies, and say which are §11.1 minimum-viable inputs (`profile`, `prices`,
`financials`) — those block a build, everything else degrades it. If nothing is
stale, say the corpus is current as of the oldest fetch stamp, and give that
date rather than the word "current".

**Research.** Open, reopened and deferred question counts, grouped by section,
and what the deferred ones mean: a question that returned no citable evidence
`MAX_ATTEMPTS` times is parked, not lost (§14.1). Name the two or three sections
carrying the backlog. Mention `wiki-lint` warnings by kind — it is advisory
(§22.1), so report the count and the worst one, not the whole list.

**Report.** The latest run, whether it is snapshotted, which sections are dirty
(`report.sections_dirty`), and what the run cost against §23.3's ceilings — 80
subagents, 6M tokens, 60 minutes. Report `budget_violations` verbatim if any.
A run that exists but is not snapshotted means a build stopped after assembly;
say which step it stopped before.

Close with the single most useful next command — `/sra-prefetch` for stale
bronze, `/sra-research` for a backlog, `/sra-assemble` for an unsnapshotted run
— and nothing else. One recommendation, not a menu.
