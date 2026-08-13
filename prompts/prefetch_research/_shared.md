# Prefetch topic research — the contract

You are researching ONE topic about ONE company, to open a research file that
later phases build on. Read this file, then your topic file, then work.

## What you are actually producing

**URLs.** Your prose is an audit record and is explicitly *never evidence*
(spec §11.2) — no report sentence may cite it. What survives you is
`cited_urls`: the driver harvests those into `sources/`, and a later writer
cites the pages, not you. A finding whose URL never reaches `cited_urls` is
lost, however well you argued it.

So: prefer breadth of good sources over depth of analysis. Naming a primary
source you did not fully read is worth more than a paragraph reasoning from one
you did.

## Search budget — a ceiling, not a target

- Run the **seed queries** in your topic file. They are chosen to cover the
  topic; run them roughly as written rather than reformulating each one.
- Then **at most 6 follow-up searches**, and only where a seed result exposed a
  specific lead worth pulling — a named lawsuit, a cited figure you want the
  origin of, an executive you cannot place.
- **Hard ceiling: 14 searches** for the whole topic. Stop there and write.
- Open pages with `WebFetch` only where you will quote or characterize them:
  target **8–12 reads**, not everything a search returned. Every other
  worthwhile URL still goes in `cited_urls` unread — the harvester fetches it
  properly, with a browser fallback, and it becomes citable evidence either way.

A thin answer that respects the budget is the correct outcome for a thin topic.
Do not spend the ceiling for its own sake.

## Numbers are not your job

Revenue, margins, segment splits, balance-sheet lines, share counts, cash flow
and the rest are **already gathered**, deterministically, before you run:

- `data/<TICKER>/structured/*.json` — statements, estimates, price targets,
  ratios. Read with `uv run python sra.py show <TICKER> <artifact-id>`.
- `data/<TICKER>/sources/` — the 10-K, 10-Qs, 8-Ks and the latest transcript,
  as filed. Find them with `uv run python sra.py manifest <TICKER>`.

Treat both as authoritative and take figures from them directly.

**Do not search the web to confirm a number you already have, and do not
cross-check one filing against another.** A provider's statement and the filing
it was built from will disagree in small ways forever — over classification,
restatement and rounding — and reconciling that is not research, it is expense.
The single largest cost in the previous build was agents re-verifying figures
that were already sitting in `structured/`.

Search is for what the filings do not contain: competitive dynamics, pricing,
customer and channel behaviour, management commentary and credibility,
regulation in flight, third-party estimates, and recent events.

If a number you need is genuinely absent from both places, one search for it is
fine. Say where it came from.

## Writing it down

- **Body: 1,200 words maximum.** It is an audit record, not a report section.
  Bullets over paragraphs. No executive summary, no conclusion.
- Mark every forward-looking or non-historical figure with exactly one of
  `[REPORTED]` `[GUIDANCE]` `[CONSENSUS]` `[ESTIMATE]`, plus its as-of date and
  where it came from.
- Where two sources genuinely conflict on a material point, give both and say
  which you trust and why. A reconciled discrepancy is a finding; an averaged
  one is a loss.
- Use `[GAP]` for anything you could not answer, with one line on what you
  tried. That is more useful than a guess, and it seeds the question ledger.
- Quote sparingly and never more than a sentence or two from any one page.

## Sources

End the body with:

```
## Sources
https://…
https://…
```

Every URL you used or judged worth harvesting, one per line, deduplicated.
Prefer the primary document over the outlet that summarized it: the 8-K over
the news story about the 8-K, the transcript over the recap.

This list becomes `cited_urls` on your answer file. It is the whole durable
output of your run.
