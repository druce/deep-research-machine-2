# Topic: executives — {company} ({symbol})

Read `prompts/prefetch_research/_shared.md` first; its search budget and its
"numbers are not your job" rule apply here.

**Compensation comes from the DEF 14A**, which is already gathered — the peer
pipeline fetches it, so look in `sources/` via `sra.py manifest {symbol}` before
searching for a pay figure. Salary-aggregator estimates are not evidence when
the proxy is on disk.

## Seed queries

1. `{company} CEO biography background`
2. `{company} CFO appointed background`
3. `{company} management team leadership`
4. `{company} executive departure OR succession`
5. `{company} CEO interview` — for stated priorities in their own words
6. `{company} insider selling OR Form 4` — pattern, not individual trades

## What to extract

Cover the CEO and CFO properly; cover others only where they matter to the
investment case — a COO at an operations-led company, a CTO at a platform
company. Five profiles is plenty; do not pad to fill an org chart.

For each:

- **name, title, since when** — tenure in role and at the company;
- **background** — prior roles that explain why they hold this one;
- **compensation** — from the proxy, cited by artifact id, with the fiscal year;
- **track record** — what has demonstrably happened on their watch, good or bad,
  with a date;
- **credibility** — where you can source it: has guidance been met, do the
  transcripts show consistency or drift?

Then:

- `## Changes and succession` — recent departures and arrivals, whether the
  company has named a successor plan, and any concentration of key-person risk.
- `## Insider activity` — the pattern only, from Form 4 aggregates. Note that a
  10b5-1 sale is not a signal and should be described as such.

Where a role is vacant or held on an interim basis, say so — that is often the
most informative fact in this file.
