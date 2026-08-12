---
name: sra-research
description: Question-driven research loop for a ticker (spec §14). Seeds questions from sections.yaml, fans out researcher subagents over batches of open questions, harvests their URLs into bronze, and synthesizes the answers into wiki pages with bronze citations — up to three rounds. Use when asked to research a ticker or a section, refresh a wiki page, or run a directed research instruction.
---

# sra-research — the question-driven research loop (§14)

Research is organized around explicit QUESTIONS a section must answer, not around
"go research section X". The ledger (`research/questions.json`) is durable state
that accumulates across runs, so what this run cannot answer is what the next run
starts from — which means every question must go through the driver, never
through a hand-edited file.

Division of labor, and it is strict:

- **The answerer gathers.** It never closes a question.
- **The synthesizer decides.** Only it marks a question answered or dropped.
- **The driver counts.** Deferral is arithmetic on `attempts`, not judgment.

## Usage

`/sra-research TICKER <section|entities/slug|all> [--rounds N] ["instruction"]`

- Sections: `profile`, `business_model`, `competitive`, `supply_chain`,
  `financial`, `valuation`, `risk_news`. `all` runs every section in one
  fan-out, which is what a cold build wants — batching groups by section
  anyway, so one wave covers them all (§14 step 2).
- `--rounds N` defaults to `DEFAULT_ROUNDS` (3).
- A directed instruction ("what evidence exists of CrowdStrike pricing
  pressure?") becomes the seed question and usually needs one round.

## Step 0 — Preflight (Bash)

```bash
uv run python sra.py status <TICKER>
```

Not initialized, or bronze missing entirely: stop and say so — run
`/sra-prefetch <TICKER>` first. Research against an empty corpus produces
web-only answers and wastes a round.

Then read the section config once and hold it; it is injected into every prompt
below:

```bash
uv run python - <<'PY'
import json
from lib.sections import load_sections

cfg = load_sections()
sections = ["<SECTION>"]        # or list(cfg["sections"]) for `all`
print(json.dumps({
    "claim_status_rule": cfg["claim_status_rule"],
    "sections": {s: {"title": cfg["sections"][s]["title"],
                     "wiki_page": cfg["sections"][s]["wiki_page"],
                     "seed_questions": cfg["sections"][s]["seed_questions"],
                     "research_guidance": cfg["sections"][s]["research_guidance"]}
                 for s in sections},
}, indent=2))
PY
```

An entity page (`entities/<slug>`) has no `sections.yaml` entry: use the
instruction as the sole seed, `competitive`'s guidance, and record its questions
under the section the entity most affects.

## Step 1 — Seed the ledger (round 1 only)

```bash
uv run python sra.py questions <TICKER> --status open
```

Seed only what is missing. Write the section's `seed_questions` (or the directed
instruction) one per line and register them — `add-questions` collapses repeats
by `sha1(section|question)`, so re-seeding an existing ledger is a safe no-op:

```bash
uv run python sra.py add-questions <TICKER> --section <SECTION> \
    --from-file /tmp/seeds_<TICKER>_<SECTION>.txt --round 1 --origin seed
```

Use `--origin user` for a directed instruction. If nothing is dispatchable and no
instruction was given, report "no open questions — page is current" and stop.

## Step 2 — The round loop (r = 1 … R)

### 2a. Batch the open set (Bash — deterministic, do not group by hand)

`batch_questions` groups by section and rebalances short tails; `waves` splits
the batches into groups of at most `MAX_PARALLEL_AGENTS`. Both are driver code
precisely so batch size and concurrency cannot drift between skill and library:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

from lib.questions import open_questions
from lib.research import batch_questions, waves

d = Path("data/<TICKER>")
qs = open_questions(d)                       # or open_questions(d, "<SECTION>")
print(json.dumps([[[{k: q[k] for k in ("hash", "section", "question")}
                    for q in batch]
                   for batch in wave]
                  for wave in waves(batch_questions(qs))], indent=2))
PY
```

`open_questions` returns both `open` and `reopened` entries — a reopened
question was reopened precisely so it gets re-answered against new evidence.
`deferred`, `answered` and `dropped` are never dispatched.

Empty list: the section is done; go to Step 3.

### 2b. Dispatch answerers — one per batch, whole wave in one message

Dispatch via the Agent tool with `subagent_type: "sra-researcher"`, issuing every
Agent call for a wave in a SINGLE message so they run concurrently. Run waves in
order; never exceed one wave in flight. Per-batch prompt, filling every
placeholder:

> Research <COMPANY NAME> (<TICKER>) and answer these questions:
>
> <numbered list of the batch's questions, each with its hash>
>
> Ticker directory: `<abs data/<TICKER>>`. Repo root: `<abs repo root>`.
>
> What a good answer contains for this section:
> <research_guidance for the batch's section>
>
> Source priority, reconciliation and claim-status rules — follow these exactly:
> <claim_status_rule, pasted in full>
>
> One exception to the citation rule above, because this pipeline harvests URLs
> for you: cite local evidence by id (`[^<source-id>]`), and cite anything you
> found on the web by its bare URL inline. Do NOT save web pages into
> `sources/` yourself — list every URL in `cited_urls` and the driver's
> `fetch-urls` will fetch them into bronze after you return.
>
> Write your answer with `write_answer`, exactly as your agent instructions
> describe, using this id: `<TODAY>_research_answer_r<R>-<batch-slug>`
>
> Return the answer path, 2-3 sentences per question (or `[GAP]` and what you
> tried), and at most three candidate follow-up questions.

The batch slug is 2-4 hyphenated words naming the batch's shared sub-topic. Keep
a record of which hashes went to which batch — Step 2c needs it.

### 2c. Record empty attempts (Bash)

For every dispatched question the answerer returned no citable evidence for
(`[GAP]`, or a batch whose agent failed outright), count the attempt through the
driver:

```bash
uv run python sra.py record-attempt <TICKER> --question-hash <H> [--question-hash <H> ...]
```

At `MAX_ATTEMPTS` (3) the driver flips the question to `deferred` on its own —
retained and revivable, but no longer dispatched. Never edit `attempts` or
`status` yourself, and never mark a gap `dropped`: silence is not out-of-scope,
and only the synthesizer drops.

### 2d. Harvest — the barrier before synthesis (Bash)

```bash
uv run python sra.py fetch-urls <TICKER>
uv run python sra.py manifest <TICKER>
```

`fetch-urls` turns the answers' `cited_urls` into bronze sources and writes each
answer's URL→id map at `derived/answers/<answer-id>.urls.json`. A URL that
failed gets a `null` in the map — that is the synthesizer's signal that the claim
resting on it is not citable. Failures are warnings, not errors; the command
still exits 0. Run it after ALL of the round's answerers return, never per batch.

### 2e. Synthesize — one subagent per active section

Dispatch via the Agent tool with `subagent_type: "sra-writer"`, one per section
that got new answers, all in one message:

> Update the wiki page `<abs ticker dir>/wiki/<wiki_page>.md` for <COMPANY>
> (<TICKER>). This page is synthesized working notes for the report's <title>
> section — key facts, tensions, quantified claims — not report prose.
>
> 1. Read the existing page if it exists; keep what is still valid.
> 2. Read this round's answers: <absolute paths under derived/answers/>.
> 3. For each answer, read its URL→id map `<answer-path>.urls.json` and
>    translate the answer's inline URLs into bronze ids. A URL mapped to `null`
>    was not fetchable — drop the claim or find other evidence for it.
> 4. Thread the material findings into the page under topical `##`/`###`
>    headings. EVERY claim carries a bronze citation `[^<id>]` — a source id or
>    a `structured/` id. NEVER cite an answer file or any `derived/` id: an
>    answer is model-written text, and a citation that terminates there is the
>    exact defect this pipeline exists to prevent. `validate` fails the build
>    for it.
> 5. Forward-looking numbers keep `[REPORTED]`/`[GUIDANCE]`/`[CONSENSUS]`/
>    `[ESTIMATE]` with as-of dates. Contradictions between sources are content:
>    state both sides with numbers, say which you trust and why — never average
>    them away.
> 6. End the body with `## Open questions` — the structural gaps the evidence
>    genuinely does not close.
> 7. Frontmatter: `section`, `updated_at` (UTC ISO), `built_from` (the COMPLETE
>    list of stamped references the page now cites, `{id, fetched_at}` per
>    entry, union of old and new), `open_questions`.
> 8. Bookkeeping — through the driver only, from the repo root:
>    - answered: `uv run python sra.py mark-answered <TICKER> --question-hash <H> --sources <bronze-id>[,<bronze-id>]`
>      (bronze ids only; `--artifacts <answer-id>` records which answer produced
>      it, for audit, never as evidence)
>    - out of scope or unanswerable: `uv run python sra.py drop-question <TICKER> --question-hash <H>`
>    - a question this round raised: `uv run python sra.py add-questions <TICKER> --section <SECTION> --question "..." --round <r+1> --origin synthesizer`
>    Leave a question OPEN if its supporting fetches failed and no bronze
>    remains. Silence never means dropped.
> 9. Return: the questions you answered, dropped and added, and the list of NEW
>    MATERIAL follow-up questions (max 6, deduped against the page). If nothing
>    material remains, return `NO NEW QUESTIONS`.
>
> Section scope: <research_guidance>

### 2f. Stop, or go round again

- Synthesizer returned `NO NEW QUESTIONS`, or `add-questions` reported
  `{"added": 0}`: that section is done — drop it from the active set.
- No active sections left, or `r == R`: stop. Remaining questions stay `open`
  and carry into the next run; that is the design, not a failure.

## Step 3 — Close out (Bash)

```bash
uv run python sra.py validate <TICKER>                      # fatal gate — must exit 0
uv run python sra.py wiki-lint <TICKER>                     # advisory
uv run python sra.py mark-dirty <TICKER> --section <SECTION>
uv run python sra.py wiki-index <TICKER>
uv run python sra.py wiki-log <TICKER> --entry "research <SECTION>: <r> rounds, <n> answers, <m> open"
```

`validate` exiting 1 on an unresolvable citation is the one hard stop here: send
the violations back to the synthesizer (continue that agent) to correct the id or
delete the claim, then re-run. Never leave an unresolvable citation on a page.
`wiki-lint` findings are advisory — report them, do not block on them.

For an entity page, `mark-dirty` the parent section(s) the entity affects.

## Failure handling

- **An answerer fails or returns nothing.** Record an attempt per question in the
  batch (2c) and continue; the round is not lost.
- **`fetch-urls` reports errors.** Expected and non-fatal — the nulls in the map
  tell the synthesizer which claims cannot be cited. Report the count.
- **The synthesizer cites an answer file.** `validate` catches it; fix and re-run
  rather than editing the page yourself.
- **A lock is held** (`LockHeldError`): another phase is writing this ticker.
  Wait and retry; use `--force-lock` only for a lock older than six hours whose
  owner is definitely gone.

## Budget shape (§14, §23.1)

Round 1 runs 10-14 batches across all sections, round 2 six to eight, round 3
three to five; synthesizers taper as sections finish. About 30-40 subagents for a
cold build. Dropping R from 3 to 2 is the main lever for a cheaper run.
