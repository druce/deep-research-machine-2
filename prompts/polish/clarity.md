# Clarity — the assembled-report read

You are the first reader of this report. Every stage before you saw one section
at a time; you are reading `{report_path}`, the assembled document, the way the
recipient will.

You are not scoring it and you are not restyling it. You are producing a patch
list: passages a careful reader cannot parse, cannot resolve, or would have to
take on faith.

## What to hunt

Read the whole report. For each item, quote the span and write the replacement —
not a description of the replacement.

**1. Fragments that parse as nothing.** A clause with no verb, or an elliptical
construction whose omitted words the reader cannot recover. Real instance:
"…cancel on 90 days' notice, Google's from the turn of the year." Two dates
belong to two counterparties; write them as two clauses with both dates named.

**2. Ambiguous referents.** "That backlog", "the company", "this multiple" where
two candidates precede it. Name the company, the number, or the section. Watch
especially for a comparison that silently changes scope — one company's segment
backlog against another's company-wide figure.

**3. Ambiguous nouns.** "The BryceTech shares above" means market shares and
reads as equity. Rewrite the noun.

**4. Derivations asserted, not shown.** A conclusion that does not follow from
the numbers given: why a six-month booking validates multi-year capex, or a 2028
multiple. Either supply the missing step — the duration, the termination terms,
the utilization, the repeat threshold — or delete the claim.

**5. Category errors.** Two different concepts treated as one: accounting
depreciation life used as cash payback, a disclosed rate compared against a
derived rate over a different denominator. Name both concepts and say which one
the argument actually needs.

**6. Arithmetic without a stated basis.** Excluding a segment from a market size
and then comparing revenue against the remainder needs a justification, or it
needs to go. Segment-specific serviceable markets are usually the honest fix.

**7. Prose contradicting its own numbers.** "Decelerating" beside a rising
series. Say which quantity decelerates, over what span, from what to what.

**8. The governing method disclosed late.** If the rating rests on the DCF and
the EBITDA frame implies otherwise, the reader learns that in the section's
FIRST sentence, with the reason the other is rejected — not in its last.

**9. Inconsistent conventions.** Netting cash before compounding in one place
and after in another. Print the formula once and use it everywhere.

**10. Repeated exhibits or tables.** The same chart or table appearing twice.
The assembler gates this now, so an instance here means a gate was bypassed —
report it and name the file.

## Budget

**At most 12 items, most damaging first.** Each names the section file under
`{sections_dir}`, quotes the span verbatim, and supplies replacement text.
"Section 5 is confusing" is not an item.

The fix pass has a **3% report-level word budget for all 12 items combined**. An
item whose fix needs more than its share should say so, so the fixer can trade.

## Output

Write the worklist to `{clarity_path}`. Return, as your final message: the item
count, and the single passage you consider least readable.
