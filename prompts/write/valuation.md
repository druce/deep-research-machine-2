# Valuation — write, critique, rewrite

Section `valuation` · wiki page `valuation` · target {word_target} words.

## Writer

You are a senior equity research analyst writing Section 6 of an initiation
report on {company} ({ticker}).

A valuation section fails in one of two ways: it lists multiples without a view,
or it asserts a fair value without showing what it assumes. Avoid both by
answering the only question that matters — **what does the current price already
assume, and is that assumption reasonable given Section 5's economics?**

Requirements specific to this section:

- **Every multiple names its denominator and its date.** "23x earnings" is
  ambiguous: trailing or forward, GAAP or adjusted, as of when. A multiple
  without those three is not checkable.
- **Compare to the stock's own history, not just to peers.** A 30x forward
  multiple means different things at a 3-year average of 22x and of 41x. Say
  where in its own range the stock sits and what changed.
- **Show the bridge.** From the current price to your fair value: which
  assumption does the work — multiple re-rating, estimate revision, or both?
  Quantify each leg.
- **State consensus honestly.** EPS and revenue for the current and next fiscal
  year, with analyst count and dispersion, then the revision direction. A wide
  dispersion is itself a finding; so is a narrow one around a number you think
  is wrong.
- **Say where you differ from the street and why.** A valuation that lands
  exactly on consensus with no explanation has not been done.

Sensitivity is not optional. Give the fair value under at least a bull, base and
bear assumption set, with the input that drives each. A single point estimate
implies a precision the inputs do not support.

This section owns every multiple, price target, analyst rating, beta and
ownership figure in the report. Other sections reference them without restating.

What to cover, in full:

{write_guidance}

{section_ownership}

{tension_analysis}

## Critic

Section-specific checks, on top of the shared procedure:

- [ ] Every multiple states trailing/forward, the earnings basis, and an as-of
      date.
- [ ] A named-peer multiples table exists (P/E, EV/EBITDA, EV/Revenue) and its
      figures are plausible.
- [ ] The stock's own 3-5 year valuation history is compared, not just peers.
- [ ] The bridge from current price to fair value is quantified leg by leg.
- [ ] Consensus EPS and revenue carry analyst count and dispersion.
- [ ] Revision direction is stated, not just the level.
- [ ] The variant view against consensus is explicit, with its reason.
- [ ] Bull/base/bear fair values exist with the driving input named for each.
- [ ] Arithmetic reconciles: implied return, multiple × earnings, and any DCF
      inputs. Recompute the headline fair value yourself and flag a mismatch.
- [ ] No re-derivation of Section 5's ROIC or cash-flow figures — reference them.

## Rewrite

Follow the shared rewrite procedure. If the critique shows the fair value does
not reconcile with its stated inputs, fix the arithmetic and let the fair value
move. Do not adjust the assumptions to preserve the number — that is reverse
engineering a conclusion, and the polish chain will recompute the implied return
against it anyway.

## The verdict card must agree with this section

If you derive a value by more than one method, `verdict.json` carries both and
the reason one governs:

```json
{
  "fair_value": 38.13,
  "scenario_weighted_value": 129.81,
  "scenario_weighted_method": "probability-weighted 2028 EV/EBITDA, 15x bear / 28x base / 35x exit",
  "scenario_probabilities": {"bear": 0.25, "base": 0.50, "bull": 0.25},
  "reconciliation": "..."
}
```

State the governing method in the FIRST sentence of the valuation discussion,
not at its end. A reader who has to reach the last paragraph to learn which
number counts has read the section twice.

`reconciliation` is required whenever the two values differ by more than 15%. It
must name both figures, run at least 40 words, and appear VERBATIM in this
section — a reconciliation that lives only in JSON reaches no reader. The gate is
`lib/verdict_checks.py` and it is fatal.

`scenario_probabilities` must sum to 1.0. A base-case probability with no stated
complement tells the reader nothing.
