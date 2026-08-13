# Topic: business model — {company} ({symbol})

Read `prompts/prefetch_research/_shared.md` first; its search budget and its
"numbers are not your job" rule apply here.

**The segment tables are already gathered.** Revenue mix, geography, margins,
working-capital lines and R&D are in `structured/` and in the 10-K's Item 7 and
segment note. Read them; do not re-derive them and do not search to confirm
them.

What the filings do not give you is *why the model works and whether it keeps
working* — unit economics as practitioners describe them, the moat's condition,
and what the company depends on. That is what to search for.

## Seed queries

1. `{company} business model how it makes money`
2. `{company} unit economics OR take rate OR ARPU OR net revenue retention`
3. `{company} competitive moat OR switching costs OR network effects`
4. `{company} pricing strategy OR price increase`
5. `{company} suppliers OR vendor dependency OR single source`
6. `{company} TAM OR market size` — and who is estimating it
7. `{company} product roadmap OR new products`
8. `{company} customer churn OR retention OR cohort`

## What to extract

Under these headings, dropping any you cannot source:

- `## Revenue model` — how each stream is actually earned and priced. Cite the
  segment figures from `structured/` by artifact id; do not restate the tables.
- `## Unit economics` — take rate, ARPU, CAC/payback, retention. These usually
  come from the transcript or from an industry write-up, not the 10-K.
- `## Moat` — each advantage named, and the specific evidence it is holding or
  eroding. Compare against the two or three closest competitors. An unevidenced
  moat claim is worth nothing here.
- `## Dependencies` — suppliers, platforms, single points of failure.
- `## Growth drivers` — where the next dollar comes from, and the TAM estimate
  with **whose** estimate it is and when it was made.

Where a source and the filings disagree about the size of something, say so and
say which you trust.
