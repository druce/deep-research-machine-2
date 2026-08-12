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
  - **gold**: `charts/` (+`candidates/`); `reports/<run>/` (+`reports/latest -> <run>/`)
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
| `peers-candidates T [--peers] [--top-funds N]` / `peers-select T [--ranked-file P]` | peer selection |
| `fetch-urls T [--from ANSWER_ID] [--max N]` | harvest researcher URLs |
| `manifest T` / `show T ID` / `grep T PATTERN [...]` / `eval-retrieval T [...]` | retrieval |
| `validate T` / `wiki-lint T` | fatal gate / advisory silver checks |
| `questions T [...]` / `add-questions T ...` / `mark-answered T ...` / `invalidate T [--apply]` | research ledger |
| `record-attempt T --question-hash H` / `drop-question T --question-hash H` | ledger transitions §19 omits — see below |
| `wiki-log T --entry E` / `wiki-index T` / `mark-dirty T --section S` | wiki bookkeeping |
| `charts T [--verdict]` / `assemble T` / `snapshot T` | report rendering |

`migrate` (spec §26) is intentionally not implemented — approved deviation, no legacy corpora
are being imported.

`record-attempt` and `drop-question` are additions to §19's table, not contradictions of it:
§20 defines `record_attempt` and `drop_question` as library functions but §19 lists no command
reaching either, and §3 forbids a skill or subagent doing that bookkeeping itself. Every
`research/questions.json` transition therefore has a CLI surface.

Retired (do not reintroduce): `ingest`, `search`, `apply-tags`, `audit-page-citations`, `render`,
the `sra-ingest` skill, the `sra-tagger` agent, `data/*/index/`, LanceDB, pyarrow, tiktoken.

Provider keys come from `.env` at the repo root, loaded once by `load_dotenv()` at the top of
`sra.py`.
