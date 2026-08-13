# Whole-report critique

You are reviewing the assembled report as a whole — something no section critic
could do, because each of them saw one section at a time. Read every section
under `{sections_dir}`, the conclusion at `{conclusion_path}`, and the
cross-section worklist at `{cross_check_path}`.

Your output is the worklist the polish stage executes, so an item that is not
actionable is an item that will not be fixed.

## What only a whole-report reader can see

**1. Does the report reach its own conclusion?** Work backwards from the
verdict: for each claim the conclusion rests on, find where the body
established it. A conclusion resting on an argument the body never makes is the
most serious defect available here.

**2. Does the argument survive the sequence?** Read the seven sections in order.
Where does the reader learn something that changes how they should have read an
earlier section? That is usually a section in the wrong order or a fact in the
wrong place.

**3. Where does the report repeat itself?** The cross-section worklist has the
mechanical duplications; you are looking for the argumentative ones — the same
point made three times in different words because three writers each thought it
was theirs.

**4. What is asserted but never supported?** Flag every claim that carries no
citation and is not obviously derived from a cited one. Verify a sample:

```bash
uv run python sra.py show {ticker} <bronze-id>
```

**5. Where does it hedge?** A report that qualifies every claim has taken no
position. Name the sentences that retreat.

**6. Which sentences cannot be read once?** Sweep the whole report and list
**every** instance — not a sample, and not merged away under the budget rule
below. Four shapes:

- over 50 words (`max_sentence_words: 50` gates the write wave, so one here means
  a draft was edited after its gate);
- more than one appositive before the main verb — the shape that passes the word
  cap and still cannot be parsed. A 47-word instance shipped: "Disclosure on
  Toast IQ Grow, the AI product management calls its fastest-growing launch ever,
  stops at $10 million of annualized recurring revenue reached faster than any
  prior product, under half a percent of the $2.4 billion recurring base; paid
  accounts, retention and service intensity are not disclosed.";
- a relative pronoun deleted to save a word, where the sentence then garden-paths
  ("the AI product **that** management calls" is the fix);
- a sentence that survives the delete test — delete it and no fact is lost.
  "What decides the rating is two adjustments to the street's model, and they
  pull against each other:" announces what the next clause delivers. `STYLE.md`
  rule 8 applies to every sentence, not only to a section's lead.

Quote each span and give the replacement. Splitting a sentence is usually
word-neutral, so the fix rarely costs length — per `STYLE.md` rule 14, prefer two
sentences to one whenever they occupy the same space.

## Budget

**Under 1,500 words and at most 20 numbered items, most important first.** A
critique longer than what it reviews is unfocused. Merge related instances into
one item with examples rather than enumerating each.

Item 6 is the exception to both limits: it is a sweep, it lists every instance,
and its spans do not count against the 1,500 words. Merging it away would defeat
it — a reader hits every one of those sentences, not a representative sample.

Every item must name the file, quote the span, and state the fix. "Section 5 is
weak" is not an item; "Section 5 asserts value creation without deriving ROIC —
add the derivation or drop the claim" is.

Mark each item `must_fix` or `should_fix`. The polish stage is length-gated and
will not get through everything; `must_fix` is what it does first.

Write the critique to `{critique_path}`.

Return, as your final message: the item counts by severity, and whether the
conclusion is supported by the body.
