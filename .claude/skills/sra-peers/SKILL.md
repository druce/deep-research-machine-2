---
name: sra-peers
description: Rate and select the peer set for a ticker. Gathers four peer sources deterministically, dispatches one rater subagent to rank the candidate table against the DEF 14A excerpt and the written rubric, and writes the top 5 with rationale. Use when asked to "pick peers", "find comparables", or refresh the peer set for a ticker.
---

# sra-peers — four-source peer selection (Bash + one rater subagent)

Gathering and selection are deterministic (`sra.py`); the only model work is
ranking, in a single call. Skipped entirely when the user named 5 or more peers.

Every artifact here is **silver** and lives under `data/<TICKER>/derived/peers/`
(spec §4.2, §13.3). None of it is citable: a comparables table in the report
cites each peer's own bronze evidence, and `peers_selected.json` records only
*why* those five were chosen (§13.6).

## Usage
`/sra-peers TICKER [--peers "AAA,BBB"]`

## Steps (in order)

1. **Gather (Bash, no subagent):**
   ```bash
   uv run python sra.py peers-candidates <TICKER> [--peers "<CSV>"] [--top-funds 5]
   ```
   Writes `data/<TICKER>/derived/peers/peers_candidates.json` plus one artifact
   per source that succeeded. Non-zero exit means every source failed **and** the
   user named nobody — report and stop.

2. **Decide whether to rank.** The whole pipeline is a top-up around the user's
   own peers. Read `derived/peers/peers_user.json` — an absent file means the
   user named zero peers. That file is never deleted (it is the user's own
   input), but `peers-select` ignores one whose `recorded_at` predates the
   current candidate set and says so in its `warnings`, so treat such a file as
   "no user peers":
   - **5 or more peers** — every slot is filled, so SKIP to step 4. The ranker
     would decide nothing and is pure cost.
   - **Fewer than 5 (including none)** — dispatch the ranker. `peers-select`
     pins the user's peers and fills the remaining slots from the ranking.

3. **Rank (ONE subagent).**

   First get a timestamp for the ranking — the rater has no Bash and cannot read
   a clock, and `peers-select` needs this stamp to tell a current ranking from
   one that ranked an older table:
   ```bash
   date -u +%Y-%m-%dT%H:%M:%S+00:00
   ```

   Then dispatch via the Agent tool with `subagent_type: "sra-rater"` and this
   prompt, filling in the absolute paths and that timestamp:

   > Read `<repo>/prompts/peers_rubric.md` — the rubric you are applying.
   >
   > Read `<abs ticker dir>/derived/peers/peers_candidates.json` — the enriched
   > candidate table, under `data.candidates`. The row with `"is_subject": true`
   > is the SUBJECT; it is never selectable.
   >
   > Also read `<abs ticker dir>/derived/peers/peers_proxy.json` if it exists —
   > `data.excerpt` is prose from the company's latest DEF 14A discussing its
   > compensation peer group.
   >
   > Pick the **5 best comparable companies** for the subject and return them
   > **in order, best first**, applying the rubric. In short: `description`,
   > `fmp_industry`, `market_cap` and `revenue_ttm` are the ground truth about
   > what each company does and how big it is —
   > weigh these above all the source labels.
   > `fund_count` and `sources: ["fmp_peers"]` are how candidates were
   > found, not evidence of comparability, and are weak. The proxy excerpt names
   > a peer group chosen for the executive **talent** market, so treat it as
   > evidence of size and growth comparability, not business comparability.
   >
   > You may name a company that appears only in the proxy excerpt and not in
   > the candidate table — say so in its rationale. Do not invent tickers; use
   > the correct US listing, and favor listed US companies over unlisted and
   > foreign ones.
   >
   > Write JSON to `<abs ticker dir>/derived/peers/peers_ranked.json`, in
   > EXACTLY this envelope — the `_meta` block is required and
   > `generated_at` must be the timestamp below, copied verbatim:
   >
   > ```json
   > {
   >   "_meta": {
   >     "id": "peers_ranked",
   >     "ticker": "<TICKER>",
   >     "producer": "model",
   >     "title": "<TICKER> ranked peer candidates",
   >     "source": "sra-rater",
   >     "generated_at": "<timestamp from the date command>",
   >     "as_of": "<the date part of that timestamp, YYYY-MM-DD>",
   >     "derived_from": ["peers_candidates"]
   >   },
   >   "data": [
   >     {"symbol": "PANW", "rank": 1, "rationale": "one concrete sentence naming the products or markets"}
   >   ]
   > }
   > ```
   >
   > `data` holds exactly 5 entries, ranks 1–5, best first.
   >
   > Then write ONE task log (§23.4) to
   > `<abs run dir>/log/<NN>_rater_peers.md`, with frontmatter
   > `purpose: rater`, `section: null`, `round: 1`, `label: "rater:peers"`,
   > `status`, `outputs`, and body headings `## Inputs`, `## Outputs`,
   > `## Notes`. Leave `started_at` and `finished_at` EMPTY — you have no Bash
   > and cannot read the clock; the run log sorts you by file time instead. In
   > `## Notes`, name the candidates you rejected and what decided it: the
   > ranking records only the five that survived.

4. **Select (Bash):**
   ```bash
   uv run python sra.py peers-select <TICKER>
   ```
   With step 3 skipped (5+ user peers, no ranking to read), `peers-select` uses
   the user's list on its own. It refuses (exit 1) a `peers_ranked.json` whose
   `_meta.generated_at` predates the candidate SET's last change — that is the
   previous run's answer to a different table; re-run step 3. A routine
   `prefetch` that rewrites the table without changing the candidates does *not*
   invalidate a ranking.

5. **Bookkeeping (Bash):**
   ```bash
   uv run python sra.py wiki-log <TICKER> \
       --entry "peers: selected <A,B,C,D,E> from <N> candidates" \
       --agents 1 --tokens <T> --minutes <M> --run <RUN>
   ```

6. **Report** the selected five with their rationale, and name the runners-up so
   the user can see what was close.

## Failure handling
- `peers-candidates` non-zero exit: report which sources failed from the
  `warnings` field; do not dispatch the ranker.
- `peers-candidates` exit 0 WITH `warnings`: a source was attempted and yielded
  nothing (e.g. "fund overlap produced no funds (5 of 5 /etf/info lookups
  failed)"). Rank anyway, but say which source was missing when you report.
- `peers-select` prints a `warnings` list too — a short peer set, or a stale
  artifact it ignored. Report those. Report counts only from the artifacts,
  never from the ranker's own prose summary.
- The ranker wrote invalid JSON, omitted `_meta`, or returned other than 5
  entries: show the parse or shape error in a follow-up Agent call asking it to
  rewrite `peers_ranked.json`; retry once.
