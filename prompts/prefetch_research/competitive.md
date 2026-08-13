# Topic: competitive landscape — {company} ({symbol})

Read `prompts/prefetch_research/_shared.md` first; its search budget and its
"numbers are not your job" rule apply here.

The peer set has already been selected and their metrics fetched — see
`derived/peers/peers_selected.json` and the peer artifacts in `structured/`.
Use that peer list as your starting point rather than assembling your own, and
take peer financials from those artifacts instead of searching for them.

Market share is the exception: it is the one number here that is genuinely not
in the corpus, so sourcing it *is* the job.

## Seed queries

1. `{company} market share percent` — plus the industry name
2. `{industry} market share IDC OR Gartner OR Statista` — the research houses
3. `{company} vs <top peer> comparison` — run this for the two closest peers
4. `{company} losing share OR gaining share`
5. `{industry} consolidation M&A`
6. `{industry} new entrants OR disruption OR startup competition`
7. `{company} pricing power OR discounting`

## What to extract

- `## Share` — the best share figure you can source, **with the house that
  produced it, the date, and the segment it covers**. An unattributed share
  number is worthless; say `[GAP]` instead. Direction over 3–5 years matters
  more than the level.
- `## Rivals` — for each selected peer: where they overlap, the one thing that
  differentiates them, and their most recent strategic move. Two or three
  sentences each, not a profile.
- `## Where it wins and loses` — three or four of each, every one tied to
  evidence and marked strengthening or weakening.
- `## Consolidation` — recent deals with values, and whether this company reads
  as acquirer or target.
- `## Disruption` — two or three specific threats with a rough horizon. Name
  the entrant; "AI" is not a competitor.

Do not restate peer revenue and margin tables — cite the peer artifacts by id.
