---
name: sra-write
description: Write or rewrite ONE report section from its wiki page, with hard checks and self-critique. Use when asked to write, redraft or fix a single section; the full seven-section cold build runs workflows/write_wave.js instead.
---

# sra-write — the incremental single-section path (§15.1)

One writer, its own hard checks, and an internal self-critique. This is the
cheap path for "section 6 is stale" or "rewrite valuation with the new
guidance" — a cold build runs `workflows/write_wave.js`, which is the same
contract at seven-section width with a separate critic and rewrite agent.

## Usage

`/sra-write TICKER <section> ["instruction"]`

Sections: `profile`, `business_model`, `competitive`, `supply_chain`,
`financial`, `valuation`, `risk_news`.

## Step 0 — Preflight (Bash)

```bash
uv run python sra.py status <TICKER>
uv run python sra.py wiki-lint <TICKER>
```

The section's wiki page is the writer's primary source, so check it exists and
is not marked dirty. A missing or dirty page means research has not finished —
run `/sra-research <TICKER> <section>` first rather than writing from thin
evidence.

Then load everything the prompts need, in one call:

```bash
uv run python - <<'PY'
import json
from lib.sections import load_sections, word_target

cfg = load_sections()
sid = "<SECTION>"
s = cfg["sections"][sid]
print(json.dumps({
    "title": s["title"],
    "wiki_page": s["wiki_page"],
    "word_target": word_target(cfg, sid, "<LENGTH_PRESET or standard>"),
    "write_guidance": s["write_guidance"],
    "hard_checks": s["hard_checks"],
    "section_ownership": cfg["section_ownership"],
    "tension_analysis": cfg["tension_analysis"],
    "single_section_critic": bool(s.get("single_section_critic")),
}, indent=2))
PY
```

Pick the run directory: the newest `reports/<date>/`, or today's date if none
exists. Sections are written into `reports/<run>/sections/`.

## Step 1 — Write (ONE subagent)

Dispatch via the Agent tool with `subagent_type: "sra-writer"`. Build the prompt
by concatenating, in order:

1. `prompts/write/_shared.md` — the reading, citing, saving and checking contract
2. the `## Writer` block of `prompts/write/<SECTION>.md`
3. `STYLE.md` in full

filling every placeholder from Step 0: `{ticker}`, `{company}`, `{section}`,
`{wiki_page}`, `{word_target}`, `{workdir}` (the absolute ticker directory),
`{report_date}`, `{draft_path}`, `{write_guidance}`, `{section_ownership}`,
`{tension_analysis}`, and `{hard_checks_json}` (the section's `hard_checks` as a
JSON array).

Append the user's instruction, if one was given, as an explicit additional
requirement — not as a replacement for the guidance.

Then add the self-critique step, which is what makes this path cheaper than the
wave without making it careless:

> After the draft passes its hard checks, critique your own draft against the
> `## Critic` block below, then apply your own critique and re-run the checks.
> Report what you changed. Be genuinely adversarial: the two most common
> failures are a number with no citation and a paragraph that describes without
> concluding.
>
> <the `## Critic` block of prompts/write/<SECTION>.md>

**When `single_section_critic: true`** for this section, skip the self-critique
paragraph and instead run the full three-agent chain — writer, then a separate
critic agent on the `## Critic` block writing to
`reports/<run>/sections/<section>.critique.md`, then a rewrite agent on the
`## Rewrite` block. A separate critic catches what a writer reviewing its own
draft will not, at three times the cost; the flag is where that trade is made.

## Step 2 — Verify the draft yourself (Bash)

Never take the agent's word for it:

```bash
uv run python -m lib.hard_checks \
    data/<TICKER>/reports/<RUN>/sections/<SECTION>.md \
    --rules-json '<hard_checks JSON from Step 0>'
```

Exit 1 means the draft is not finished. Send the printed failures back to the
same agent (continue it) and re-check. After two failed rounds, stop and report
what is failing — a check that will not pass usually means the wiki page cannot
support the section, which is a research problem, not a writing one.

## Step 3 — Gate and record (Bash)

```bash
uv run python sra.py validate <TICKER>
uv run python sra.py wiki-log <TICKER> --entry "write <SECTION>: <n> words, hard checks passed"
```

`validate` is fatal: it resolves every `[^bronze-id]` in the draft. Exit 1 means
a citation points at nothing, or at silver — send the violations back to the
writer. A section that cannot be cited does not ship.

If the writer recorded gaps through `add-questions`, say so when you report: an
unanswered question is the honest reason a section is thin, and it is the input
to the next research round.

## Report

Give the word count against target, the hard-check result, `validate`'s result,
what the self-critique changed, and any gap the writer recorded.
