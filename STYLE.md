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
- **Be skeptical.** Treat management's characterization of the business as a claim to be tested
  against the numbers, not as a fact to be relayed. Be an analyst, not a reporter.
- **Lead with the investment implication, then support it.** Open each subsection with what the
  reader should conclude, not with a description of the data you are about to present.
  "Data center margin compression looks structural. Segment margin fell 340bp ..." beats "The
  company reports results in three segments. Data center revenue was ...".
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
  verbatim in the company's own investor deck or an "About Us" page: founding-story color with
  no investment consequence, product lists with no economics attached, "leading provider of",
  "well-positioned to capitalize on", "strong track record of innovation". If a sentence would
  survive unchanged in a report about a competitor, delete it.
- Each section should stand alone as useful to a reader who skips the others.
- Avoid repetition, refer to previous statements instead of repeating them.
- Attribution *in prose*: use "per the 10-K" or similar sparsely — once per source type is
  sufficient. After the first attribution, the reader understands your sourcing.

## Voice and sentence mechanics

Everything above governs *what you say*. This governs *how the sentence is built*, and it is
the part an LLM writer gets wrong. There are two failure modes. The first is corporate mush,
covered above. The second is the one that actually shows up in this pipeline's drafts:

**Epigrammatic compression** — the aphoristic essay voice. Short abstract subject, colon,
evidence, four-word verdict. Novel metaphor doing the work a number should do. Every paragraph
shaped like a punchline. It reads as confident and it is not analysis; it is cadence wearing
analysis as a costume. Write like a sell-side analyst who has to get this out before the open,
not like an essayist with a thesis to land.

Diagnose it by **form**, not content. The judgment, the numbers and the positions demanded
above all stay. Only the syntax changes.

1. **One idea per sentence, in subject–verb–object order.** Name the actor and give it a real
   verb. "Attach rates are rising" beats "The moat is attach". If your subject is an abstract
   noun with a definite article — *the moat, the falsifier, the load-bearing assumption, the
   mechanism, the escape, the real leverage* — rewrite the sentence around whoever or whatever
   is doing something.
2. **Budget the antithesis: at most one "X, not Y" per section.** It is the strongest
   rhetorical move available and it goes dead on the second use. Once you have spent it, state
   the two things in separate sentences and say which one you use.
3. **Budget the punctuation.** At most one em dash per paragraph and one mid-sentence colon per
   subsection. Semicolons: rare. If a sentence needs two of these to hold together, it is two
   sentences.
4. **No new metaphor.** Standard market vocabulary is correct and expected — the GAAP/non-GAAP
   wedge, net cash, re-rating, the print, beat-and-raise, share donor, air-pocket. Inventing one
   is not: *load-bearing assumption, purchased migration channel, advisory glide path,
   re-plumbing the SOC, the seam this exists to pry open, the second dollar, arbitraging its own
   currency*. Every invented figure of speech is a number you did not write. Write the number.
5. **Do not end paragraphs on a kicker.** End on the most important fact, or on the position you
   take, in an ordinary sentence. A plain short sentence is fine — "Insiders have bought nothing
   here" is fine. A manufactured one is not: "Treat it as a ceiling." "Volatility, not terminal
   value." "And neither binds."
6. **Address the reader as "we", never "I" and never the imperative.** "We use the organic
   series", not "Underwrite the 28%", "Weight the organic series", or "on my arithmetic". The
   sections are written in parallel and must share one voice: first person plural, and only
   where you are stating a view.
7. **Headings are labels.** "Balance sheet and dilution", "SASE", "Return on capital", "Peer
   benchmark". Not "Leverage is not a credit question; it is a share-count question". The thesis
   goes in the section's first sentence, where the reader is already looking.
8. **Complete sentences, always.** No verbless fragments, no headline-ese. "Data gravity: XSIAM
   ARR above $600 million, up 100% across 740 customers" is a caption, not prose.
9. **American spelling throughout.** amortization, organization, recognized, platformized,
   realized, labeled. Not amortisation, organisation, recognised.
10. **Cut the third item.** Where you have written a triad, keep the two that carry numbers.
    Three-part lists read as rhetoric unless the list is genuinely exhaustive.

### Rewrites

| Written like an LLM | Written like an analyst |
|---|---|
| The moat is attach: roughly one million firewalls in the field average more than four subscriptions each against an eleven-module catalog. | Attach rates are the durable advantage. Roughly one million installed firewalls carry more than four subscriptions each, out of an eleven-module catalog. |
| Bulls capitalize the first number, bears the second, and the reconciliation disappears exactly when the multiple most depends on it. | Management stops disclosing the core-versus-acquired split after Q4 FY2026, so the 60% and 28% figures can no longer be reconciled. We use the 28%. |
| The load-bearing assumption is consolidation: enterprises keep collapsing point products onto one vendor and pay more each renewal. | The model assumes enterprises keep consolidating point products onto one vendor and paying more at each renewal. If attach stalls, cohort retention decays toward the industry norm. |
| Underwrite the 28%: it is the growth the company controls, and the other 32 points were bought with 112 million shares. | We use the 28% organic figure. It is the growth the company controls; the other 32 points were bought with 112 million shares. |
| The second dollar is worse where it matters: subscription and support gross margin, the 80% line, fell 90bp. | Incremental margin is worse in the 80% of revenue that matters. Subscription and support gross margin fell 90bp in FY2025 as cloud hosting costs rose $189.5 million. |
| Check Point is the clearest donor at 6% fiscal 2025 revenue growth. | Check Point is the most likely share donor. FY2025 revenue grew 6%. |
| Nir Zuk's resignation, softened by an advisory glide path to November 2, 2026. | Nir Zuk resigned as CTO and director effective August 14, 2025, staying on as an advisor through November 2, 2026. |
| Volatility, not terminal value. | We read the Evercore cut as a volatility event, not a change to terminal value. |

### Count these before you report a draft done

- "X, not Y" constructions: **at most 1** per section
- Em dashes: **at most 1 per paragraph**
- Sentences whose subject is "The &lt;abstract noun&gt;": **at most 2** per section
- Invented metaphors: **0**
- Instances of `I`, `my`, or a bare imperative aimed at the reader: **0**
- British spellings: **0**

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

## Labeling claims: reported vs. guidance vs. consensus vs. estimate

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
- An unlabeled forward-looking number on a wiki page is a research defect, not a writing
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
  most of the tickers this system covers, and a mislabeled year is a factual error.
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
