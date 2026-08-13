# CLAUDE.md

SRA6 — skills-based equity research agent with a persistent per-ticker knowledge base.
**The spec is authoritative: see `sra6-spec.md` at the repo root. Read it before structural
changes.** The command surface below is the **target** state from spec §19 — implementation
status (what actually exists today) lives in `docs/superpowers/plans/`, not here.

## Layout (medallion tree, spec §4)
- `data/<TICKER>/` — persistent knowledge base, three layers:
  - **bronze** (citable): `sources/` (+`sources/archive/` for superseded versions) — immutable
    fetched text; `structured/` — fetched or reproducibly computed JSON
  - **silver** (never citable): `derived/` (+`answers/` researcher answers, `peers/`
    peer-selection working set and audit trail — durable, not disposable scratch); `wiki/` —
    synthesized research knowledge
  - **gold**: `charts/` (+`candidates/`); `reports/<run>/` (+`reports/latest -> <run>/`,
    `log/` one markdown log per agent, `run_log.md` the assembled audit log)
  - plus `research/questions.json` (the question ledger, §14) and `.state.json`
- `sra.py` — deterministic driver CLI (init/status/prefetch/...). All deterministic work goes
  here, never into skills.

## Conventions
- `pathlib.Path`, type hints everywhere, no bare `except:`, data functions return
  `(success, data, error_msg)`, `main()` returns exit code
- Sources are IMMUTABLE — refreshes write new files with `supersedes:`, never overwrite
- Every fetched artifact carries provenance (frontmatter or `_meta`): source, url, fetched_at, as_of
- Tests: `uv run pytest -q -m "not integration"` must stay green; network tests are `@pytest.mark.integration`

## Command surface (target — spec §19)

| Command | Purpose |
|---|---|
| `init T` / `status T` | initialize ticker / report stale bronze |
| `prefetch T [--kinds] [--stale-only] [--peers]` | ticker gather |
| `prefetch-macro [--series] [--stale-only]` | shared macro gather |
| `prefetch-peers T [--stale-only]` | metric bronze for the SELECTED comparables |
| `peers-candidates T [--peers] [--top-funds N]` / `peers-select T [--ranked-file P]` | peer selection |
| `fetch-urls T [--from ANSWER_ID] [--max N]` | harvest researcher URLs |
| `manifest T` / `show T ID` / `grep T PATTERN [...]` / `eval-retrieval T [...]` | retrieval |
| `validate T` / `wiki-lint T` | fatal gate / advisory silver checks |
| `questions T [...]` / `add-questions T ...` / `mark-answered T ...` / `invalidate T [--apply]` | research ledger |
| `record-attempt T --question-hash H` / `drop-question T --question-hash H` | ledger transitions §19 omits — see below |
| `wiki-log T --entry E [--agents N --tokens N --minutes M --run R]` / `wiki-index T` / `mark-dirty T --section S` | wiki bookkeeping |
| `charts T [--verdict]` / `assemble T` / `snapshot T` | report rendering |
| `lint-render T [--run R]` | check rendered HTML+PDF for leaked CSS/template text (§22.4) |
| `run-log T [--run R]` | assemble `reports/<run>/run_log.md` from per-agent task logs |

`migrate` (spec §26) is intentionally not implemented — approved deviation, no legacy corpora
are being imported.

`record-attempt` and `drop-question` are additions to §19's table, not contradictions of it:
§20 defines `record_attempt` and `drop_question` as library functions but §19 lists no command
reaching either, and §3 forbids a skill or subagent doing that bookkeeping itself. Every
`research/questions.json` transition therefore has a CLI surface.

Retired (do not reintroduce): `ingest`, `search`, `apply-tags`, `audit-page-citations`, `render`,
the `sra-ingest` skill, the `sra-tagger` agent, `data/*/index/`, LanceDB, pyarrow, tiktoken,
and the harness **`deep-research` Workflow** in prefetch (§11.2) — it is harness-owned, so no
budget, model or effort reaches it, and on TOST its seven topics cost 728 subagents / 20.65M
input tokens / 34 minutes, 93% of the run, for 202 URLs. Prefetch now dispatches seven
`sra-researcher` agents against a written search budget instead.

Provider keys come from `.env` at the repo root, loaded once by `load_dotenv()` at the top of
`sra.py`.

## Rendered deliverables (spec §22.4)

`templates/report.css` is an **HTML fragment**, not a stylesheet: it opens and closes a single
style element and is spliced into pandoc's head verbatim. Append rules **above** the closing
tag, and never write that closing sequence anywhere else in the file — not even inside a CSS
comment, where HTML still honors it. Rules below the close tag do not apply *and* print as body
copy. `sra.py lint-render` and `tests/test_lint_render.py` fail the build for both.

`report.html` and `report.pdf` are generated files. Fix the template and re-assemble; a hand-edit
repairs one deliverable, leaves the other corrupt, and is discarded by the next `assemble`.

## Model and effort (spec §21.1)

Every stage names its own effort; nothing inherits the session default silently. Effort is the
preferred dial — lowering it also shortens tool loops, which cuts input tokens faster than it
cuts turns. Model downgrades are for stages whose input already contains the answer, and there
is exactly one (`sonnet` for the clarity fix in `/sra-assemble`).

Where each dial lives is a harness constraint: the **Agent tool takes `model` per call but no
`effort`**, so skill-dispatched agents get effort from `.claude/agents/*.md` frontmatter only.
**Workflow `agent()` takes both**, so `workflows/*.js` set effort per stage in a `STAGE_TUNING`
map that overrides the frontmatter floor. Judgment stages (critic, conclusion, whole-report
critique, lint, synthesizer, clarity critique) run `high`; worklist-applying and retrieval stages
run `medium`. Do not cheapen `evaluate` — §23.3's quality gate is calibrated against it, so
changing its model moves the measuring stick.
