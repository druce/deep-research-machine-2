# STYLE — editorial style guide for all report sections

Referenced by `sra6-spec.md` §18.2 and handed whole to every writer, critic and rewrite
agent. It governs *how the prose reads*. What each section must contain is
`sections.yaml`; what a citation must resolve to is spec §8.

## Audience and stance

- **Who you are writing for:** a professional fundamental equity investor who already knows the
  sector and is deciding whether to own the stock. Assume they can read a balance sheet. Do not
  explain what gross margin is. Do not describe what the company does for its own sake.
- Write at the level of a Goldman Sachs or Morgan Stanley initiation report: clear, concise,
  measured, professional.
- The final section output should be structured so that a fundamental long/short equity PM could **scan in 1 minute and then spend 5-10 minutes drilling into in detail**.
- **Be skeptical.** Treat management's characterisation of the business as a claim to be tested
  against the numbers, not as a fact to be relayed. Be an analyst, not a reporter.
- **Lead with the investment implication, then support it.** Open each subsection with what the
  reader should conclude, not with a description of the data you are about to present.
  "Segment margin compression looks structural, not cyclical: ..." beats "The company reports
  results in three segments. Data center revenue was ...".
- When you present a number, tell the reader what it means for the stock. When you describe a
  competitive advantage, assess how durable it is. Neutral summaries belong in a 10-K; your job
  is to add judgment.
- **Quantify every claim you can.** A sentence with an adjective and no number is a hypothesis,
  not analysis. "Inventory days rose from 92 to 141" beats "inventory has built up meaningfully."
  Use specific numbers, not vague qualifiers.
- Have strong opinions and express them clearly — but ALWAYS back them up with data. Distinguish
  opinions (interpretation and analysis) from facts (the objective data). Label opinions with
  analytical framing: this indicates/implies/suggests, a reasonable conclusion is.
- **Where the evidence conflicts, surface the conflict rather than averaging it away.** Quantify
  both sides and say which one you weight more heavily and why.
- Acknowledge uncertainty where it exists — do not oversell.
- **No generic company description and no promotional language.** Cut anything that would appear
  verbatim in the company's own investor deck or an "About Us" page: founding-story colour with
  no investment consequence, product lists with no economics attached, "leading provider of",
  "well-positioned to capitalise on", "strong track record of innovation". If a sentence would
  survive unchanged in a report about a competitor, delete it.
- Each section should stand alone as useful to a reader who skips the others.
- Avoid repetition, refer to previous statements instead of repeating them.
- Attribution *in prose*: use "per the 10-K" or similar sparsely — once per source type is
  sufficient. After the first attribution, the reader understands your sourcing.

## Citations

Prose attribution is a courtesy to the reader. The footnote is the contract, and it is
mechanical:

- **Every claim carries `[^<id>]`**, regardless of how sparse the prose attribution is.
  The two are independent: dropping "per the 10-K" from a sentence never means dropping
  its footnote.
- **The id must be a bronze id** — a fetched document under `sources/` or a fetched or
  computed artifact under `structured/`. Never a wiki page, never a file under
  `derived/answers/`. A researcher's answer file records how the wiki learned something;
  it is not evidence, and `sra.py validate` fails the build over it.
- If the only support you have for a claim is a wiki note whose own citation you cannot
  resolve, **cut the claim**. Do not launder it by citing the note.
- **Never name an internal file, artifact id or repo path in prose.** `[^income_statement_yahoo]`
  in a footnote is correct; "per `income_statement_yahoo.json`" in a sentence is a hard-check
  failure. Name the *source* — "per the Q3 10-Q", "consensus per FMP".

## Labelling claims: reported vs. guidance vs. consensus vs. estimate

Never present these four kinds of number as if they were the same kind. The distinction is the
single most common way an equity report misleads. Signal it explicitly in prose:

| Kind | What it is | How to write it |
|---|---|---|
| **Reported** | Filed or announced actuals | "FY2025 revenue of $53.1B" (cite the filing) |
| **Guidance** | Management's own forecast | "management guides to $56–59B for FY2026 (Q2 call, May 2026)" |
| **Consensus** | Sell-side aggregate estimate | "consensus of $58.2B (FMP, as of July 2026)" |
| **Estimate** | Your own or a single analyst's derived figure | "assuming 8% unit growth and flat ASPs, ..." |

- Any forward-looking number needs its kind, its source and its as-of date.
- These four are the canonical set, and they map one-to-one onto the wiki tags
  `[REPORTED]` / `[GUIDANCE]` / `[CONSENSUS]` / `[ESTIMATE]`. There is no fifth kind and
  no `[ASSUMPTION]` — a derived figure, whoever derived it, is an estimate.
- Claims on the wiki pages carry `[REPORTED]` / `[GUIDANCE]` / `[CONSENSUS]` / `[ESTIMATE]`
  tags with as-of dates. Convert the tag into explicit wording — never drop the distinction,
  and never carry the bracket tags themselves into the report.
- An unlabelled forward-looking number on a wiki page is a research defect, not a writing
  one: you cannot recover its status from the number. Flag it rather than guessing.
- A gap between guidance and consensus is itself a finding. Quantify it when it is material.

## Source reliability hierarchy (most to least authoritative)

Where two sources disagree, prefer the higher one and **say that they disagree** rather
than silently picking a number.

1. **SEC filings** — `sources/*_sec_*.md`. Audited and legally binding. Authoritative for
   reported figures, segment definitions, risk language and share counts.
2. **Provider structured data** — `structured/*_yahoo.json`, `*_fmp.json`,
   `sec_financials_edgar.json`. The numeric substrate, split by provider so the two can be
   compared rather than blended. Where a provider disagrees with the filing, the filing wins
   and the gap is worth a sentence.
3. **Earnings-call transcripts** — `sources/*_transcript.md`. Management's own words:
   authoritative for *what management said*, evidence for nothing else. Label it as a claim
   and test it against the numbers.
4. **Computed artifacts** — `*_computed.json`. Exactly as good as their inputs, and
   reproducible from them, which is why they are citable. Check the `derived_from` chain
   before leaning hard on one.
5. **Fetched third-party documents** — `sources/*_web_page_*.md`, `*_news_*.md`,
   `*_wikipedia.md`. Quality varies; weight by the publisher, and prefer the primary source
   a piece cites over the piece itself.
6. **Live MCP / web tool results** — useful for checking a figure, but **not citable until
   fetched into bronze.** If a claim rests on something that exists only in a tool response,
   it has no footnote and cannot ship.

**Comparables** are not a file. The peer set is chosen in `derived/peers_selected.json`
(spec §13), which records *why those five* and is not itself evidence. The comparables
table is built from each peer's own bronze artifacts, and cites them.

## Formatting conventions

- First reference in the report: full legal name + exchange and ticker in parens, e.g.
  "D.R. Horton, Inc. (NYSE: DHI)"
- Subsequent references: the short name ("D.R. Horton") or "the company"
- Revenue: always label the fiscal year *and* its end date, e.g. "fiscal year 2025 (ended
  September 30, 2025)". **Take the fiscal year end from the subject's own profile artifact
  — never assume a December or September year end.** Fiscal and calendar years diverge for
  most of the tickers this system covers, and a mislabelled year is a factual error.
- Large numbers: "$34.3 billion", not "$34,300,000,000"
- Employee count: from the profile artifact, formatted with comma separators

## Number formatting

- **Stock prices**: Always format to nearest penny (2 decimal places), e.g., "$328.47"
- **Market capitalization**: Express in billions with 1 decimal, e.g., "$24.3B" or "$24.3 billion"; use trillions for >= $1T, e.g., "$3.45T"
- **Revenue / earnings**: Use billions or millions as appropriate, e.g., "$4.7 billion", "$312 million"
- **Percentages**: 1 decimal place for margins, growth rates, yields, e.g., "23.4%", not "23.4123%"
- **Ratios (P/E, EV/EBITDA)**: 1 decimal place, e.g., "18.3x"
- **Share counts**: Express in millions or billions, e.g., "1.2 billion shares outstanding"
- **Superscripts**: Use HTML `<sup>` tags, e.g., `<sup>1</sup>`, not caret syntax (`^1^`)
