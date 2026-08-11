# CLAUDE.md

SRA6 — skills-based equity research agent with a persistent per-ticker knowledge base.
**The spec is authoritative: see `sra6-spec.md` at the repo root. Read it before structural
changes.** The command surface below is the **target** state from spec §19 — implementation
status (what actually exists today) lives in `docs/superpowers/plans/`, not here.

## Layout
- `data/<TICKER>/` — persistent knowledge base: `sources/` (immutable bronze text, frontmatter),
  `structured/` (bronze JSON with `_meta`), `wiki/` (silver), `charts/`, `reports/`, `.state.json`
- `data/<TICKER>/.tmp/` — scratch for intermediate, regenerable artifacts. Never clutter the
  reference dirs (`sources/`, `structured/`, `wiki/`) with intermediate results. Disposable —
  can be deleted and regenerated. Constant: `lib/provenance.py:TMP_SUBDIR`.
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
| `wiki-log T --entry E` / `wiki-index T` / `mark-dirty T --section S` | wiki bookkeeping |
| `charts T [--verdict]` / `assemble T` / `snapshot T` | report rendering |
| `migrate T` | one-shot; removed once all corpora have migrated |

Retired (do not reintroduce): `ingest`, `search`, `apply-tags`, `audit-page-citations`, `render`,
the `sra-ingest` skill, the `sra-tagger` agent, `data/*/index/`, LanceDB, pyarrow, tiktoken.

Provider keys come from `.env` at the repo root, loaded once by `load_dotenv()` at the top of
`sra.py`.
