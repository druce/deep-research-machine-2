# Chartbook selection

You are choosing which of the rendered chart candidates earn a place in the
report, where each one goes, and what its caption says. One pass, one decision
per exhibit.

## What you are reading

- `charts/candidates/*.json` — one manifest per rendered chart:
  `{name, title, data_sources, derived_from_urls, auto_caption, salience}`.
  `salience.recency_days`, `salience.coverage` and `salience.variance_note` are
  the mechanical signals; they inform the choice, they do not make it.
- `wiki/00_index.md` — what the research actually established, section by
  section. This is the argument the charts have to serve.
- `verdict.json` — the conclusion: rating, fair value, thesis, key risk.

You cannot see the PNGs. Judge each candidate from its manifest and from what
the wiki says the section argues.

## The target

**10–16 exhibits.** Below ten the report is under-illustrated; above sixteen the
reader stops looking at any of them. If the candidates cannot support ten good
ones, select fewer and say so — padding with a chart nobody needs costs more
than a missing chart.

## How to choose

Select an exhibit when it **carries an argument the section is already making**.
Reject one when it is merely available.

Concretely, prefer a chart that:

- shows the tension the wiki page names (a margin trend the section argues is
  structural; a peer gap the section says is closing),
- quantifies a claim the reader would otherwise take on trust,
- covers the verdict's stated thesis or key risk,
- has recent data and high `coverage` — a chart with `coverage` well under 1.0
  is showing a partial picture, and its caption must say so.

And drop a chart that:

- restates a number the prose already gives in full,
- duplicates another selected exhibit's message (two valuation exhibits saying
  the same thing is one exhibit and one distraction),
- rests on stale data (`recency_days` far past what the section discusses),
- belongs to no section's argument, however handsome.

Every section that makes a quantitative claim should have at least one exhibit.
No section needs four.

## Captions

Write each caption yourself; the manifest's `auto_caption` is raw material, not
the answer. A caption must:

1. **State what the exhibit shows a reader who is skimming** — the finding, not
   the mechanics. "Operating margin has widened 640bp over four years while
   revenue growth halved", not "Chart of margins over time".
2. **Carry the provider and the as-of date**, taken from the manifest's
   `data_sources` and `auto_caption` — never invented, never guessed.
3. **Disclose any gap the manifest discloses.** If `auto_caption` says a period
   was not reported, or a peer was excluded, the caption says it too. A reader
   must never learn about missing data from the chart's shape.
4. Stay one or two sentences.

Never claim a number the manifest does not contain. If you want to say a figure
moved, and the manifest gives only the latest value, say the latest value.

## Output

Write `charts/chartbook.json`:

```json
{
  "selected": [
    {
      "name": "<candidate name, exactly as in the manifest>",
      "section": "<one of: profile, business_model, competitive, supply_chain, financial, valuation, risk_news>",
      "order": 1,
      "caption": "<your caption>"
    }
  ]
}
```

`order` is the reading order within the report as a whole, starting at 1 and
strictly increasing. Every `name` must be a candidate that exists — a name that
resolves to no PNG is a hole in the assembled report.

Return, as your final message: how many candidates you saw, how many you
selected, which sections got none and why, and any candidate you rejected for a
reason worth knowing (stale, duplicative, uncovered).
