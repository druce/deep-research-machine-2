# Topic: risks — {company} ({symbol})

Read `prompts/prefetch_research/_shared.md` first; its search budget and its
"numbers are not your job" rule apply here.

**Start with the filing, not the web.** The 10-K's Item 1A is the company's own
risk disclosure and it is already in `sources/` — find it with
`sra.py manifest {symbol}` and read it. Leverage, maturities, coverage and
currency exposure are in `structured/`. Take all of that as given.

Your job is the part Item 1A cannot do: which of those risks is **live now**,
what a third party says about it, and what is missing from the company's own
list. A risk restated from the 10-K with no external corroboration and no
current status is not worth the words.

## Seed queries

1. `{company} risk factors analysis` — sell-side or independent commentary
2. `{company} litigation OR class action OR regulatory investigation`
3. `{company} regulation OR legislation` — the rules in flight in its industry
4. `{company} competitive threat OR market share loss`
5. `{company} customer concentration OR churn OR pricing pressure`
6. `{company} debt covenant OR credit rating OR downgrade`
7. `{company} cybersecurity incident OR data breach OR outage`
8. `{company} key person OR succession OR executive departure`

## What to extract

Six to ten risks, no more, each as a short block:

- **the risk**, in one sentence, specific to this company — not "competition"
  but the named competitor and the segment they are taking;
- **evidence**: what makes it live, with a date and a URL;
- **severity**: your judgement of likelihood and what it would cost, with the
  figure sourced from `structured/` or the filing where one applies;
- **mitigant**, where the company or a source names one.

Group under `## Operational`, `## Financial`, `## Regulatory`, `## Market`, and
drop any heading you have nothing solid for. An empty heading is worse than a
missing one.

Note explicitly, under `[GAP]`, any risk you believe is real but could not
source.
