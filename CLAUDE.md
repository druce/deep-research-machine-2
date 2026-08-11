# CLAUDE.md

SRA6 — skills-based equity research agent with a persistent per-ticker knowledge base.
**The spec is authoritative: see `sra6-spec.md` at the repo root. Read it before structural changes.**
It describes the **target** state (medallion layers, no vector index); this file describes the CLI
as it exists today. Where they differ, the spec is the destination, not a bug report.

## Layout
- `data/<TICKER>/` — persistent knowledge base: `sources/` (immutable frontmattered md),
  `structured/` (per-source JSON with `_meta`), `wiki/`, `index/`, `charts/`, `reports/`, `.state.json`
- `data/<TICKER>/.tmp/` — scratch for intermediate, regenerable artifacts (e.g. the peers
  gather/rank chain). Temporary artifacts go here; never clutter the reference dirs
  (`sources/`, `structured/`, `wiki/`) with intermediate results. Disposable — like `index/`,
  it can be deleted and regenerated. Constant: `lib/provenance.py:TMP_SUBDIR`.
- `sra.py` — deterministic driver CLI (init/status/prefetch/...). All deterministic work goes here, never into skills.

## Conventions
- `pathlib.Path`, type hints everywhere, no bare `except:`, data functions return `(success, data, error_msg)`, `main()` returns exit code
- Sources are IMMUTABLE — refreshes write new files with `supersedes:`, never overwrite
- Every fetched artifact carries provenance (frontmatter or `_meta`): source, url, fetched_at, as_of
- Tests: `uv run pytest -q -m "not integration"` must stay green; network tests are `@pytest.mark.integration`

## Commands
- `uv run python sra.py prefetch TICKER [--kinds a,b] [--stale-only] [--peers "AAA,BBB"]` — default 12 kinds:
  `profile,prices,technical,financials,estimates,targets,calendar,peers,filings,transcript,wikipedia,news`;
  `perplexity` is opt-in via `--kinds` (needs `PERPLEXITY_API_KEY`). Registry order is load-bearing
  (prices before technical, profile before wikipedia) and is pinned by `tests/test_registry.py`.
- `uv run python sra.py ingest TICKER [--new-only]` — chunk/embed `sources/*.md` into LanceDB
  (`data/<T>/index/`, table `chunks`); queue in `index/indexed_ids.json`, tagging view in
  `index/chunks_for_tagging.json`. The index is disposable: delete `index/` and re-ingest.
- `uv run python sra.py search TICKER --query Q [--sections a,b] [--top-k N]` — hybrid
  vector+BM25/RRF; every hit carries `source_id`, `url`, `as_of`, `fetched_at`, `title`
- `uv run python sra.py apply-tags TICKER --tags-file PATH` — merge tagger output
  (`[{"id": chunk_id, "tags": [...]}]`) into `section_tags`; warns on unselective tags
- `uv run python sra.py peers-candidates|peers-select TICKER` — deterministic four-source
  peer gather and top-5 selection into `structured/peers_selected.json`; every intermediate
  lives in `.tmp/`. Full contracts and gotchas: `sra6-spec.md` §13,
  `.claude/skills/sra-peers/SKILL.md`.
  Two things that bite from outside the peers flow: classification is FMP's taxonomy,
  NOT GICS (licensed) — columns are named `fmp_sector` / `fmp_industry`; and the gather
  records state kind `peers_candidates`, never `peers` (the SELECTED set), though
  `prefetch` treats either stage as satisfying the `peers` kind.
- FMP HTTP goes through `lib/fmp_http.py:fmp_get` — it appends the key, and no httpx
  message (which embeds the keyed URL) is ever allowed to reach a warning or a log.
- `uv run python sra.py questions/add-questions/mark-answered/wiki-log/wiki-index/mark-dirty/audit-page-citations TICKER ...` —
  question ledger + wiki bookkeeping for the research loop (spec §8.3); driven by `sections.yaml`
  (`lib/sections.py`) and `data/<T>/.research/questions.json` (`lib/questions.py`)
- Provider keys come from `.env` at the repo root, loaded once by `load_dotenv()` at the top of `sra.py`
  (`OPENAI_API_KEY` powers embeddings for ingest/search)
