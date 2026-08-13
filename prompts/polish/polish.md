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

So the order of work is: **delete first, then fix.** Sweep the empty sentences
(below), resolve the redundancies from the cross-section worklist — the
non-owning section loses the number and gains a reference — and only then spend
the freed words on the critique's `must_fix` items.

## The empty-sentence sweep

This is the one piece of work that does not come from a worklist. Read every
section and the conclusion and **delete any sentence that carries no fact and no
analysis.** You do not need a critique item to justify a deletion, and you do
not need permission to delete a sentence nobody flagged.

A sentence earns its place by doing one of two things:

- **carrying a fact** — a figure, a named party, a date, a filing, a disclosed
  or computed quantity; or
- **doing analytical work on facts already stated** — interpreting them, weighing
  them against each other, drawing the consequence, taking a position, or placing
  them in context the reader does not already have.

Everything else goes. The three failure modes, in the order you will find them:

- **Restatement.** The sentence says again, in different words, what the previous
  sentence or the section heading already said. Delete it; the earlier statement
  survives.
- **Filler.** Throat-clearing, transitions that announce structure rather than
  connect ideas ("It is worth examining the competitive dynamics here"),
  hedged non-claims ("There are several factors to consider"), and sentences
  whose only content is that the topic is important.
- **Abstract framing.** The sentence names the *kind* of thing coming rather than
  the thing — "The two largest risks here are unmarked in the accounts", "The
  binding risk is not execution, it is the distance between delivery and price."
  Where the framing sentence opens a section or paragraph, check the sentence
  after it first: the fact is usually already there, and deleting the frame
  promotes it, which is both the cheapest fix and the one *Leads* in `STYLE.md`
  asks for.

**A summary sentence is not filler.** Opening or closing on a sentence that
analyzes, contextualizes, or states the position you have earned is legitimate
and often the best sentence in the paragraph. The test is not where the sentence
sits, it is whether a reader loses something when it goes. "We use the 28%
organic figure because it is the growth the company controls" is analysis.
"Palo Alto Networks' competitive position is therefore mixed" is not. Nor does
this license the manufactured kicker `STYLE.md` rule 5 already forbids: an
ordinary sentence that states the consequence, yes; a four-word verdict shaped
like a punchline, no.

When you are unsure, apply the deletion test: cut the sentence, read the
paragraph, and ask what a reader no longer knows. If the answer is nothing, it
stays cut. These deletions are what pay for the critique's `must_fix` items
under the length gate.

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

Do not rewrite prose you were not asked to fix. Apart from the empty-sentence
sweep, every edit traces to a worklist item; a pass that restyles the whole
report will fail the length gate and lose the fixes that mattered. The
distinction is deletion versus rewriting: cutting a sentence that carries nothing
needs no authorization and pays into the gate, while rewriting a sentence that
does carry something needs a worklist item. If you find yourself replacing an
empty sentence with a better one rather than deleting it, you are writing, not
polishing — cut it and move on.

Do not delete a sentence because it is short, plain, or unexciting. "Insiders
have bought nothing here" is a fact and stays. The sweep removes sentences with
no content, not sentences with modest content, and a section that loses a
disclosed number to this pass has been damaged, not polished.

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
 "sentences_deleted": {"<section>": 0}, "words_before": 0, "words_after": 0,
 "shrink_gate_passed": true}
```

`sentences_deleted` counts the empty-sentence sweep only, per section — not
deletions made to satisfy a worklist item. It is how the sweep stays auditable:
a pass reporting zero across every section either found a clean report or did not
look, and the run log should say which.

Skipping is legitimate — an item you believe is wrong, or one the length gate
would not allow. Say which and why; a silently dropped item is worse than a
declared one.
