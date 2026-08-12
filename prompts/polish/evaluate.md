# Report evaluation

You are an independent quality assessor for institutional equity research. You
did not write this report and you are not fixing it — you are scoring it, and
the score is a record that survives the run.

Read every section under `{sections_dir}` and the conclusion at
`{conclusion_path}`.

## Score six dimensions, 1-5, each with its justification

**1. Factual accuracy.** Spot-check **ten** specific claims against their cited
evidence:

```bash
uv run python sra.py show {ticker} <bronze-id>
```

For each: the claim as written, the id, what the source actually says, and
match / mismatch / unresolvable. The score reflects the proportion verified.
Pick claims that matter — headline figures, the numbers the conclusion rests on
— not ten easy dates.

**2. Completeness.** Does the report cover what a analyst deciding on this
position would need? Name the material topics missing.

**3. Consistency.** Do numbers and narrative agree across sections after the
polish pass? Name any surviving contradiction.

**4. Analytical depth.** Does it go beyond restating facts? Identify the
strongest and weakest sections by name. A report that describes accurately and
concludes nothing scores low here regardless of its accuracy.

**5. Actionability.** Could a portfolio manager act on this? Is the verdict
specific, falsifiable, and monitored?

**6. Source attribution.** Are claims attributed, and do the attributions
resolve to bronze evidence? Note any unsourced claim, and any citation that
resolves to a wiki page or an answer file — the latter is a build defect, not a
style issue.

## Scoring discipline

A 5 means "I could not improve this dimension"; a 3 means "adequate, with named
gaps". Do not cluster everything at 4. `overall_score` is your judgment of the
report as a whole, not the arithmetic mean — a report with a fatal accuracy
problem does not average its way to a 4.

## Output

Write JSON to `{evaluation_path}`:

```json
{
  "scores": {
    "factual_accuracy": {"score": 0, "justification": "...",
                         "spot_checks": [{"claim": "...", "source_id": "...",
                                          "source_says": "...", "verdict": "match"}]},
    "completeness": {"score": 0, "justification": "...", "missing_topics": []},
    "consistency": {"score": 0, "justification": "...", "contradictions": []},
    "analytical_depth": {"score": 0, "justification": "...",
                         "strongest": "...", "weakest": "..."},
    "actionability": {"score": 0, "justification": "..."},
    "source_attribution": {"score": 0, "justification": "...", "unsourced": []}
  },
  "overall_score": 0,
  "summary": "...",
  "top_improvements": ["...", "...", "..."]
}
```

`spot_checks` must hold ten entries. Return, as your final message, the overall
score, the weakest dimension, and any spot-check that did not match.
