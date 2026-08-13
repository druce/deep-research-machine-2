# Conclusion and verdict

You are writing the part of the report a portfolio manager reads first and
returns to last. Everything above describes the company; this takes a position
on the equity.

Read the seven sections under `{sections_dir}` and the cross-section worklist at
`{cross_check_path}`. Target {word_target} words.

## Structure — emit exactly these parts, in this order

Begin with `## Conclusion: Investment Thesis` as the very first line.

**1. The verdict (2-3 paragraphs).** Open with the call, not with a summary of
the company. State the rating, the 12-month fair value and the method that
produced it, the implied return from the current price, and your conviction with
the reason it is not higher. Then the risk/reward: what the buyer is being paid
to underwrite, and what has to be true for the base case to hold.

Reference the report's most salient points — the SWOT, the bull and bear cases,
the ranked risks — without restating their numbers in full. Where your view
differs from sell-side consensus, say so and say why; where it matches, say that
too rather than implying independent confirmation.

**2. `### Key Tests` — a table.** The 3-5 propositions the thesis depends on,
each stated so that it could turn out false:

| Test | What must be true | What we would see if wrong | Timeframe |

Every row names something observable and dated. "Execution improves" is not a
test; "gross margin recovers above 42% by Q3 FY2027, from 38.1% today" is. If
you cannot say what evidence would falsify a claim, it is not carrying weight in
the thesis and does not belong in the table.

**3. `### Monitoring Dashboard` — a table.** The 4-6 indicators to track before
the next review:

| Indicator | Current | Threshold | Check | Implication if breached |

`Current` is a real number with its period, taken from the sections or the
structured artifacts. `Threshold` is the level at which the thesis changes, not
a round number. `Check` is the cadence. `Implication` states which way the
thesis moves and roughly how far.

## Rules

- **Take a position.** A conclusion that surveys both sides and stops has not
  done its job. If the honest answer is Hold, say Hold and say what would move
  you off it — that is a position.
- **No new facts.** Everything traces to the sections or to bronze. Carry the
  `[^bronze-id]` citations through for any figure you state.
- Keep reported results, guidance, consensus and your own assumptions distinct
  throughout. The fair value is YOUR assumption; label it as such.
- No hedging, no restatement of the company description.

Write the conclusion to `{conclusion_path}`.

## Second output — the verdict card

Also write `{verdict_path}`, the same call in structured form. Exactly these
keys, `null` where you genuinely cannot support a value rather than inventing
one:

```json
{
  "rating": "Buy | Hold | Sell",
  "conviction": "High | Medium | Low",
  "fair_value": 0.00,
  "horizon_months": 12,
  "current_price": 0.00,
  "implied_return_pct": 0.0,
  "valuation_method": "e.g. 14x FY2027 EPS, cross-checked against DCF",
  "thesis": "one sentence, under 30 words",
  "key_risk": "one sentence, under 30 words",
  "base_case_probability": 0.0,
  "vs_consensus": "above | in line with | below",
  "pillars": [
    {"claim": "one sentence with a number in it, under 40 words",
     "support": "three to five sentences"}
  ]
}
```

### `pillars` — the one-minute read

Three or four, rendered on the front page directly under the verdict card. They
are the reason the rating is what it is, in the order a reader should meet them.

`claim` is a sentence a reader could disagree with, and it carries a figure:
"Adjusted EBITDA excludes depreciation on satellites rebought every five years,
which is $2.75 billion a year." Not "Margins are the key issue" — that names a
topic and makes the reader read on to learn the finding, which is the defect
*Leads* in `STYLE.md` exists to prevent.

`support` is three to five sentences carrying the numbers behind the claim. Same
prose rules as everywhere else: complete sentences, "we" not "I", no invented
metaphor, no kicker to close.

These are the report's own argument restated for someone who will read nothing
else, so every figure in them is already established in a section and cited
there. **Do not introduce a number here that appears nowhere else** — the pillars
carry no footnotes of their own, and a fact that lives only on the front page is
a fact the reader cannot check.

A gate in `lib/verdict_checks.py` rejects a claim with no digit in it, a claim
over 40 words, a support outside three to five sentences, and a set that is not
three or four pillars.

`current_price` comes from the price or target artifacts, not from memory. Fill
`implied_return_pct` with your own arithmetic — but know that the driver
recalculates it from `fair_value` and `current_price` before assembly and
overwrites yours (§15.3). It does not trust model arithmetic here, and neither
should you: if your number and the recomputed one differ, the recomputed one is
what the report will show, so check yours before the mismatch becomes a
contradiction between your prose and the card.

## Do not ship a contradiction unexplained

The verdict card and the valuation section state the same fair value, or they
state both values and which governs. If a scenario-weighted figure implies a
different rating than the headline, say so in the conclusion in the same breath
as the rating. Never leave a reader to find the contradiction themselves — and
never let a `base_case_probability` stand without the complement it implies.
