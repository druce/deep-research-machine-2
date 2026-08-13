# Topic: business profile — {company} ({symbol})

Read `prompts/prefetch_research/_shared.md` first; its search budget and its
"numbers are not your job" rule apply here.

This topic is the **orientation** file — what the company is, for a reader who
knows nothing about it. It is deliberately the shallowest of the seven: the
other six go deep on model, competition, risk, thesis, people and news, and
repeating them here buys nothing. Do not write a second version of those.

Financials are in `structured/`; the corporate description is in the 10-K's
Item 1 and in the Wikipedia page already gathered under `sources/`. Read those
before searching.

## Seed queries

1. `{company} company profile history founded`
2. `{company} products and services overview`
3. `{company} industry overview market position`
4. `{company} board of directors governance`
5. `{company} strategy priorities investor day`
6. `{company} ESG controversy OR governance concerns`

## What to extract

Short, factual, one screen each:

- `## What it is` — founding, headquarters, what it sells and to whom, how it
  got here. Dates matter; adjectives do not.
- `## Products` — the major lines, and roughly what each contributes. Take the
  contribution figures from the segment note, cited by artifact id.
- `## Industry` — the market it operates in and its position within it, with
  whose estimate that is.
- `## Strategy` — management's own stated priorities, from the transcript or an
  investor day, quoted or closely paraphrased with a date.
- `## Governance` — board composition, control structure, dual-class or
  founder control, and any live governance controversy. This is the one section
  no other topic covers, so give it real attention.

Cross-reference rather than duplicate: if something belongs to the competitive
or risk topic, one line and move on.
