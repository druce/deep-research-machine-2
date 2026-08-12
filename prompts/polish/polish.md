# Polish — the shrink-gated pass

You are applying two worklists to the report and, in the process, making it
shorter. Read:

- the critique at `{critique_path}` — `must_fix` items first,
- the cross-section worklist at `{cross_check_path}`,
- every section under `{sections_dir}` and the conclusion at `{conclusion_path}`.

## The gate

**This pass may not grow the REPORT.** A single section may grow by up to 10% if
another shrinks to pay for it. Every section you touch is checked with:

```bash
uv run python -m lib.hard_checks {sections_dir}/<section>.md \
    --rules-json '["report_not_longer_than: {baseline_dir}",
                   "not_longer_than_pct: {baseline_dir}/<section>.md 1.10"]'
```

The report-level rule counts WORDS across every section against the pre-polish
copies. It exists because a previous generation of this pipeline ran a polish
pass that GREW the body by 1,933 bytes while leaving every flagged redundancy in
place, and nothing detected it. A polish pass that adds words has not polished
anything.

The per-section rule replaced a hard per-section ceiling, which had its own
failure mode: every clarifying word had to be bought with a deletion from the
same paragraph, so sentences compressed into fragments that parse as nothing.
"Spot pays 320.8x trailing EBITDA for AI cloud revenue that either party can
cancel on 90 days' notice, Google's from the turn of the year" shipped that way.
Clarity is allowed to cost words now — as long as the report pays for them.

So the order of work is: **delete first, then fix.** Resolve the redundancies
from the cross-section worklist — the non-owning section loses the number and
gains a reference — and only then spend the freed words on the critique's
`must_fix` items.

## What to fix

- **Number inconsistencies**: set every occurrence to the worklist's `correct`
  value. Do not average, do not pick the majority reading.
- **Redundancies**: keep the fact in its owning section; elsewhere replace it
  with a reference that carries no number ("at the multiple discussed in
  Section 6").
- **Unsupported claims**: attach the citation, or cut the claim. Do not soften
  it into a vaguer sentence — that keeps the assertion and loses the number.
- **Hedging**: cut the qualifier or commit to the position.
- **Contradictions** flagged `genuine_tension: false`: resolve them.

## What NOT to fix

**Do not smooth away a tension marked `genuine_tension: true`.** Where the
evidence genuinely pulls two ways, the report says so with numbers on both sides
and takes a view. Making that read cleanly is the single most damaging thing
this pass can do — it is the report's most useful content, and it is the first
thing an editing instinct removes.

Do not rewrite prose you were not asked to fix. Every edit traces to a worklist
item; a pass that restyles the whole report will fail the length gate and lose
the fixes that mattered.

Do not touch citations except to add a missing one. `[^bronze-id]` markers are
renumbered by the assembler.

## Output

Overwrite the section files and the conclusion in place, then verify:

```bash
for s in {section_ids}; do
  uv run python -m lib.hard_checks {sections_dir}/$s.md \
      --rules-json '["report_not_longer_than: {baseline_dir}",
                     "not_longer_than_pct: {baseline_dir}/'"$s"'.md 1.10"]'
done
```

Return, as your final message, a JSON object and nothing else:

```json
{"applied": ["<critique item numbers>"], "skipped": [{"item": 3, "reason": "..."}],
 "words_before": 0, "words_after": 0, "shrink_gate_passed": true}
```

Skipping is legitimate — an item you believe is wrong, or one the length gate
would not allow. Say which and why; a silently dropped item is worse than a
declared one.
