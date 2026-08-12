# Report quality: clarity, citations, exhibits, peers

**Status:** approved design, not yet implemented
**Origin:** defects found reading `data/SPCX/reports/2026-08-12/report.html`

## Problem

The SPCX run shipped six distinct classes of defect. Each traces to a specific
mechanism, and five of the six are deterministic — the pipeline has no code path
that could have caught them.

| Symptom | Mechanism |
|---|---|
| Sankey and price charts each render three times | Three independent emitters, no dedupe, and no stage reads the assembled report |
| `[^2]` renders as literal unclickable text | Sections carry `[^N]` markers; `references.md` is a plain `[N] …` list, so pandoc sees no footnote *definitions* |
| Every peer cell reads `$N/A` | Peer selection runs *after* prefetch; nothing ever fetches bronze for the five selected peers |
| Sentences compress into gibberish | `polish.md` enforces a per-section `not_longer_than` word gate, so every clarifying word must be bought with a deletion |
| `Sell $38.13` beside a `$129.81` weighted scenario | `cross_check.json` compares numbers *across sections*; the verdict-vs-valuation pair is not a checked pair |
| Report calls itself "the analyst" | Three template strings, plus `STYLE.md` |

## Non-goals

- No cold rebuild of SPCX in this work. The clarity pass (W5) only proves out in
  a full run; that is a separate follow-up task.
- No change to how sections are written, researched, or cited. This work touches
  rendering, gating, and one new fetch path.

---

## W1 — Exhibit duplication

Three emitters place charts. `price_weekly` and `income_sankey` hit all three.

1. `templates/final_report.md.j2:121` — dashboard "Stock Chart" block
2. `templates/final_report.md.j2:207` — dashboard "Income Statement Flow" block
3. `lib/render/assemble.py:346` `_body()` — chartbook exhibits inline per section
4. `templates/final_report.md.j2:237` — appendix "Chartbook" gallery, every exhibit again

### Changes

**`templates/final_report.md.j2`**
- Delete the `{% if chartbook %}` appendix gallery block and the
  `<li><a href="#chartbook">Chartbook</a></li>` TOC entry. Every exhibit already
  appears inline beside the section that argues it; the gallery was a verbatim
  third copy.

**`lib/render/assemble.py`**
- Add module constant `DASHBOARD_CHARTS = frozenset({"price_weekly", "income_sankey"})`.
- `load_chartbook()` drops any selection whose `name` is in `DASHBOARD_CHARTS`,
  appending an advisory string to `problems` (not fatal — the chart *is* in the
  report, just placed by the template).
- `_body()` tracks emitted image paths in a `set`; a repeat is skipped and
  appended to the returned problem list. `_body()`'s signature changes from
  `-> str` to `-> tuple[str, list[str]]`; the caller merges the problems.

**`prompts/chartbook.md`**
- New paragraph under the selection rules: the dashboard already places the
  weekly price chart and the income-statement Sankey; selecting either wastes an
  exhibit slot and the selection will be dropped.

**`lib/validate.py`**
- New fatal check inside `_check_assembled_reports`: parse `report.md` for image
  targets; any target appearing more than once is a `Finding` at fatal severity.
  This is the guarantee — the template, the selector, and `_body()` can all
  regress, and the gate still catches it.

### Tests — `tests/test_assemble_charts.py`

- A chartbook naming `price_weekly` produces zero inline occurrences and one
  advisory problem.
- An assembled `report.md` contains each image target exactly once.
- `validate` returns a fatal finding for a hand-built `report.md` with a repeated
  image target.

---

## W2 — Clickable citations

`[^N]` markers survive into HTML as literal characters (`report.html:1182`).
There is no footnote CSS in `templates/report.css` at all.

Native pandoc footnotes are not usable: `[^2]` has eleven call sites in the SPCX
report and a pandoc footnote may be referenced once. Anchors into the existing
References list handle repetition and keep one canonical entry per source.

### Changes

**`lib/render/postprocess.py`** — new `link_citations(markdown: str) -> str`,
added to the `postprocess()` chain after `colour_signed_cells` and before
`blank_image_alts`. `postprocess()` runs on the fully rendered template
(`assemble.py:202`), so both the body and the References section are in scope.

- Split on the `## References` heading. Everything above is the body.
- In the body, replace each `[^N]` with
  `<sup class="cite"><a id="cite-N-k" href="#ref-N">N</a></sup>`, where `k` is
  the 1-based occurrence index of that specific `N`.
- In the References section, rewrite each entry `[N] …` to carry
  `<span class="ref-n" id="ref-N">[N]</span>`, followed by the entry text and
  one back-link per call site: a bare `↩` when a source is cited once,
  `↩¹ ↩² …` when cited more.
- A `[^N]` whose `N` has no reference entry is left untouched, and the function
  returns the dangling numbers so the caller can report them.

**`lib/validate.py`**
- A dangling `[^N]` in an assembled report is a fatal finding. Today it ships as
  visible garbage in the PDF.

**`templates/report.css`**
- `.cite a` — superscript sizing, accent colour, no underline, and
  `scroll-margin-top` so an anchor jump does not land under the header.
- `#ref-N:target` — brief background highlight, so the reader sees which entry
  they landed on.
- Reference entries get `margin-bottom: 0.6em` — the half-line of vertical
  breathing room the current dense list lacks.

### Tests — `tests/test_postprocess_citations.py`

- Three occurrences of `[^2]` produce `cite-2-1`, `cite-2-2`, `cite-2-3` and
  three back-links on entry 2.
- A single occurrence produces one bare `↩`.
- No `[^` survives in the output for any resolvable marker.
- A `[^99]` with no entry 99 is returned as dangling and left literal.

---

## W3 — Peer fundamentals

`assemble._peer_row()` reads `data/<PEER>/structured/key_ratios_computed`. For
SPCX, `data/` holds only SPCX and an unrelated ticker set — BA, LMT, RTX, NOC and
GD were never fetched.

`prefetch --peers` feeds the *candidate* list to `lib/fetchers/peers.py`; it does
not fetch anything about the peers themselves. And it runs before selection, so
even in principle it cannot know the winners.

### Changes

**`sra.py`** — new command `prefetch-peers TICKER [--stale-only] [--force-lock]`
- Reads `derived/peers/peers_selected.json`; exits 1 with a pointer to
  `/sra-peers` when absent.
- For each of the five selected symbols: create the ticker tree if needed, then
  fetch exactly the three kinds `_peer_row()` consumes — `profile_yahoo`,
  `key_ratios_computed`, `technical_indicators_computed`. Roughly fifteen calls.
- `--stale-only` skips a peer whose three artifacts are all fresh, using the same
  staleness horizon `sra.py status` applies to the subject's own bronze.
- Prints `{fetched, skipped, warnings}`. A peer that fails is a warning, not a
  non-zero exit — four good comparables beat a failed build.

**`lib/render/assemble.py`**
- When more than half of peer metric cells read `N/A`, record a warning in the
  assembly block of `run_stats.json`. A silent peer-table failure should be
  visible in the run record, not only in the PDF.

**Skill wiring**
- `.claude/skills/sra-build/SKILL.md` — new step immediately after `/sra-peers`,
  with the same resume rule as its neighbours (skip when every selected peer has
  fresh metrics).
- `.claude/skills/sra-update/SKILL.md` — included in the refresh path.
- `.claude/skills/sra-assemble/SKILL.md` — Step 0 reports missing peer metrics so
  the operator can fix it before spending a polish chain.

**`CLAUDE.md`** — new row in the command-surface table.

### Tests — `tests/test_prefetch_peers.py`

- Missing `peers_selected.json` exits 1 with the pointer message.
- Given a fixture peer tree with the three artifacts, `_peers()` returns
  populated cells rather than `N/A`.
- `--stale-only` skips a peer whose artifacts postdate the staleness horizon.
- A peer whose fetch raises produces a warning and exit 0.

---

## W4 — Contradictory headline numbers

The SPCX report headlines `Sell` at a `$38.13` fair value while its
probability-weighted scenario produces `$129.81` — a divergence the text
acknowledges but never reconciles. `base_case_probability: 0.5` appears with no
statement of what the other half is.

Extracting `$129.81` from model-written prose is too fragile to gate on. The
scenario numbers move into the schema instead.

### Changes

**`verdict.json` schema** gains four fields:

```json
{
  "scenario_weighted_value": 129.81,
  "scenario_weighted_method": "probability-weighted 2028 EV/EBITDA scenarios discounted at 12%",
  "scenario_probabilities": {"bear": 0.25, "base": 0.50, "bull": 0.25},
  "reconciliation": "The DCF governs because … the scenario frame's 28x exit multiple assumes …"
}
```

**New `lib/verdict_checks.py`** — `check_verdict(verdict: dict, valuation_md: str) -> list[str]`

- If `scenario_weighted_value` is present and
  `abs(fair_value - scenario_weighted_value) / fair_value > 0.15`, then
  `reconciliation` must be non-empty, at least 40 words, and mention both
  figures. Absent or thin → failure.
- The `reconciliation` text must also appear in the valuation section — a
  reconciliation that lives only in JSON reconciles nothing for the reader.
- `scenario_probabilities` values must sum to 1.0 within 0.01. This is what kills
  an orphaned "base case probability 50%".
- `scenario_weighted_value` may be `null` for a report with no scenario frame;
  then the other three fields are not required.

**`lib/validate.py`** — calls `check_verdict` at the gold gate. Fatal.

**Prompts**
- `prompts/write/valuation.md` — emit the scenario fields and write the
  reconciliation into the section prose.
- `prompts/polish/conclusion.md` — the verdict card and the valuation section
  must agree, and the governing method is named in the first sentence of the
  valuation discussion, not at its end.
- `STYLE.md` — new rule: never present two materially different values for the
  same quantity without naming which governs and why.

### Tests — `tests/test_verdict_checks.py`

- 240% divergence with empty `reconciliation` → failure.
- Same divergence with a 60-word reconciliation naming both figures, present in
  the valuation markdown → pass.
- Reconciliation in JSON but absent from the section → failure.
- Probabilities summing to 0.9 → failure.
- `scenario_weighted_value: null` → pass with no other fields.

---

## W5 — Clarity

Two halves, both needed. Loosening the gate alone leaves nothing hunting for
gibberish; the clarity pass alone would be unable to spend words fixing it.

### (a) The gate moves from section to report

`prompts/polish/polish.md` runs `not_longer_than: {baseline_dir}/<section>.md`
per section. Its rationale is sound — a prior generation grew the body while
fixing nothing — but a per-section ceiling means a sentence can only be clarified
by mutilating its neighbour.

**`lib/hard_checks.py`** — two new rules, dispatched in `run_checks` alongside
the existing ones:
- `report_not_longer_than: <baseline_dir> [<factor>]` — sums words across every
  `<section>.md` in the current directory and compares against the same sum in
  the baseline directory, times `factor` (default `1.0`). The real budget.
- `not_longer_than_pct: <baseline_file> <factor>` — a single section may reach
  `factor ×` its baseline word count. Set to `1.10` in the polish prompt, so one
  section can grow while the report total holds.

Both take a directory or file argument plus an optional trailing number, so
`_parse` needs no change — it already splits on the first `": "` and hands the
remainder to the check.

`prompts/polish/polish.md` swaps the per-section rule for the pair, and states
the new contract: the report may not grow; an individual section may, by up to
10%, if another shrinks to pay for it.

### (b) A clarity pass over the assembled report

No stage in the pipeline reads `report.md`. `critique.md` and `evaluate.md` both
read `{sections_dir}` — which is why nothing saw the tripled Sankey, and why
cross-section referent ambiguity survives.

**New `prompts/polish/clarity.md`** — reads the assembled `report.md` and hunts
these failure modes, each drawn from an observed defect:

| Mode | Observed instance |
|---|---|
| Elliptical fragment with no verb | "Google's from the turn of the year" |
| Ambiguous referent | "That backlog" — SpaceX or CoreWeave |
| Unexplained derivation | why a six-month booking validates a 2028 multiple |
| Category error | accounting life treated as cash payback |
| Unjustified arithmetic | excluding $22.7T, then comparing against $5.8T |
| Prose contradicting its own numbers | "decelerating" while revenue rises |
| Governing method disclosed late | DCF vs. EBITDA frame revealed at section end |
| Ambiguous noun | "The BryceTech shares above" (market shares) |
| Repeated exhibit or table | backstop for W1 |
| Inconsistent convention | netting cash before compounding in one place, after in another |

Output: at most 12 items, each naming the section file, quoting the span, and
supplying the replacement text. Not a critique — a patch list.

**Fix pass** — before it runs, the current section files are copied to
`reports/<run>/sections_preclarity/`, mirroring the existing
`sections_prepolish/` convention. The pass then applies the clarity worklist
under `report_not_longer_than: sections_preclarity 1.03`. This is the one
pass permitted to grow the report, because every item it applies is an
explanation the reader needed and did not get. The 3% headroom is the whole
budget for all 12 items — an item needing more than its share must be raised as
a skip with a reason, exactly as `polish.md` already requires.

**Wiring**
- `.claude/skills/sra-assemble/SKILL.md` — new step between Step 3 (assemble) and
  Step 4 (validate): assemble → clarity critique → clarity fix → **re-assemble**
  → validate. The re-assemble is not optional; the fixes land in section files.
- `workflows/polish_chain.js` — the new stage in the same position.

---

## W6 — Voice

The report refers to itself as "the analyst" in three places
(`report.md:72`, `:145`, `:851`), all from `templates/final_report.md.j2`.

- Template: "the analyst's" → "the report writer's" (3 occurrences).
- `STYLE.md`: explicit rule — the report refers to its author as "the report
  writer", never "the analyst". Existing similes about *writing like* a sell-side
  analyst stay; they describe voice, not identity.
- All seven section rule lists in `sections.yaml` gain
  `not_regex: (?i)\bthe analyst\b`. The `not_regex` rule already exists in
  `lib/hard_checks.py` and is already used in every one of those lists, so this
  costs nothing but a line each.
- `prompts/polish/evaluate.md:24` — "a analyst" → "an investor" while there.

---

## Verification

Each deterministic workstream ships with the pytest listed under it.
`uv run pytest -q -m "not integration"` must stay green.

Then, against the existing `data/SPCX/reports/2026-08-12` sections:

```bash
uv run python sra.py prefetch-peers SPCX
uv run python sra.py assemble SPCX
uv run python sra.py validate SPCX
```

Expected: each image target once, `[^N]` rendering as clickable superscripts with
back-links, populated peer cells, and a **fatal** verdict finding — SPCX's
current `verdict.json` has no scenario fields and a 240% unreconciled divergence,
which is exactly what W4 is built to stop. Fixing that report's content is not in
scope; the gate firing is the pass condition.

W5's prose changes are prompt-level and only exercise in a cold rebuild, which is
a separate follow-up task.

## Sequencing

1. **W1, W2, W6** — pure render and template. Fastest feedback, no new data paths.
2. **W3** — new fetch command and skill wiring.
3. **W4** — schema change plus the gold gate. Touches prompts that write
   `verdict.json`, so it lands after the render layer is stable.
4. **W5** — prompts and skill wiring; verified in the follow-up rebuild.
