# Shared writing contract

Pasted into every writer, critic and rewrite prompt. What each section must
*contain* is `sections.yaml` (`write_guidance`, injected as `{write_guidance}`);
how the prose must *read* is `STYLE.md`; this file is how the job is done.

Nothing here restates `sections.yaml` or `STYLE.md`. Where they disagree with
this file, they win — they are the single source of truth for content and style
respectively, and a third copy would only drift.

## READING

You write from what the research already established. Your inputs, all under
`{workdir}`:

- `wiki/{wiki_page}.md` — the synthesized notes for this section, with bronze
  citations. This is your primary source.
- `structured/<id>.json` — the exact figures, resolved by the ids the wiki page
  cites. Read these rather than copying a number out of prose.
- `sources/<id>.md` — the underlying documents, when the wiki page's summary is
  not enough.
- `STYLE.md` at the repo root, in full.

**Do no independent retrieval.** No web, no MCP, no new fetches. This is not a
tooling restriction to work around — it is the design (§15.1). A fact you
discover while writing has no bronze id, so it cannot be cited, so it cannot
survive assembly. If the evidence you need is missing, record the gap:

```bash
uv run python sra.py add-questions {ticker} --section {section} \
    --question "..." --origin section-write
```

Then write the section without that claim, and say plainly what is not known.
A stated gap is worth more to the reader than a confident sentence with nothing
behind it.

## CITING

Every quantitative claim carries `[^<bronze-id>]` — an id under `sources/` or
`structured/`, copied from the wiki page. Never cite a wiki page, a researcher
answer, or anything under `derived/`: those are model-written, and assembly
would turn the citation into a number pointing at model output.

Internal artifact names never appear in prose. Not `key_facts.json`, not
`structured/`, not a path. The reader has no filesystem; a hard check rejects
them.

Forward-looking numbers keep their status tag — `[REPORTED]`, `[GUIDANCE]`,
`[CONSENSUS]`, `[ESTIMATE]` — with an as-of date and the venue or provider.

## LENGTH

Hit the word target. **Never buy it with clarity.**

The target measures how much the reader should have to read, not how much you
should compress. A sentence that has to be read twice has saved nothing — it has
moved the cost from your budget to the reader's.

When the draft is over budget, in this order:

1. **Drop the least important point entirely.** Five points that read beat nine
   that don't. If the section makes nine arguments and has room for five, make
   five and make them well. This is almost always the right move and almost
   never the one taken.
2. **Split long sentences into short ones.** Fewer clauses per sentence — not
   fewer words per clause.
3. **Turn a parallel enumeration into a bullet list.** Risks, catalysts,
   drivers, a top-five: these are lists, and a list reads faster as a list. The
   *argument* stays prose.
4. **Exceed the target by up to 10%** and say so in your final message.

All four beat the alternative. **Never** delete the subject, the verb, or the
connective that carries the logic. Never stack nouns to avoid a preposition.
Never let an elliptical clause carry a second fact.

Worked examples of each failure are in `STYLE.md` under *Compressed past
readability* — read them before you decide to tighten a sentence rather than cut
a point. The three that shipped cost sixteen words to fix, across a whole report.

## OWNERSHIP AND TENSION

The seven sections are written in parallel by agents that cannot see each
other's drafts, so "avoid repetition" only works as a contract. `sections.yaml`
carries the fact-ownership table and the tension rules in full, and they are
injected into your prompt. Two consequences that catch writers out:

- State an owned fact in full **exactly once**, in its owning section. Elsewhere
  reference it without restating the number: "at the forward multiple discussed
  in Section 6".
- A tension is only worth reporting if you can quantify both sides, and you must
  then **take a position on it**. Presenting both sides and stopping is not
  analysis.

## SAVING

Write the section to:

```text
{workdir}/reports/{report_date}/sections/{section}.md
```

Begin with the exact H2 the hard checks require, and use `###` for every
subsection — a second `##` renders as a sibling of the numbered sections in the
assembled report, and the check rejects it.

## CHECKING

**First, read your three longest sentences.** If any needs a second pass to
parse, split it — even if that costs words. This is the check the machine cannot
run for you, and it is the one that catches what *Length* above describes.

Then run the section's hard checks over your own draft:

```bash
uv run python -m lib.hard_checks {draft_path} --rules-json '{hard_checks_json}'
```

Exit 0 means passed. Exit 1 prints the failures — fix them and run it again.
Do not report a draft as finished while a check fails.

`max_length_prose` is a **runaway guard, roughly 25% above the word target**, and
it excludes citation ids so the count is prose the reader actually reads. Hitting
it means the draft is long enough to need a point removed, not a paragraph
squeezed. Follow the ladder under *Length*. Do not shave characters to land just
under it — a previous generation of this pipeline did exactly that, every section
finishing within 1.3% of its cap, and the shipped sentences were unreadable.

## CRITIQUE PROCEDURE

Pasted into every critic prompt, ahead of that section's own checklist.

Read the draft at `{draft_path}`. Critique it as an equity research editor who
will have to defend it.

**1. Verify every factual claim.** For each number, date, percentage, ranking or
named entity in the draft, resolve its citation and check it:

- the id must exist — `uv run python sra.py show {ticker} <id>` from the repo
  root — and it must be bronze, never a wiki page or an answer file;
- the source must actually say what the draft says it says.

Report each as **CONFIRMED**, **CONTRADICTED** (state what the source says) or
**UNSUPPORTED** (no citation, or the citation does not cover the claim). An
uncited number is not a style problem; it is the defect this whole pipeline
exists to prevent.

**2. Check completeness** against the section's own checklist below and against
`{write_guidance}`. Flag anything missing or shallow — "shallow" means present
as an assertion with no number behind it.

**3. Check length, asymmetrically.** Count the words. Target: {word_target}.

- **Over by more than 20%:** flag it, and say which point to drop. Name the
  point — "cut the third supplier anecdote" — not "tighten throughout".
- **Under target:** flag it ONLY if the section's required content is missing
  (check 2 covers that). A short draft that covers its checklist and reads
  cleanly has passed. Do not ask for padding, and never ask a writer to restore
  a point it correctly dropped.
- **Never** propose fixing length by compressing a sentence. If you find
  yourself suggesting denser prose, you are asking for the defect in *Length*
  above. Ask for a cut instead.

**4. Check the analysis, not just the facts:**

- **So-what**: every key data point is followed by its investment implication.
  A paragraph that lists facts without connecting them to the case fails.
- **Position taken**: tensions are stated, quantified on both sides, and
  resolved with a view. Both-sides-and-stop is a failure.
- **Ownership**: no fact this section does not own is restated with its number.
- **Opinion vs fact**: judgments are framed as judgments, facts as facts.
- **Style**: no promotional language, no throat-clearing, no hedging. Full
  compliance with `STYLE.md`. Bullets are legitimate for a parallel enumeration
  — risks, catalysts, drivers, a top-five — and a defect only when they replace
  an argument that needs connective tissue. A bulleted *case* is a failure; a
  bulleted *list* is not.
- **Readability**: read the three longest sentences. Any that needs a second
  pass to parse is a defect, and the fix is to split it, not to shorten it. Quote
  each one and give the split.
- **Voice**: the dominant failure here is not "In conclusion" or "robust" — it is
  the epigrammatic essay voice described under *Voice and sentence mechanics* in
  `STYLE.md`. Count these in the draft. Report all of them as **one** numbered
  item against the budget below, giving the count per category and quoting the
  two or three worst spans — not one item per instance:
  - `"X, not Y"` antithesis — **at most 1** per section;
  - em dashes — **at most 1 per paragraph**;
  - sentences whose subject is an abstract noun with a definite article ("The
    moat is attach", "The load-bearing assumption is...") — **at most 2**;
  - invented metaphors, as opposed to standard market vocabulary — **0**;
  - `I`, `my`, or an imperative aimed at the reader ("Underwrite the 28%",
    "Treat it as a ceiling") — **0**; the report says "we";
  - British spellings (`amortisation`, `organisation`, `recognised`) — **0**;
  - headings written as theses rather than labels;
  - verbless fragments used as verdicts ("Volatility, not terminal value.").

  These are form defects, not content defects. Never propose fixing one by
  dropping the judgment, the number or the position — the rewrite keeps all
  three and changes only the sentence.
- **Numbers**: prices to 2 decimals, market cap in billions to 1 decimal,
  percentages to 1 decimal, multiples to 1 decimal with `x`, fiscal years
  labeled explicitly.

**Budget: under 1,200 words and at most 15 numbered items, most important
first.** A critique longer than the draft it reviews is unfocused — merge
related points rather than enumerating every instance, and quote only the span
you are flagging. Save it to `{critique_path}`.

## REWRITE PROCEDURE

Pasted into every rewrite prompt.

Read the draft at `{draft_path}` and the critique at `{critique_path}`. Address
each issue in order.

- **CONTRADICTED claims**: replace the figure with what the source says, reading
  the artifact yourself rather than trusting the critique's transcription.
- **UNSUPPORTED claims**: find the bronze id that supports it, or cut the claim.
  Do not soften it into a vaguer sentence that keeps the assertion without the
  number — that is the same defect with better manners.
- **Missing elements**: add them from the wiki page and the structured
  artifacts. The reading rules above still hold: no new retrieval.
- **Length**: follow the ladder under *Length* — drop a point, split sentences,
  bullet an enumeration, or exceed by up to 10%. Never compress into denser
  prose, and never restore a point the writer correctly dropped just to reach
  the target.

Do not introduce new research or speculation, and do not fix a criticism you
believe is wrong — say so in your final message with the evidence, and leave the
text as it was. Save the revised section to `{draft_path}`, overwriting it, then
re-run the hard checks.
