# Wiki lint — the two judgments a program cannot make

You are auditing the working notes an equity research report will be written
from. The deterministic checks have already run (§22.1): numeric claims without
citations, forward-looking numbers without a status tag, ownership breaches,
duplicate figures, broken `built_from` links and unindexed entity pages are all
caught by `sra.py wiki-lint` and are **not** your job. Do not repeat them.

You are here for exactly two questions, and §22.1 limits you to them:

1. **Does each cited source actually support the claim it is attached to?**
2. **Is each claimed tension genuine?**

Everything else you notice — a thin section, a stale figure, a claim you find
unconvincing — is a research question, not a lint finding. Say so in the
worklist and move on.

## What you read

Under `{workdir}`:

- `wiki/00_index.md`, and every page it lists
- `wiki/entities/*.md`
- for each citation you check, the source itself:

```bash
uv run python sra.py show <TICKER> <bronze-id>
```

Read the source. A citation you did not open is a citation you did not check,
and reporting it as verified is worse than skipping it.

## Judgment 1 — does the source support the claim?

For each cited claim you sample, decide one of:

- **supported** — the source states the claim, or states something the claim
  reports faithfully (a rounded figure, a restated sentence).
- **partial** — the source is about the right subject but does not carry the
  specific number, period, or scope the claim asserts. This is the most common
  real defect: a claim about FY2026 gross margin cited to a filing that reports
  FY2025.
- **unsupported** — the source does not contain the claim at all. Usually a
  citation attached to the wrong id, or a claim that drifted during synthesis.
- **wrong-layer** — the claim leans on the source for something the source
  cannot establish (a market share figure cited to the company's own 10-K, a
  competitor's roadmap cited to a news roundup that speculates about it).

Sample deliberately rather than exhaustively: every quantified claim in
`valuation` and `financial`, every competitive-position claim in `competitive`,
and a spread of the rest. Prefer claims that would change the verdict if wrong.

## Judgment 2 — is the tension genuine?

A tension is genuine when two well-supported facts genuinely pull in opposite
directions for the investment case, and the page has quantified both sides.
It is NOT genuine when:

- both sides are the same fact stated twice with different framing,
- one side is unsupported, or supported only by a model's own reasoning,
- the "tension" is a truism about the industry that would be true of any
  company in it,
- it resolves immediately once a date or a scope is stated (a "conflict"
  between a FY2025 actual and FY2026 guidance is not a tension).

A manufactured tension is expensive: the section writer is instructed to take a
position on it, so it becomes a paragraph of prose arguing about nothing.

## Output

Write your findings to `{findings_path}` as JSON in exactly this shape:

```json
{
  "citation_findings": [
    {
      "page": "valuation",
      "claim": "<the claim, quoted>",
      "cited_id": "<bronze id>",
      "verdict": "partial",
      "why": "<what the source actually says, in one sentence>",
      "question": "<the research question that would settle it, or null>"
    }
  ],
  "tension_findings": [
    {
      "page": "competitive",
      "tension": "<the tension, quoted>",
      "verdict": "not-genuine",
      "why": "<one sentence>",
      "question": "<research question, or null>"
    }
  ],
  "checked": {"citations": 0, "tensions": 0}
}
```

`verdict` is one of `supported`, `partial`, `unsupported`, `wrong-layer` for
citations, and `genuine`, `not-genuine` for tensions.

Write a `question` whenever the finding is fixable by evidence rather than by
editing — the orchestrator turns each one into a ledger entry with
`--origin lint`, and a finding with no question is a note nobody will action.
Phrase it as a question a researcher can answer from sources, naming the
company, the metric and the period.

`checked` records how many of each you actually opened and read. Report it
honestly; an inflated count makes the next run skip an audit that never
happened.

## What you must not do

- Do not edit any wiki page. You report; the research loop fixes.
- Do not add questions yourself. The driver owns the ledger (§3).
- Do not fetch anything. Every source you need is already in bronze; a claim
  whose support is not on disk is exactly the finding to report.
