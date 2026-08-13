# Topic: recent developments — {company} ({symbol})

Read `prompts/prefetch_research/_shared.md` first; its search budget and its
"numbers are not your job" rule apply here.

The earnings numbers themselves are already in `structured/` and in the filings.
What you are after is the **narrative and the dates** — what happened, when, and
how it was received — with a URL for each.

## Seed queries

Run these, adjusting only the year where a query obviously needs the current one:

1. `{company} earnings results guidance` — the last four quarters
2. `{company} acquisition OR merger OR divestiture`
3. `{company} layoffs OR restructuring OR reorganization`
4. `{company} CEO OR CFO appointment OR departure`
5. `{company} lawsuit OR SEC investigation OR settlement`
6. `{company} partnership OR alliance announcement`
7. `{company} analyst upgrade downgrade price target`
8. `{company} short seller OR accounting concerns`

## What to extract

One dated line per development, newest first, grouped under three headings —
`## Recent Developments`, `## Regulatory & Legal`, `## Strategic & Competitive`.

For each: the date, what happened in one or two sentences, and the market
reaction if a source states it. Skip items with no date you can source.

Prefer the 8-K or the company release over the outlet that covered it, and put
both URLs in `## Sources` when the coverage adds something.

Anything material you find but cannot date, list under `[GAP]` rather than
guessing.
