# **SRA6 — Skills-Based Equity Research Agent with a Persistent Per-Ticker Knowledge Base**

**Status:** Authoritative specification. Target state.
 **Date:** 2026-08-10
 **Location:** `~/projects/sra6`. `~/projects/sra5` remains untouched as reference.

This document is the single authority for SRA6. It supersedes:

- the previous `sra6-spec.md`,
- `docs/plans/sra6-interface-contracts.md`, including all nine amendments,
- `docs/superpowers/specs/2026-08-01-peers-pipeline-design.md`,
- `docs/superpowers/specs/2026-08-09-medallion-restructure-design.md`,
- `CHARTS.md`.

The two dated design documents remain on disk as historical records. Where they conflict with this specification, this specification governs.

`STYLE.md` is incorporated by reference (§18.2) and remains a separate versioned prompt file.

This specification describes the target system. Implementation status belongs in `docs/superpowers/plans/`, not here.

------

# **Part I — System Definition**

## **1. Motivation**

### **1.1 The sra5 baseline**

sra5’s Workflow-based `/sra` pipeline produced a PANW report rated 4.7/5 in self-evaluation. It required:

- 2h37m,
- 10.5M tokens,
- 169 subagents.

Post-mortem reconstruction, validated against `cost.json`, showed:

- Every subagent incurred a ~50k-token context floor.
  - Minimum observed: 46k.
  - ~42k came from the fixed Claude Code subagent harness.
  - ~8.5k came from project `CLAUDE.md`.
  - This floor is not meaningfully reducible.
- 126 of 169 agents, or 75%, performed mechanical work such as:
  - writing JSON sidecars,
  - copying files,
  - running `uv run python`,
  - checking whether files existed.
- Those mechanical agents consumed 66% of all tokens.
- Actual model work used 43 agents and 3.5M tokens.

The cause was architectural: the Workflow JavaScript sandbox had no filesystem or process access, so deterministic operations had to be delegated to Bash-capable subagents.

sra5 was also batch-oriented. State lived under per-run paths such as:

```text
work/<SYM>_<DATE>/
```

Refreshing one item required rerunning the pipeline. Knowledge did not accumulate across runs. Provenance also terminated at internal files such as:

```text
source: key_facts.json
```

The budgets in §23 derive from these measurements.

### **1.2 The attribution defect**

An intermediate SRA6 design stored model-synthesized research answers in `sources/`, which this specification reserves for evidence-grade source material.

Examples:

```text
<date>_research_answer_*.md
```

Those files were indexed and cited by `source_id` exactly like filings. They also cited each other, producing chains such as:

```text
report → wiki → answer_r2 → answer_r1 → 10-K
```

A report citation could therefore terminate at model-generated text rather than source evidence.

In the PANW corpus:

- 33 distinct citation targets existed.
- At least 5 were model output.
- Ten `research_answer` files totaled ~110KB.
- These answer files represented roughly 15% of the corpus by size.

The same issue existed in `structured/`, where `peers_selected.json`, produced through model judgment, sat beside fetched artifacts such as `income_statement_yahoo.json`.

SRA6 separates evidence from non-evidence in the filesystem. Model-generated and selection-pipeline artifacts never reside in fetcher-owned evidence directories.

### **1.3 The pipeline was too heavy for the corpus**

Measured 2026-08-09:

| **Layer**                                           | **Size**                                                     |
| --------------------------------------------------- | ------------------------------------------------------------ |
| PANW bronze                                         | 732KB / 38 files                                             |
| PANW bronze with 3 years of filings + 8 transcripts | ~2MB ≈ 500k tokens                                           |
| PANW silver wiki                                    | 85KB across 2 of 7 sections; projected ~300KB ≈ 75k tokens for all seven |

The entire synthesized layer fits in context.

A section writer reads roughly one 12k-token wiki page. A cross-section critic reads all seven pages at roughly 75k tokens.

The write, chart, and assemble phases therefore require no retrieval system.

Retrieval is needed only by researchers reading bronze evidence. A vector index added:

- an OpenAI embedding dependency,
- a tagger subagent,
- a chunk schema,
- an `indexed_ids` queue,
- an additional staleness surface.

§9 replaces this with a manifest plus deterministic grep. §9.2 defines the retrieval evaluation.

------

## **2. Goals**

1. **Persistent per-ticker knowledge base** Store ticker state under:

```text
data/<TICKER>/
```

1. Build it once, then update it incrementally.

2. **Lightweight updates** Refresh only stale evidence, calculate the resulting invalidation set, reopen only affected research questions, and regenerate only affected report sections.

3. **Explicit data layers**

   

   Maintain:

```text
evidence → synthesized knowledge → report
```

1. Bronze evidence remains authoritative. Silver artifacts are working derivations and audit records. Gold artifacts are report outputs.

2. **End-to-end citation provenance** Every citation in the final report must resolve mechanically to bronze evidence. This is a hard requirement. Claim coverage—whether every assertion has a citation—is advisory rather than mechanically guaranteed (§22.2).

3. **Structured data separated by original source**

   

   Every structured artifact contains `_meta` with enough information to identify how it was produced and, for bronze artifacts, how to regenerate it.

4. **Institutional chartbook**

   

   Render charts from downloaded data. Use model judgment to select the exhibits most relevant to the report thesis.

5. **Efficiency**

   - Cold build:

     - ≤100 model subagents,
     - ≤6M tokens (reduce to half or less if possible)
     - ≤60 minutes.
   
     Incremental limits:

     - bare refresh or directed research ≤ 30 minutes
     - directed research ≤8 model subagents.
   
   Deterministic work runs through in-session Bash and Python, not subagents.

   Model work remains on the Max subscription through in-session subagents. SRA6 does not use API/SDK billing for model work.

### **Non-goals**

- No project-authored dynamic Workflow orchestration engine, DAG YAML, or generated interpreter JavaScript.
- SRA6 may invoke the harness-provided `deep-research` Workflow (§11.2), but does not author it.
- Orchestration uses skills plus the deterministic Python driver.
- Two static Workflow scripts are permitted for quality loops (§15.2).
- No SQLite state layer.
- No vector store (§9, §27.7).
- No agent-memory framework (§27.3).
- No knowledge graph framework.
- Procedural memory across tickers is out of scope (§28.3).

------

## **3. Operating Model**

SRA6 has two primary entry modes.

### **Cold build**

```text
/sra-build PANW
```

Creates `data/PANW/` and the first report.

### **Incremental update**

```text
/sra-update PANW
```

Refreshes stale bronze evidence, computes invalidation, reopens affected questions, regenerates dirty wiki and report sections, and reassembles the report.

Directed research:

```text
/sra-update PANW "research Zscaler's SASE win rates vs PANW"
```

This:

1. adds the instruction as a research question,
2. performs focused research,
3. harvests cited URLs into bronze,
4. updates the relevant wiki page or entity page,
5. regenerates affected report sections,
6. reassembles the report.

Several instructions may be given in one invocation. Each quoted argument becomes one research question:

```text
/sra-update PANW \
  "research Zscaler's SASE win rates vs PANW" \
  "what is CrowdStrike's renewal pricing trend?" \
  "how much of FY27 revenue is already in RPO?"
```

The questions enter one research round together and are batched by §14's fan-out, so three instructions do not cost three times one instruction.

Instruction boundaries come from the shell, never from splitting prose. The driver does not parse a single argument into multiple questions on punctuation or newlines, because separators occur inside real questions.

There is no cap on how many questions one invocation may raise. Fan-out runs them in waves of `MAX_PARALLEL_AGENTS`, and the run's budget (§23.3) bounds the spend; whatever is not reached stays open for the next run (§14).

Granular skills remain directly callable:

```text
/sra-research PANW competitive
/sra-write PANW valuation
/sra-chartbook PANW
```

### **Division of labor**

Deterministic work runs through the Python driver:

```text
sra.py <command>
```

This includes:

- fetching,
- manifest generation,
- grep and show,
- validation,
- chart rendering,
- report assembly and rendering,
- state bookkeeping,
- peer candidate gathering and selection mechanics.

Model work runs in subagents:

- research,
- wiki synthesis,
- peer ranking,
- section writing,
- critique,
- chart selection,
- judgment lint.

Orchestrating skills should make only coarse control-flow decisions:

```text
run driver phase → fan out model wave → run driver phase
```

All durable state is stored in files.

Every phase must be idempotent and resumable.

**Nothing deterministic belongs in a skill. If it can be implemented as a function, it belongs behind an** **`sra.py`** **command.**

------

# **Part II — Data Model**

## **4. Layer Model**

```text
data/PANW/
  sources/                  BRONZE: immutable fetched textual evidence
    00_manifest.md          generated catalog of current evidence
    archive/                superseded versions, <id>_<superseded-date>.md

  structured/               BRONZE: fetched or reproducibly computed JSON

  derived/                  SILVER: non-citable working derivations and audit records
    answers/                researcher answers
      *.urls.json           URL → bronze-id maps
    peers/                  peer-selection working set and audit trail
      peers_user.json
      peers_fmp.json
      peers_funds.json
      peers_proxy.json
      peers_candidates.json
      peers_ranked.json     the rater’s ordered top 5 — the selection audit record
      peers_selected.json   the final peer set (§13)

  wiki/                     SILVER: synthesized research knowledge
    00_index.md
    log.md
    entities/
    <section>.md

  charts/                   GOLD support artifacts
    candidates/
    chartbook.json

  reports/<run>/            GOLD
    report.md
    report.html
    report.pdf
    references.md
    citation_map.json
    verdict.json
    evaluation.json
    sections/
    run_stats.json
    run_log.md              the readable audit log (§23.4)
    log/                    one markdown log per agent, written by the agent

  reports/latest -> <run>/

  research/                 the question ledger (§14)
    questions.json

  .state.json
  .lock

data/_MACRO/
  sources/
    00_manifest.md
  structured/
  .state.json
```

`<run>` is:

```text
YYYY-MM-DD
YYYY-MM-DD_2
YYYY-MM-DD_3
...
```

`data/` is gitignored by default. Users may opt in.

### **Layer definitions**

**Bronze** contains evidence that is either:

- fetched from an identifiable source, or
- deterministically reproducible from bronze inputs using version-controlled code.

Bronze is citable.

**Silver** contains non-citable working derivations, synthesis, selection artifacts, and audit records.

Silver may be produced by either deterministic code or model judgment; what makes an artifact silver is that it exists to support later work rather than to serve as report evidence. Everything under `derived/` and `wiki/` is silver and is never a citation target.

**Gold** contains report-facing outputs and generated exhibits.

`wiki/` remains a top-level sibling of `derived/` because it is the human-facing knowledge surface and is referenced directly throughout the project.

### **4.1 Reproducible computed evidence**

Some `structured/` artifacts are computed:

```text
technical_indicators_computed
key_ratios_computed
```

These remain bronze when they satisfy all of the following:

- `source: "computed"`,
- non-empty `derived_from`,
- no `url`,
- deterministic code produces them from bronze inputs.

A citation to:

```text
[^technical_indicators_computed]
```

is valid because the result is reproducible from evidence using version-controlled code.

Model judgment is not reproducible in this sense. For example:

```text
derived/peers/peers_ranked.json
derived/peers/peers_selected.json
```

are silver: their content depends on a subagent’s ranking, so re-running does not reproduce them.

### **4.2 Silver working artifacts**

`derived/peers/` contains the peer-selection working set and audit trail.

These files are not evidence and are never citation targets.

Some are produced deterministically:

```text
peers_user.json
peers_fmp.json
peers_funds.json
peers_proxy.json
peers_candidates.json
```

Others contain model judgment:

```text
peers_ranked.json
peers_selected.json
```

All remain silver because they exist to support selection rather than to serve as report evidence. Location, not producer shape, sets the layer: a deterministically computed artifact written under `derived/` is silver and non-citable, while the same shape under `structured/` is bronze (§4.1).

They are documented durable artifacts. They may be regenerated, but they are not treated as disposable scratch space, because `peers_ranked.json` is the audit record of why a peer set was chosen and `peers_selected.json`’s lineage must not evaporate.

Every durable `derived_from` id must resolve.

A `.state.json` entry naming a missing artifact is stale by definition (§10.1), so a deleted working artifact is refetched rather than silently ignored.

------

## **5.** **`sources/`** **— Bronze Text Documents**

All textual evidence lands in `sources/` as Markdown.

Filename format:

```text
<YYYY-MM-DD>_<kind>[_<topic>][_<n>].md
```

If a same-day id already exists, `make_source_id` appends the smallest available suffix:

```text
_2
_3
...
```

Example:

```yaml
---
id: 2026-05-21_sec_10q
ticker: PANW
kind: sec_filing
source: SEC EDGAR
url: https://www.sec.gov/Archives/...
fetched_at: 2026-05-21T14:22:03Z
as_of: 2026-04-30
title: "PANW Q3 FY26 10-Q"
fetch_tool: lib/fetchers/edgar.py
fetch_cmd: uv run python sra.py prefetch PANW --kinds filings
supersedes: 2026-02-20_sec_10q
cited_urls:
  - https://ir.zscaler.com/...
---
<body copied from the source and converted to markdown>
```

Required fields are marked by their presence in the schema above. `supersedes` and `cited_urls` are optional.

`url` is where the bytes came from. For a document fetched as a page, it is that document's own URL.

**For an API call, record the request itself.** `url` carries the endpoint with its query string removed, and `request` carries the parameters that produced this artifact:

```yaml
url: https://financialmodelingprep.com/stable/income-statement
request:
  method: GET
  params:
    symbol: PANW
    period: quarter
    limit: 1
```

Every parameter that affects the response is recorded verbatim, including defaults the caller passed explicitly. A request body, where one exists, is recorded the same way under `request.body`.

Credential parameters — `apikey`, `api_key`, `token` and their equivalents — are omitted entirely: not blanked, not masked, not placeholdered. §8.4 rejects any artifact carrying one, and a masked value still discloses the parameter's presence and length.

`request` is required whenever an artifact came from an API call and omitted for a plain page fetch. A provider-only identifier is not a substitute for the endpoint, and a hand-picked nearest page is not a substitute for the parameters: only the recorded request says what was actually asked.

`as_of` is the date the content speaks to: period end, article date, or quote date.

`lib/provenance.py` defines two disjoint kind sets.

### **`BRONZE_KINDS`**

Valid under `sources/`:

```text
sec_filing
wikipedia
news
perplexity_research
transcript
web_page
press_release
analyst_note
other
```

### **`MODEL_KINDS`**

Valid for researcher answers:

```text
research_answer
```

A researcher answer is written under:

```text
derived/answers/
```

with `kind: research_answer`.

`write_source` must reject any kind in `MODEL_KINDS`.

`validate` must fail if a model kind appears under `sources/`.

### **Immutability and archiving**

Files under `sources/` are immutable. A refresh never rewrites a file: it writes a new one carrying `supersedes`.

`write_source` raises `FileExistsError` on overwrite.

When a new file supersedes an existing source, the superseded file moves to:

```text
sources/archive/<id>_<YYYY-MM-DD>.md
```

The appended date is the date the file was superseded, not the date it was fetched — the fetch date already opens the id. The archived name therefore records the interval over which that copy was the current one.

Archiving is a move, not an edit. Content and frontmatter are untouched, so an archived file is byte-identical to the file that was current.

**The `id` does not change.** It stays as written in frontmatter, and a resolver recovers it from an archived filename by stripping the `_<YYYY-MM-DD>` suffix. `sources/` therefore holds exactly the current evidence, and `sources/archive/` holds every prior version of it.

**Ids are unique across both directories.** `make_source_id` picks its `_<n>` suffix (§5) against `sources/` and `sources/archive/` together. Without that, archiving a same-day id would free the name for reuse and two different documents would answer to one citation key.

**Citations resolve into the archive.** `[^id]` resolves whether the file is current or archived (§8.4). This is the reason superseded evidence is archived rather than deleted: a report published in May cited the 10-Q that was current in May, and that citation must still land on the document the writer actually read.

**Retrieval sees current evidence only.** `00_manifest.md` and `grep` cover `sources/` and exclude `sources/archive/`, so a researcher reads today's evidence rather than four versions of it. `grep --include-archived` widens the search when the history is the question. `show TICKER ID` resolves an archived id with no flag, because a citation must always be inspectable.

`.state.json` records the current evidence ids for each data kind.

### **`supersedes`**

`supersedes` means replacement of the same evidence item.

Examples:

- refreshed Wikipedia page,
- amended filing,
- refreshed copy of the same news roundup.

It does **not** mean temporal succession.

A later-quarter 10-Q or transcript does not supersede the prior period. Historical periods remain valid evidence.

Temporal succession is expressed through `as_of`.

This distinction controls invalidation:

- replacements trigger dependency invalidation,
- new periods trigger subscription invalidation (§10.3).

### **Aggregator sources**

An **aggregator** is a fetched document that reports on other documents rather than originating its own claims: a Perplexity research output, a news roundup, a Wikipedia article, an analyst note quoting other research.

An aggregator is bronze. A third party wrote it, it was fetched from a URL, and it is immutable and citable like any other source. What distinguishes it is that its claims came from somewhere else, so citing the aggregator leaves one derivative hop between the report and the evidence.

Typically aggregators:

```text
news
wikipedia
perplexity_research
analyst_note
```

Typically primary — the subject or the record speaking for itself:

```text
sec_filing
transcript
press_release
```

`web_page` and `other` are either, judged per document.

An aggregator is not the same thing as a research answer. The aggregator was written by a third party and is bronze; a research answer is SRA6’s own model output, is silver, and is never a citation target (§1.2). The line is who wrote it, not how derivative it is.

Aggregator documents must therefore preserve their own citations:

- in `cited_urls`,
- and inline in the document body.

`sra.py fetch-urls` converts those URLs into bronze evidence (§8.3) so a claim can cite its origin rather than the roundup that repeated it. Where the aggregator is itself the only support a claim has, it is cited as such (§8.2).

### **5.1** **`sources/00_manifest.md`**

Generated by:

```text
sra.py manifest
```

Never hand-edited.

Format:

```text
| id | kind | as_of | bytes | one-line contents |
```

One line per current source. Archived versions are excluded: the manifest is a catalog of what is true now, and listing every superseded copy would grow the researcher's entry point without adding evidence.

This is the primary entry point for document retrieval (§9).

------

## **6.** **`structured/`** **— Bronze JSON**

Every artifact has the shape:

```json
{
  "_meta": {
    "id": "balance_sheet_yahoo",
    "ticker": "PANW",
    "producer": "fetch",
    "source": "Yahoo Finance",
    "url": "https://finance.yahoo.com/quote/PANW/balance-sheet",
    "request": {
      "method": "GET",
      "params": {"symbol": "PANW", "freq": "quarterly"}
    },
    "provider_tool": "yfinance.Ticker.balance_sheet",
    "fetch_cmd": "uv run python sra.py prefetch PANW --kinds financials",
    "fetched_at": "2026-07-30T14:20:11Z",
    "as_of": "2026-04-30",
    "period": "quarterly",
    "derived_from": []
  },
  "data": {}
}
```

### **6.1 Provenance fields**

Four fields represent distinct facts.

| **Field**       | **Meaning**                                                  | **Example**                                             |
| --------------- | ------------------------------------------------------------ | ------------------------------------------------------- |
| `fetch_cmd`     | Command that regenerates the artifact from current provider data | `uv run python sra.py prefetch PANW --kinds financials` |
| `provider_tool` | Code or provider method that performed the operation         | `yfinance.Ticker.balance_sheet`                         |
| `url`           | Endpoint or page the bytes came from, query string stripped  | `https://financialmodelingprep.com/stable/income-statement` |
| `request`       | The parameters actually sent, credentials omitted (§5)       | `{"method": "GET", "params": {"symbol": "PANW", "period": "quarter"}}` |

`fetch_cmd` is required on all bronze artifacts, including computed artifacts.

For computed artifacts, it is the command that reruns the derivation.

`fetch_cmd` does not guarantee byte-identical reproduction. Providers, pages, and library versions may change. It guarantees regeneration of the same artifact type and shape using current source data.

Byte-level reproduction is out of scope (§28.6).

### **6.2 Producer shapes**

#### **`fetch`**

Layer: bronze.

Required:

```text
id
ticker
title
source
url
fetched_at
as_of
provider_tool
fetch_cmd
```

`fetch` always carries a `url`, and carries `request` whenever the artifact came from an API call rather than a page fetch (§5). A provider-only identifier is not a substitute for either.

#### **`compute`**

Layer: bronze under `structured/`; silver and non-citable when written under `derived/` (§4.2).

Required:

```text
id
ticker
title
source
derived_from
computed_at
as_of
provider_tool
fetch_cmd
```

`derived_from` must be non-empty.

`url` must be omitted.

#### **`model`**

Layer: silver.

Required:

```text
id
ticker
title
source          (the agent type)
derived_from
generated_at
as_of
```

`derived_from` must be non-empty.

`url` and `fetch_cmd` must be omitted.

Model-shape JSON is written through `write_derived`, not `write_structured`.

The producer contract is validated over the full tree (§8.4).

### **6.3 Source separation**

Facts from different providers remain separate.

Example:

```text
key_facts_yahoo.json
key_facts_fmp.json
```

There is no blended canonical view and no general precedence resolver.

A consumer chooses one source for each figure.

If providers disagree materially, the writer surfaces the disagreement rather than averaging it.

Computed evidence lists source ids in `derived_from`.

All citation ids share one namespace within a ticker.

The flat JSON-plus-`_meta` shape is the node payload of the future knowledge graph: nothing may assume a JOIN across files except through ids.

### **6.4 Minimum financial-data semantics**

SRA6 does not implement a general financial canonicalization layer.

It must enforce these rules:

- **No cross-provider arithmetic.**A single computed figure must use one provider’s artifacts.
- **Fiscal periods come from the subject profile.**
  - Never assume calendar fiscal periods.
- `_meta.period` is one of:
  - `quarterly`,
  - `annual`,
  - `ttm`.
- `as_of` is the period end.
- Prices are stored using the provider’s adjustment convention.
- `_meta.adjusted` records whether prices are split/dividend adjusted.
- Per-share figures must not mix adjustment conventions.
- The driver must not construct TTM from four quarters.
- TTM is used only when supplied by the provider.
- `_meta.currency` records reporting currency.
- No FX conversion is performed.
- Non-USD reporting currency must be disclosed in the report.
- Null values remain null.
- Missing values are never zero-filled.
- Charts must disclose gaps rather than interpolate them.

Material provider disagreements are analytical findings, not driver errors.

------

## **7.** **`.state.json`**

Example:

```json
{
  "ticker": "PANW",
  "company_name": "Palo Alto Networks, Inc.",
  "created_at": "...",
  "last_build": "...",
  "last_update": "...",
  "peers_asked_at": "2026-07-30T13:00:00Z",

  "data": {
    "prices": {
      "current_ids": ["prices_yahoo"],
      "fetched_at": "...",
      "policy_days": 1
    },
    "news": {
      "current_ids": ["2026-07-30_news_yahoo"],
      "fetched_at": "...",
      "policy_days": 5
    },
    "financials": {
      "current_ids": [
        "income_statement_yahoo",
        "balance_sheet_yahoo",
        "cashflow_yahoo",
        "key_ratios_computed"
      ],
      "fetched_at": "...",
      "policy": "on_earnings"
    },
    "filings": {
      "current_ids": [
        "2026-05-21_sec_10q",
        "sec_financials_edgar"
      ],
      "fetched_at": "...",
      "policy": "on_new_filing"
    },
    "profile": {
      "current_ids": ["profile_yahoo"],
      "fetched_at": "...",
      "policy_days": 90
    },
    "estimates": {
      "current_ids": [
        "estimates_yahoo",
        "eps_revisions_yahoo"
      ],
      "fetched_at": "...",
      "policy_days": 7
    },
    "peers_candidates": {
      "current_ids": ["peers_candidates"],
      "fetched_at": "...",
      "policy_days": 90
    }
  },

  "derived": {
    "peers_selected": {
      "current_id": "peers_selected",
      "updated_at": "...",
      "user_peers": ["CRWD", "FTNT"],
      "derived_from": [
        {
          "id": "peers_ranked",
          "generated_at": "..."
        },
        {
          "id": "peers_candidates",
          "fetched_at": "..."
        }
      ]
    }
  },

  "wiki": {
    "competitive": {
      "updated_at": "...",
      "built_from": [
        {
          "id": "...",
          "fetched_at": "..."
        }
      ]
    }
  },

  "report": {
    "last_generated": "2026-07-30",
    "sections_dirty": []
  }
}
```

### **Rules**

`current_ids` is always stored as a list.

A data kind may produce one or many bronze artifacts.

`record_fetch` accepts either:

```text
current_id: str
```

or:

```text
current_ids: list[str]
```

and normalizes the value to a list in state.

`data{}` holds fetch lifecycle state — what `prefetch` gathers and `status` ages. It is bronze except for one entry: `peers_candidates`, because the peer gather runs as a prefetch kind under a 90-day policy (§11.1) even though its artifacts are silver.

Lifecycle state for model-produced silver belongs under `derived{}`.

`derived.peers_selected.user_peers` persists the user’s pinned peer list, so a later refresh can reconstruct what was pinned without re-reading the gather artifacts (§13.5).

### **Freshness policies**

`on_earnings` fires when the most recent past earnings date is newer than the artifact’s `fetched_at`.

The date comes from:

```text
events_calendar_yahoo.json
```

through:

```text
lib.fetchers.calendar.last_earnings_date
```

The policy uses the most recent past event, not the next scheduled earnings date.

`on_new_filing` currently falls back to:

```text
EVENT_POLICY_FALLBACK_DAYS = 7
```

It does not yet consult the SEC filing index (§28.4).

`sections_dirty` accumulates changed report sections until regeneration consumes them.

### **Derivation stamps**

Where a silver artifact records inputs, use stamped references:

```json
{"id": "...", "fetched_at": "..."}
```

or the relevant producer timestamp, such as:

```json
{"id": "...", "generated_at": "..."}
```

This applies to:

- `wiki.built_from`,
- `derived.*.derived_from`,
- `answer_source_ids`.

Sources are immutable, but structured bronze ids are overwritten in place. The timestamp allows `invalidate` to detect a refetch.

### **7.1 Concurrency and crash consistency**

Every mutating command acquires:

```text
data/<T>/.lock
```

The lock:

- is created using O_EXCL semantics,
- records PID and command,
- causes a second mutating process for the same ticker to fail immediately,
- may be broken with `--force-lock` if older than 6 hours.

State is committed **after each fetcher returns**.

This applies whether the fetcher:

- succeeds,
- succeeds with warnings,
- fails cleanly,
- raises an exception that the driver converts into a fetcher error.

Saving after each fetcher prevents a crash from leaving completed artifacts unrecorded in state.

Multi-file transactions are not provided.

Recovery is:

```text
rerun the phase
```

not rollback.

All phases must therefore be idempotent.

------

# **Part III — Provenance**

## **8. Provenance System**

### **8.1 Derivation and citation are different relations**

| **Relation**                  | **Meaning**                        |
| ----------------------------- | ---------------------------------- |
| `derived_from` / `built_from` | dependency relation between layers |
| `[^id]`                       | evidence citation                  |

Citation rule:

Every citation id must resolve to bronze evidence.

Silver artifacts are never citation targets.

A report may depend on a wiki page, but it may not cite the wiki page as evidence.

A researcher may read an earlier answer, but any inherited claim must be cited back to bronze evidence.

### **8.2 Citation flow**

```text
fetch
  → sources/<id>.md or structured/<id>.json

research
  → derived/answers/<answer>.md with cited_urls

harvest
  → fetch-urls downloads those URLs into sources/

synthesize
  → wiki claim [^bronze-id]

write
  → section claim [^bronze-id]

assemble
  → [^bronze-id] becomes [^1]
  → references.md
  → citation_map.json
```

Example reference:

```text
[1] PANW Q3 FY26 10-Q — SEC EDGAR, https://www.sec.gov/... — fetched 2026-05-21
```

Example map:

```json
{
  "1": "2026-05-21_sec_10q"
}
```

For an aggregator source, the reference entry prefers the true origin harvested from `cited_urls`; where the aggregator document is itself the claim’s only support, it is cited as such ("Perplexity research, fetched \<date\>").

Section drafts retain bronze ids directly.

Assembled reports use numeric citations.

Gold validation therefore checks:

1. every numeric report citation exists in `citation_map.json`,
2. every mapped id resolves to bronze.

A citation that fails to resolve is a build defect.

The validator cannot detect an assertion that has no citation. §22.2 defines the advisory controls for citation coverage.

Internal artifact names must never appear in report prose.

Examples prohibited by hard check:

```text
key_facts.json
manifest
data/PANW/...
structured/...
```

### **8.3** **`fetch-urls`**

Command:

```text
sra.py fetch-urls TICKER [--from ANSWER_ID] [--max N]
```

Without `--from`, process all answer files with unharvested `cited_urls`.

For each URL:

1. fetch it,
2. convert it to Markdown,
3. write:

```text
sources/<date>_web_page_<slug>.md
```

1. record:
   - `kind: web_page`,
   - original URL,
   - site name,
   - `fetch_tool: lib/fetchers/urls.py`,
   - reproduction command.

### **URL freshness**

If the URL already exists in bronze and was fetched within:

```text
WEB_PAGE_POLICY_DAYS = 30
```

reuse the existing source id.

If older than 30 days:

- refetch,
- create a new immutable source,
- set `supersedes` to the previous copy.

### **URL-to-id map**

For each answer, write:

```text
derived/answers/<answer-id>.urls.json
```

Format:

```json
{
  "https://example.com/a": "2026-08-10_web_page_example",
  "https://example.com/b": null
}
```

`null` means the fetch failed.

The synthesizer converts answer-level URL citations to bronze ids using this map.

If the supporting URL could not be fetched, the claim is not citable and must be:

- dropped,
- or supported from another source.

A failed target fetch is a warning, not a command failure.

Output:

```json
{
  "fetched": [],
  "skipped": [],
  "errors": {}
}
```

Exit 0 unless the answer file itself cannot be read.

### **Evidence without a URL**

A live MCP response is not citable by itself.

If a live tool provides useful evidence:

1. use a registered fetcher to persist it in bronze, or
2. omit the claim.

There is no third path.

### **8.3.1 SSRF controls**

`fetch-urls` is the only component that fetches model-selected URLs.

`lib/fetchers/urls.py` must enforce:

- only `https` and `http`,
- resolved-IP denial for:
  - loopback,
  - link-local,
  - private ranges,
  - multicast,
  - unspecified addresses,
  - cloud metadata addresses including `169.254.169.254`,
- DNS resolution before connection,
- redirect validation at every hop,
- maximum 3 redirects,
- response cap of 5 MB,
- 20-second timeout,
- MIME allowlist:
  - `text/html`,
  - `text/plain`,
  - `application/pdf`,
  - `application/xhtml+xml`,
- Markdown output truncated at 200k characters,
- truncation recorded in source frontmatter.

### **8.4** **`validate`**

Command:

```text
sra.py validate TICKER
```

Deterministic. Model-free. Fatal.

Checks:

1. **Producer contract**every JSON artifact conforms to its producer shape.

2. **`fetch_cmd`**

   - required on bronze,
   - forbidden on `model` artifacts.

3. **Layer boundary**

   - no model-written kinds in `sources/`,
   - no model-shape JSON in `structured/`.

4. **Citation resolution**

   - every citation in wiki pages and report section drafts resolves to ticker bronze or `_MACRO` bronze, current or archived (§5),
   - every numeric citation in the assembled report maps through `citation_map.json` to bronze.

5. **Derivation resolution**

   - every `derived_from` and `built_from` id resolves to an existing durable artifact.

6. **Secret scanning**

   

   

   - reject provider API-key values,
   - reject known provider key patterns,
   - reject non-empty query parameters named:
     - `apikey`,
     - `api_key`,
     - `token`.

   Patterns include:

   - FMP 32-hex,
   - OpenAI `sk-`,
   - FRED 32-lowercase-hex.

   The scan covers every artifact, log line, and answer file, including recorded `request.params` (§5), which is the one field designed to hold provider call parameters and therefore the likeliest place a key lands by accident. A credential parameter must be absent, not blanked.

   Pattern matching is what catches a rotated key the current environment no longer holds. Redaction itself happens at the fetch boundary (§11.1, §12.1); `validate` is the backstop, not the mechanism.

7. **Path containment**

   - every artifact path resolves inside the intended ticker directory,
   - ticker must match:



```regex
^[A-Z][A-Z0-9.-]{0,9}$
```

- 1. `_MACRO` is the one reserved directory name exempt from the ticker pattern (§12). No other leading-underscore name is accepted.

- 1. topics and slugs are reduced to:

```regex
[a-z0-9-]
```

Exit 1 on any validation error.

There is no `--force`.

Run `validate` at the bronze, silver, and gold gates.

`wiki-lint` remains separate because it is advisory (§22.1).

------

# **Part IV — Retrieval and Freshness**

## **9. Retrieval: Manifest + Grep**

Researchers retrieve bronze evidence through progressive disclosure.

1. Read:

```text
sources/00_manifest.md
```

1. Use:

```text
sra.py grep
```

1. or:

```text
sra.py show
```

Commands:

```text
sra.py manifest TICKER
sra.py show TICKER ID
sra.py grep TICKER PATTERN [--kinds a,b] [--context N] [--top-k K] [--include-archived]
```

`grep` searches current sources. `--include-archived` extends it to `sources/archive/` (§5). `show` resolves any id, current or archived, without a flag.

`show` requires the ticker because structured ids such as:

```text
prices_yahoo
profile_yahoo
```

are reused across tickers.

Use `_MACRO` as the ticker for shared macro evidence.

`grep` returns:

```text
source_id
kind
as_of
url
title
excerpt
matched_terms
```

Ranking is deterministic:

1. number of distinct matched terms descending,
2. `as_of` descending.

### **9.1 No vector store**

**SRA6 has no vector store.** Retrieval is the manifest-plus-grep path above and nothing else. No embeddings are computed, no similarity search runs anywhere in the pipeline, and no component of this specification depends on one existing.

Earlier SRA6 work carried a LanceDB index over bronze. It is removed, together with:

- embedding dependency,
- tagger,
- chunk schema,
- indexed-id queue,
- index staleness tracking.

`data/<T>/index/`, the `ingest`, `search` and `apply-tags` commands, the `sra-ingest` skill, the `sra-tagger` agent, and the `lancedb` / `pyarrow` / `tiktoken` dependencies go with it (§19). §27.7 records the reasoning.

§9.2’s re-entry condition states the measured evidence that would justify revisiting this decision. It is a falsification test for the choice, not a roadmap item: nothing is planned, and nothing should be designed in anticipation of a vector store returning.

### **9.2 Retrieval evaluation**

Answered research questions create retrieval test cases.

The question ledger provides:

```text
(question, relevant bronze ids)
```

`eval-retrieval` evaluates document retrieval only.

Structured-artifact evidence is excluded from the gold set because document grep cannot retrieve it.

Archived sources are excluded for the same reason: `grep` searches current evidence by default (§5), so a question whose accepted evidence has since been superseded is not scored as a miss.

The evaluation is selection-biased because accepted evidence reflects what prior retrieval surfaced. It is primarily a regression test, not an absolute retrieval-quality benchmark.

Command:

```text
sra.py eval-retrieval TICKER [--k 10] [--baseline PATH]
```

For each answered question:

1. remove stopwords,
2. derive query terms,
3. run the deterministic manifest/grep path,
4. calculate:

```text
recall@k = |returned ∩ gold| / |gold|
```

### **Gates**

Ship gate:

```text
mean recall@10 ≥ 0.70
```

CI baseline:

```text
tests/fixtures/retrieval_baseline.json
```

CI fails if mean recall drops by more than 0.02 from the recorded baseline.

Use `k=10`.

### **Vector-store re-entry**

Reconsider vector retrieval if both conditions hold:

- bronze exceeds ~2MB per ticker,
- measured `recall@10 < 0.60`.

------

## **10. Freshness**

Two commands answer different questions.

| **Command**  | **Purpose**                                             |
| ------------ | ------------------------------------------------------- |
| `status`     | Which bronze data must be refreshed?                    |
| `invalidate` | Which silver knowledge is stale because bronze changed? |

### **10.1** **`status`**

```text
sra.py status TICKER
```

Output:

```text
ticker
stale
sections_dirty
data
```

`stale` is a list of data kinds.

A kind is stale when:

- its time policy expires,
- its event policy fires,
- any id in `current_ids` is missing from disk.

Refetch:

```text
sra.py prefetch TICKER --stale-only
```

### **10.2 Dependency invalidation**

`invalidate` identifies consumers of replaced evidence.

Example:

```text
sra.py status PANW
sra.py prefetch PANW --stale-only
sra.py invalidate PANW
sra.py invalidate PANW --apply
```

Dry-run output should distinguish:

```text
new bronze
reopened questions
dependency cause
subscription cause
dirty wiki pages
dirty report sections
```

A source is replaced if a current source declares the old source in `supersedes`.

A structured artifact is replaced when its current producer timestamp is newer than the timestamp stored in the consumer’s derivation stamp.

Affected questions are reopened.

Affected wiki pages are marked dirty.

### **10.3 Subscription invalidation**

New-period evidence may supersede nothing.

Examples:

- new 10-Q,
- new transcript,
- new competitor filing,
- new news cycle.

Each report section therefore declares subscribed data kinds in `sections.yaml`.

Example:

```yaml
valuation:
  subscribes_to:
    - financials
    - estimates
    - targets
    - filings
    - transcript
```

A question is reopened when:

- its cited evidence was replaced, or
- a newly arrived bronze artifact belongs to one of its section’s subscribed kinds and is newer than `answered_at`.

`invalidate` is dry-run by default.

Mutation requires:

```text
--apply
```

------

# **Part V — Pipelines**

## **11. Prefetch**

Prefetch has two distinct stages:

1. deterministic gather,
2. model-based web research.

### **11.1 Deterministic gather**

```text
sra.py prefetch TICKER [--kinds a,b] [--stale-only] [--peers "AAA,BBB"]
```

Default kinds:

```text
profile
prices
financials
estimates
targets
calendar
peers
filings
transcript
news
technical
wikipedia
```

`perplexity` is opt-in.

### **Dependency waves**

#### **Wave 1**

```text
profile
prices
financials
estimates
targets
calendar
peers
filings
transcript
news
```

#### **Wave 2**

```text
technical
wikipedia
```

Dependencies:

```text
technical → prices
wikipedia → profile
```

Each fetcher declares:

```python
DEPENDS_ON: tuple[str, ...]
```

The driver topologically sorts dependency edges.

`tests/test_registry.py` pins the required edges.

Fetchers run as in-process futures.

Only the main thread mutates shared state.

### **Outputs**

| **Kind**     | **Output**                                                   |
| ------------ | ------------------------------------------------------------ |
| `profile`    | `structured/profile_yahoo.json`; sets `company_name`; 90d    |
| `prices`     | `structured/prices_yahoo.json`; 1d                           |
| `technical`  | `structured/technical_indicators_computed.json`; price-chart candidate |
| `financials` | income statement, balance sheet, cash flow, computed key ratios; `on_earnings` |
| `estimates`  | estimates + EPS revisions; 7d                                |
| `targets`    | price targets + recommendations                              |
| `calendar`   | `events_calendar_yahoo.json`                                 |
| `peers`      | the §13 gather → `derived/peers/`; records state kind `peers_candidates`; 90d |
| `filings`    | source filings + `structured/sec_financials_edgar.json`; `on_new_filing` |
| `transcript` | transcript source; `on_earnings`                             |
| `wikipedia`  | Wikipedia source; 90d                                        |
| `news`       | Yahoo news source; 5d                                        |

### **Fetcher contract**

```python
fetch_x(
    ticker,
    ticker_dir,
    state,
    **kw
) -> tuple[bool, list[Path], str | None]
```

`kw` supports injectable providers and `now=` for tests.

Interpretation:

```text
(True, paths, None)   success
(True, paths, err)    success with warning
(False, paths, err)   failure
```

Uncaught exceptions become:

```text
errors[kind] = "<kind> crashed: <exc>"
```

One fetcher failure does not discard prior successful work.

**State is committed after each fetcher returns.**

Prefetch output:

```json
{
  "fetched": [],
  "skipped": [],
  "errors": {},
  "warnings": {}
}
```

Exit 2 if `errors` is non-empty.

### **Minimum viable input**

Fatal kinds:

```text
profile
prices
financials
```

Failure of any fatal kind stops `/sra-build`.

All other kinds are degradable.

A degraded build:

- continues,
- records missing kinds in `run_stats.json`,
- states the gap in the report methodology note,
- omits unavailable charts,
- limits claims in affected sections.

### **API-key handling**

FMP calls go through:

```text
lib/fmp_http.py:fmp_get
```

The helper appends credentials.

No exception, warning, log line, or artifact may contain a raw provider key.

The same rule applies to FRED.

### **11.2 Prefetch web research**

Prefetch launches the harness-provided `deep-research` Workflow for seven topics:

```text
news
business profile
executives
business model
competitive
risk
thesis
```

Prompt files:

```text
prompts/prefetch_research/<topic>.md
```

Invocation:

```javascript
Workflow({
  name: "deep-research",
  args: <topic prompt + ticker context>
})
```

Fallback:

```text
one sra-researcher subagent per topic
```

Results are written to:

```text
derived/answers/<date>_prefetch_<topic>.md
```

with `cited_urls`.

The driver then runs:

```text
sra.py fetch-urls
```

The research answer itself is never evidence.

Perplexity remains an optional supplement.

------

## **12. Macro Data**

Shared macro data lives under:

```text
data/_MACRO/
```

Examples:

- FRED 10Y series,
- multpl CAPE.

Command:

```text
sra.py prefetch-macro [--series a,b] [--stale-only]
```

Ticker citation resolution checks:

1. ticker bronze,
2. `_MACRO` bronze.

Charts read only persisted macro artifacts.

They must not fetch macro data during rendering.

### **12.1 FRED**

Use:

```text
api.stlouisfed.org/fred/series/observations
api.stlouisfed.org/fred/series
```

with `FRED_API_KEY`.

Store metadata including:

```text
title
units
frequency_short
seasonal_adjustment
last_updated
realtime_start
realtime_end
```

### **Frequency policy**

| **Frequency** | `policy_days` |
| ------------- | ------------- |
| `D`           | 2             |
| `W`           | 9             |
| `M`           | 40            |
| `Q`           | 100           |
| `A`           | 400           |

Unknown frequency:

```text
policy_days = 40
```

and emit a warning.

Store the current vintage plus:

```text
realtime_start
realtime_end
last_updated
```

Report snapshots preserve rendered report content, but structured bronze artifacts remain mutable by id.

An old report is therefore readable and auditable, but not necessarily re-derivable from current structured artifacts.

Versioned structured evidence is future work (§28.6).

### **12.2 multpl**

Use:

```text
lxml
pandas.read_html
```

Initial series:

```text
sp500_pe
shiller_pe_cape
sp500_dividend_yield
sp500_earnings_yield
sp500_price_real
```

Freshness:

```text
policy_days = 30
```

Each scraper must validate:

- expected columns,
- expected data types,
- monotonic date index,
- plausible value range.

Markup changes must fail loudly.

### **12.3 Dependencies**

No additional package dependencies are required.

A failed macro series is a warning.

------

## **13. Peer Selection**

Pipeline:

```text
gather candidate evidence
→ model ranks candidates using rubric
→ deterministic pin-and-fill selects five
```

There is no weighted score or arithmetic composite.

### **13.1 Problem**

FMP’s stock-peer endpoint can return sector or market-cap neighbors that are poor business comparables.

For CRWD it returned:

```text
ACN
ADBE
ADI
CRWV
FTNT
KLAC
NET
PANW
```

Only a subset are strong operating comparables.

SRA6 therefore gathers several signals before model selection.

### **13.2 Candidate sources**

| **Source**                 | **Mechanism**                   | **Use**                   |
| -------------------------- | ------------------------------- | ------------------------- |
| FMP stock peers            | `/stable/stock-peers`           | one mechanical signal     |
| Top-5 fund overlap         | equity exposure + fund holdings | industry overlap          |
| DEF 14A peer-group excerpt | latest proxy                    | size/growth comparability |
| User-provided peers        | `--peers`                       | pinned judgment           |

The subject ticker appears as:

```json
{"is_subject": true}
```

for comparison but can never be selected.

### **Fund filtering**

From `etf_equity_exposure`:

1. remove null weights,
2. remove subject weights >25%,
3. remove ETF symbols containing `.` or a space,
4. require:

```text
20 ≤ holdingsCount ≤ 150
```

1. take the top 5 by subject weight,
2. load each fund’s top 50 holdings,
3. record `fund_count` for each candidate.

An empty ETF-info result is a source failure.

### **Proxy source**

Do not attempt deterministic company-name extraction from DEF 14A peer-group prose.

Instead store merged windows of approximately ±4,000 characters around literal occurrences of:

```text
peer group
```

The model receives this excerpt as contextual evidence when ranking candidates.

### **13.3 Pipeline**

Commands:

```text
sra.py peers-candidates TICKER [--peers "AAA,BBB"] [--top-funds 5]

/sra-peers TICKER

sra.py peers-select TICKER [--ranked-file PATH]
```

Outputs:

```text
derived/peers/peers_user.json
derived/peers/peers_fmp.json
derived/peers/peers_funds.json
derived/peers/peers_proxy.json
derived/peers/peers_candidates.json
derived/peers/peers_ranked.json
derived/peers/peers_selected.json
```

All are silver and non-citable.

The first five are deterministic candidate-selection artifacts.

`peers_ranked.json` and `peers_selected.json` contain model-mediated judgment.

`peers-candidates` records state kind `peers_candidates`, never `peers`.

### **Candidate gathering**

#### **1. Gather all four sources**

Record all source memberships.

A candidate named by both the user and FMP must retain both signals:

```json
"sources": ["user", "fmp_peers"]
```

#### **2. Apply overlap filter**

Keep a candidate when:

```text
fund_count >= 2
OR in_fmp_peers
OR in_user_peers
```

#### **3. Fetch candidate profiles**

Use batched:

```text
/stable/profile
```

Capture:

```text
sector
industry
country
exchange
marketCap
description
isEtf
isFund
isAdr
isActivelyTrading
cik
```

#### **4. Apply hygiene filter**

Drop:

- ETFs,
- funds,
- inactive companies.

The subject row is exempt.

#### **5. Add revenue**

Fetch one current income statement per surviving candidate.

Record:

```text
revenue_ttm
```

A failure produces:

```json
"revenue_ttm": null
```

#### **6. Write** **`peers_candidates.json`**

Also record:

```text
candidates_changed_at
```

for staleness checks.

### **Taxonomy**

Use FMP’s taxonomy.

Column names:

```text
fmp_sector
fmp_industry
```

Never label these fields GICS.

### **Model ranking**

`/sra-peers` dispatches one `sra-rater` subagent.

It receives:

- the complete candidate table,
- candidate metrics,
- descriptions,
- source signals,
- fund overlap,
- market capitalization,
- revenue,
- FMP sector and industry,
- proxy peer-group excerpt.

The rater applies a written rubric rather than a numeric weighted score.

The rubric should judge:

- similarity of business model,
- product and customer overlap,
- competitive substitutability,
- end-market similarity,
- scale,
- growth profile,
- revenue profile,
- company description,
- mechanical-source agreement,
- proxy peer-group context.

These signals guide judgment. They are not converted into a weighted composite.

Output:

```text
derived/peers/peers_ranked.json
```

Shape:

```json
[
  {
    "symbol": "FTNT",
    "rank": 1,
    "rationale": "..."
  }
]
```

The ranking is ordered.

A ranked symbol may come from the DEF 14A excerpt even if it was not present in the mechanical candidate table. Such a row may contain only:

```text
symbol
rank
rationale
```

### **Deterministic selection**

`peers-select`:

1. pins user-provided peers first, in user order,
2. fills remaining slots from `peers_ranked.json` in rank order,
3. stops at:

```text
PEER_SET_SIZE = 5
```

There is no weighted formula.

Output:

```text
derived/peers/peers_selected.json
```

It stores:

- final five peers,
- rank,
- rationale,
- non-selected runners-up,
- user-provided peers.

### **13.4 Top-up behavior**

| **User peers** | **Rater** | **Result**                                      |
| -------------- | --------- | ----------------------------------------------- |
| ≥5             | skipped   | first five user peers                           |
| <5             | runs      | user peers + highest-ranked unpinned candidates |

If the user supplies more than five peers:

- first five are selected,
- extras remain recorded as runners-up with:

```text
origin: user_provided
```

Pinned peers are excluded from the rater’s ranking task because their slots are already fixed.

`TARGET_PEERS = 8` is retired.

`/sra-build` asks for peers once at step 0.

`/sra-update` does not re-ask.

Persisted user peers may be changed explicitly through the peer-prefetch path.

### **13.5 Staleness and failures**

`peers-select` exits 1 when `derived/peers/peers_ranked.json` has a `_meta.generated_at` predating `candidates_changed_at` — it ranked an older table.

It likewise ignores a `derived/peers/peers_user.json` older than the same stamp, so a file left by an unrelated earlier run cannot silently pin slots; it then falls back to `state.derived.peers_selected.user_peers`. Nothing is deleted; re-assert a list with `peers-candidates --peers`.

Each source may fail independently.

The pipeline continues on remaining sources.

`fetch_peers` returns failure only when:

- every candidate source fails,
- and the user supplied no peers.

Freshness:

```text
90 days
```

### **Modules**

```text
lib/fetchers/peers.py
lib/peers_funds.py
lib/peers_proxy.py
lib/peers_table.py
lib/peers_enrich.py
lib/peers_scoring.py
lib/fmp_http.py
```

### **13.6 Citing comparables**

Peer-selection files are silver and cannot support report citations.

A comparables table cites each peer’s own bronze evidence:

- profile,
- financials,
- valuation inputs.

`peers_selected.json` records why the peers were selected. It is lineage, not evidence.

------

## **14. Question-Driven Research Loop**

Research is organized around explicit questions.

Default maximum:

```text
R = 3 rounds
```

### **Round structure**

#### **1. Questions**

Round 1 uses seed questions from `sections.yaml`.

Each section must define 4–6 seed questions.

Rounds 2 and 3 use follow-up questions generated by the preceding synthesis step.

#### **2. Fan-out**

Select questions with `status: open`. Deferred, answered and dropped questions are not dispatched.

Group them into batches of `QUESTIONS_PER_BATCH` (2–4) related questions.

Dispatch one `sra-researcher` subagent per batch, across all sections at once.

Concurrency is capped at `MAX_PARALLEL_AGENTS` (default 16) batches in flight. When the open set needs more batches than that, they run as successive waves: parallel within a wave, sequential across waves, until the open set is exhausted or the run's wall-clock or token budget is reached (§23.3).

The cap is a concurrency width, not a question limit. Eight batches already cover 16–32 questions, and nothing refuses a larger open set — the budget is what bounds spend, not an arbitrary question count.

Questions left undispatched when a budget is reached stay `open` for the next run.

Researchers may use:

- manifest,
- grep,
- show,
- MCP tools,
- WebSearch,
- WebFetch.

Each batch writes:

```text
derived/answers/<date>_research_answer_r<r>-<slug>.md
```

Each answer contains:

- full frontmatter,
- `cited_urls`,
- per-claim inline citations,
- compact summary,
- candidate follow-up questions.

#### **3. Harvest**

Run:

```text
sra.py fetch-urls
```

for new answers.

This creates bronze evidence and URL→id maps.

Harvest is a barrier before synthesis.

#### **4. Synthesize**

For each active section, dispatch a synthesizer.

The synthesizer:

1. reads new answer material,
2. resolves answer URLs through the URL→id maps,
3. writes useful claims into the wiki with bronze citations,
4. never cites answer files,
5. proposes new questions,
6. removes:
   - already-answered questions,
   - out-of-scope questions.

#### **5. Stop**

Stop a section when it emits no material new questions.

Hard stop after `R` rounds.

Remaining questions are recorded under:

```text
Open questions
```

in the wiki page. They remain `open` in the ledger and carry into the next run.

### **14.0 The ledger accumulates across runs**

`research/questions.json` is durable per-ticker state, not per-run scratch. A question survives the build that raised it, so what one run could not answer is what the next run starts from.

**Every phase may contribute questions, not only the research loop.** A section writer that hits a gap, a critic that finds an unsupported claim, `sra-lint`, and chart selection that wants an exhibit it cannot build all produce exactly the question worth keeping. Each records it through:

```text
sra.py add-questions T --section S --question "..." --origin <phase>
```

`origin` records who raised the question, using the `purpose` vocabulary of §23.4 plus `seed` and `user`. It is triage information, never evidence.

Capture is cheap and idempotent: identity is `sha1(section|question)`, so a question re-proposed by a later phase, or by a later run, collapses into the existing entry rather than duplicating it.

**Accumulation needs a floor as well as a ceiling.** A question that is simply unanswerable from available sources would otherwise be re-attempted by every future run forever, since §14.1 makes `dropped` an explicit decision and never infers it from silence. `attempts` therefore counts dispatches that returned no citable evidence, and at `MAX_ATTEMPTS` (default 3) the question becomes `deferred`: retained, listable, revivable, but no longer dispatched.

Deferral is a statement about the evidence available so far, not a verdict. A `deferred` question returns to `open` when `invalidate --apply` sees new bronze of a kind its section subscribes to (§10.3) — new evidence is exactly the reason to try again — or when re-asserted by hand.

```text
sra.py questions T --status deferred
```

### **Question ledger**

Path:

```text
data/<T>/research/questions.json
```

Example:

```json
[
  {
    "hash": "a1b2c3d4e5",
    "question": "What does consensus imply for FY27 FCF margin?",
    "section": "valuation",
    "status": "open",
    "origin": "seed",
    "attempts": 0,
    "round": 1,
    "answered_at": "2026-08-02T11:04:00Z",
    "answer_source_ids": [
      {
        "id": "2026-05-21_sec_10q",
        "fetched_at": "..."
      }
    ],
    "answer_artifacts": [
      "2026-08-02_research_answer_r1-fcf"
    ]
  }
]
```

### **Question identity**

```python
question_hash(section: str, question: str) -> str
```

Definition:

```python
sha1(
    f"{section}|{question.strip().lower()}".encode()
).hexdigest()[:10]
```

The section is part of the identity.

The same question may therefore exist independently in two sections.

If the 10-character hash collides for different `(section, question)` pairs, `add-questions` must refuse the collision and report both entries.

### **`answer_source_ids`**

Contains stamped bronze evidence ids.

Used by:

- `invalidate`,
- `eval-retrieval`.

### **`answer_artifacts`**

Contains researcher-answer ids.

Used only as an audit trail.

Never treated as evidence.

### **14.1 Question-state transitions**

| **Transition**          | **Actor**                       | **Timing**                              |
| ----------------------- | ------------------------------- | --------------------------------------- |
| → `open`                | `add-questions`, from any phase | seeded, proposed, or user-supplied      |
| `open` → `answered`     | synthesizer via `mark-answered` | after claim is incorporated into wiki   |
| `open` → `dropped`      | synthesizer                     | explicitly out of scope or unanswerable |
| `open` → `deferred`     | driver                          | `attempts` reaches `MAX_ATTEMPTS`       |
| `deferred` → `open`     | `invalidate --apply`, or a user re-assertion | subscribed evidence arrived (§10.3) |
| `answered` → `reopened` | `invalidate --apply`            | dependency or subscription invalidation |

`attempts` increments when a dispatched batch returns no citable evidence for that question. A question answered on its second try keeps its count and is never deferred.

Deferral is deterministic bookkeeping, not judgment: the driver applies it by counting, and only a synthesizer may `drop` a question outright.

The answerer does not close questions.

The synthesizer decides whether a question was actually answered.

`mark-answered --sources` accepts bronze ids only.

If supporting URL fetches fail and no bronze evidence remains, the question stays open.

Silence never means dropped.

### **Directed updates**

A directed `/sra-update` instruction enters the same question loop.

Example:

```text
sra.py add-questions T \
  --section competitive \
  --question "What evidence exists of CrowdStrike pricing pressure?"
```

`--question` is repeatable. Each occurrence becomes one ledger entry, and every entry in the call takes the call's `--section` and `--round`:

```text
sra.py add-questions T \
  --section competitive \
  --question "What evidence exists of CrowdStrike pricing pressure?" \
  --question "Has Zscaler's SASE win rate moved in the last two quarters?"
```

Questions spanning more than one section need one call per section, or `--from-file`.

Re-adding an existing question is a no-op: identity is `sha1(section|question)`, so a repeated `--question` in the same call, or across calls, collapses to one entry rather than duplicating it.

Adding is bookkeeping and is never refused for volume. A large open set is dispatched in successive waves of `MAX_PARALLEL_AGENTS` batches (§14 step 2); anything the run's budget does not reach stays `open` for the next run. `add-questions` reports the resulting open count so the operator sees the backlog it implies, but the count is never an error.

### **Expected cold-build research budget**

Approximate:

- round 1: 10–14 answer batches,
- round 2: 6–8,
- round 3: 3–5,
- synthesizers taper by active section.

Expected research subagents:

```text
~30–40
```

### **14.2 Wiki pages**

One wiki page per report section.

Entity pages live under:

```text
wiki/entities/<slug>.md
```

Wiki pages are working notes, not report prose.

They should contain:

- key facts,
- tensions,
- quantified claims,
- bronze citations.

Forward-looking values require:

```text
[REPORTED]
[GUIDANCE]
[CONSENSUS]
[ESTIMATE]
```

with as-of dates where applicable.

Frontmatter:

```yaml
section: valuation
summary: One line saying what this page establishes.
updated_at: ...
built_from:
  - id: ...
    fetched_at: ...
open_questions: []
```

`summary` is written by whoever writes the page. It is the row the index shows, and only the author can write it: a working note opens with its scope and period conventions, so anything derived from the prose describes the assignment rather than the finding. `wiki-lint` raises `missing-summary` when it is absent, and the index falls back to deriving one — skipping preamble, stripping citations and status tags, and showing nothing at all rather than a fragment, because a wrong summary makes the index look maintained while misdescribing the page.

`00_index.md` is the wiki's NAVIGATION page, not a catalog. It carries:

- the seven report sections **in report order**, each a link, with its summary, last update, source count, open-question count and dirty flag;
- a row for every section that has **no page yet**, marked `not written` — a section nobody researched is the most important thing the table can say, and a listing of the files that happen to exist cannot say it;
- entity and other pages, linked, in their own groups;
- a rollup of every page's `open_questions`, grouped by page — what is still unknown, without opening seven 60KB pages;
- links to the phase journal, the question ledger and the latest report.

It is generated, never hand-edited, and carries no generated-at stamp: regenerating it must not show up as a diff.

`log.md` is an append-only phase journal.

The driver maintains both through:

```text
wiki-index
wiki-log
```

Subagents do not update them directly. This is about SHARED driver-maintained state; it does not cover the per-agent task logs of §23.4, which each agent writes for itself.

------

## **15. Writing, Assembly, and Polish**

### **15.1 Write wave**

Writers read:

- relevant wiki pages,
- structured artifacts referenced by those pages,
- `STYLE.md`.

They do not perform independent retrieval.

For each section:

1. writer drafts,
2. writer runs hard checks,
3. critic reviews,
4. rewrite agent applies critique,
5. rewrite agent reruns hard checks.

Output:

```text
reports/<run>/sections/<section>.md
```

Citations remain:

```text
[^bronze-id]
```

Cold build:

```text
7 sections × 3 agents ≈ 21 agents
```

The seven sections are **independent chains launched together**, not three stages over seven sections. Each section runs its own write → critic → rewrite to completion; they read different wiki pages and write different files, so nothing one section does can hold up another. Progress is grouped per section, because "valuation is rewriting while risk_news still drafts" is the true picture and a stage-shaped display hides it.

Sections are dispatched **longest first**. The harness caps concurrent workflow agents below the section count (§23.1), so one section always queues, and it should be the cheapest one.

Every agent in the wave writes its own task log (§23.4).

### **Incremental single-section path**

`/sra-write` uses one writer with:

- hard checks,
- internal self-critique.

Optional:

```yaml
single_section_critic: true
```

enables writer + critic + rewrite.

### **15.2 Static quality Workflows**

Two checked-in Workflow scripts are permitted.

#### **`workflows/write_wave.js`**

Runs:

```text
write → critic → rewrite
```

for each section, as one self-contained chain per section under a single `parallel()`.

All agents:

```text
agentType: "sra-writer"
```

`meta.phases` names the seven section titles from `sections.yaml`, and every agent in a section's chain carries that section's title as its `phase`.

The critic returns a schema, not free text — `{section, critique_path, items, contradicted, unsupported, blocking}`. The flow does not branch on it; it exists so what the critic found is recorded rather than discarded.

#### **`workflows/polish_chain.js`**

Sequential stages:

1. cross-section consistency,
2. conclusion + `verdict.json`,
3. whole-report critique,
4. shrink-mandated polish,
5. evaluation → `evaluation.json`.

Authoring requirements:

- first export:

```javascript
export const meta = {
  name,
  description,
  phases
}
```

- `meta` must be a pure literal,
- plain JavaScript only,
- no TypeScript,
- no `Date.now()`,
- no `Math.random()`,
- pass through `args`:

```text
ticker
workdir
report_date
sections
char_caps
```

- subagents receive absolute workdir paths,
- Workflow returns a summary object.

### **15.3 Deterministic assembly**

`/sra-assemble` is the orchestration skill.

`sra.py assemble` itself is deterministic and never launches model agents.

The orchestration skill runs any required polish Workflow and chart-selection phase before calling the Python assembler.

`sra.py assemble TICKER`:

1. validates `charts/chartbook.json`,
2. refuses references to nonexistent chart candidates,
3. concatenates report sections and conclusion,
4. collects citations in order of appearance,
5. renumbers:

```text
[^bronze-id] → [^1..n]
```

1. writes:
   - `references.md`,
   - `citation_map.json`,
2. renders:
   - Markdown,
   - HTML through pandoc,
   - PDF through weasyprint.

Reuse the sra5 rendering pipeline and CSS fixes, including:

- `align_numeric_columns`,
- pagetitle handling,
- empty-alt image handling.

A computed citation expands to the upstream evidence used in its derivation.

Every structured artifact requires `_meta.title` so reference generation does not depend on a separate lookup table.

### **Snapshot**

```text
sra.py snapshot TICKER
```

Creates a unique run directory.

Examples:

```text
2026-08-10
2026-08-10_2
2026-08-10_3
```

Then:

- update `latest`,
- update state,
- append a wiki log entry.

### **`verdict.json`**

Fields:

```text
rating
conviction
fair_value
horizon_months
current_price
implied_return_pct
valuation_method
thesis
key_risk
base_case_probability
vs_consensus
```

The driver recalculates `implied_return_pct`.

It must not trust the model-provided arithmetic.

------

# **Part VI — Output**

## **16. Chartbook**

### **16.1 Candidate generation**

Command:

```text
sra.py charts TICKER
```

Render every supported chart for which required inputs exist.

A renderer returns:

```python
None
```

when inputs are unavailable.

That is normal degraded behavior.

### **Tier 1 — supported today**

| **Chart**                          | **Inputs**                                               |
| ---------------------------------- | -------------------------------------------------------- |
| price / volume / relative strength | `prices_yahoo`, `technical_indicators_computed`          |
| revenue and growth                 | `income_statement_yahoo`                                 |
| margin trends                      | `income_statement_yahoo`                                 |
| FCF and conversion                 | `cashflow_yahoo`, `income_statement_yahoo`               |
| income-statement Sankey            | `income_statement_yahoo`                                 |
| forward multiple vs history        | `estimates_yahoo`, `prices_yahoo`, `key_ratios_computed` |
| peer scatter and multiples         | peer bronze profile and financial artifacts              |
| catalyst calendar                  | `events_calendar_yahoo`                                  |
| macro series                       | `_MACRO/structured/*`                                    |

### **Tier 2 — producer missing**

Not implementation requirements until a bronze producer exists:

```text
segment mix
geographic mix
RPO / billings / deferred revenue
ownership and short interest
buyback / dilution
DCF sensitivity
```

Estimate-revision history also remains unavailable because current estimate artifacts overwrite in place.

### **Verdict-dependent exhibits**

Examples:

```text
football-field valuation
DCF exhibit
```

These read:

- bronze inputs,
- `verdict.json`.

They are rendered in a separate pass (§16.4).

### **Candidate manifest**

For every chart:

```text
charts/candidates/<name>.png
charts/candidates/<name>.json
```

Example:

```json
{
  "name": "...",
  "title": "...",
  "data_sources": ["..."],
  "derived_from_urls": ["..."],
  "auto_caption": "...",
  "salience": {
    "recency_days": 3,
    "coverage": 0.92,
    "variance_note": "..."
  }
}
```

### **16.2 Salience selection**

`/sra-chartbook` uses one model subagent.

Inputs:

- candidate manifests,
- `wiki/00_index.md`,
- `verdict.json`.

Target:

```text
10–16 exhibits
```

Output:

```text
charts/chartbook.json
```

Example:

```json
{
  "selected": [
    {
      "name": "...",
      "section": "valuation",
      "order": 1,
      "caption": "..."
    }
  ]
}
```

Selected charts are embedded at their sections and again in a Chartbook appendix.

Every caption includes provider and as-of information derived from bronze metadata.

### **16.3 Chart data rules**

No chart performs network fetches.

Charts are functions of persisted artifacts.

Exception:

```text
requires_verdict = True
```

for conclusion-dependent exhibits such as the football field or DCF.

Their manifests must list:

```text
verdict
```

as an input dependency.

### **16.4 Phase ordering**

Required order:

```text
write wave
→ polish chain produces verdict.json
→ sra.py charts
→ sra.py charts --verdict
→ /sra-chartbook
→ sra.py assemble
```

`/sra-assemble` orchestrates this sequence.

`sra.py assemble` remains deterministic.

------

## **17. Chart Style**

All generated figures follow this section.

Renderer:

```text
plotly + kaleido write_image
```

Shared code:

```text
lib/charts/base.py
```

Output is static PNG for HTML and printed PDF.

### **17.1 Palette**

Colors are shared with `templates/report.css`. Do not invent new ones: a figure that needs a color absent from this section needs rethinking.

#### **Chrome and ink**

| **Role** | **Hex**   | **Use**                  |
| -------- | --------- | ------------------------ |
| Body ink | `#23282f` | labels, price line       |
| Muted    | `#5b636e` | annotations, volume      |
| Navy     | `#0f2942` | panel-style axis titles  |
| Rule     | `#dde1e6` | axis and reference lines |
| Gridline | `#eef0f3` | horizontal grid          |

#### **Price-chart series slots**

| **Slot** | **Hex**   | **Assignment**           |
| -------- | --------- | ------------------------ |
| 1        | `#2a78d6` | MA13                     |
| 2        | `#4a3aa7` | MA52                     |
| 3        | `#5b636e` | volume                   |
| 4        | `#eb6834` | relative strength vs SPX |

Never cycle these assignments.

#### **Categorical set**

```text
#2a78d6
#eb6834
#1baf7a
#4a3aa7
```

If more than four categories exist, combine the tail into:

```text
Other
```

The third categorical color requires direct labeling because its white-background contrast is below the desired legend-swatch threshold.

#### **Status colors**

| **Role** | **Hex**   |
| -------- | --------- |
| Up       | `#1a7f37` |
| Down     | `#b3261e` |

Candles and the Sankey’s semantic chains (§17.3) are the only places red and green carry meaning. Nowhere else may a series be colored by sign or by sentiment.

Red/green color alone is insufficient for candlestick direction.

Shape encoding is mandatory (§17.2).

### **17.2 Price chart**

Weekly candlesticks.

Three stacked panels:

```python
row_heights=[0.62, 0.16, 0.22]
vertical_spacing=0.035
```

Panels:

1. price,
2. volume,
3. relative strength.

Never use secondary y-axes.

Up candles:

```text
hollow
```

Down candles:

```text
filled #b3261e
```

Both use:

```text
line.width = 1
```

Maximum default history:

```text
4 years weekly
```

Moving averages:

```text
width = 1.75
```

Volume:

```text
#5b636e
opacity = 0.45
```

Do not color volume by price direction.

Relative strength:

```text
#eb6834
width = 1.75
```

Add parity line:

```text
y = 1.0
```

Annotation:

```text
= S&P 500
```

Axis:

```text
vs S&P 500, indexed to 1.0 at start
```

No legend.

Direct-label each series at its final x-value.

### **17.3 Income-statement Sankey**

Revenue node:

```text
#1a5fb4
```

Profit chain:

```text
node rgba(26,127,55,0.85)
link rgba(26,127,55,0.18)
```

Cost chain:

```text
node rgba(179,38,30,0.85)
link rgba(179,38,30,0.16)
```

Node border:

```python
line=dict(color="#0f2942", width=0.5)
```

Layout:

```text
arrangement="fixed"
```

Use explicit x positions for five columns.

Use:

```text
pad=28
```

Fold components under 0.25% of revenue into:

```text
Other
```

### **17.4 General chart rules**

Plots do not write their own title.

The report template provides the exhibit heading and caption.

Font:

```text
Helvetica Neue, Helvetica, Arial, sans-serif
```

Base size:

```text
11
```

Base color:

```text
#23282f
```

Export:

```text
CHART_WIDTH = 980
CHART_SCALE = 2
```

Default dimensions:

```text
price: 980 × 520
Sankey: 980 × 420
```

Margins:

```python
dict(l=52, r=64, t=8, b=28)
```

Axes:

- horizontal gridlines only,
- no vertical grid,
- no plot box,
- no zero line unless analytically required,
- no Plotly range slider.

Units must be visible on the chart.

Permitted exceptions must be documented at the call site.

### **17.5 Chart-change procedure**

Before committing chart code:

1. rerender from cached bronze:

```text
uv run python sra.py charts <T>
```

1. inspect PNG at 100%,
2. inspect at actual PDF scale,
3. inspect the printed page with:

```text
pdftoppm -png -r 100 -f <n> -l <n> report.pdf out
```

1. if any palette hex changed, rerun color-separation checks,
2. desaturate the price chart and verify candle direction remains distinguishable.

------

## **18. Editorial Machinery**

### **18.1** **`sections.yaml`**

Located at repo root.

Loaded by:

```text
lib/sections.py:load_sections
```

Report order:

```text
profile
business_model
competitive
supply_chain
financial
valuation
risk_news
```

File order must equal:

```text
lib.sections.SECTION_IDS
```

The loader enforces this.

Notation:

- `§N` means this specification.
- Report sections use `Report §N` or their section id.

Each section defines:

```text
title
wiki_page
seed_questions
research_guidance
write_guidance
word_target_base
hard_checks
subscribes_to
```

`seed_questions` must contain 4–6 questions.

Required top-level keys:

```text
length_presets
section_ownership
tension_analysis
claim_status_rule
```

Hard checks include:

- single-H2 rule,
- no internal filenames,
- character/word budget.

Claim-status labeling belongs in research guidance so forecast status is established before report writing.

### **18.2** **`STYLE.md`**

`STYLE.md` is supplied in full to:

- writers,
- critics,
- rewrite agents.

It governs:

- audience,
- stance,
- citation discipline,
- claim-status labels,
- source hierarchy,
- formatting,
- number conventions.

The source hierarchy must match the layer model:

1. filings,
2. provider structured evidence,
3. transcripts,
4. computed bronze,
5. fetched third-party documents,
6. live tool results.

Live tool results are not citable until persisted in bronze.

`STYLE.md` must also preserve the writer-facing citation rule:

- claims use `[^id]`,
- ids must resolve to bronze,
- unsupported silver notes cannot be laundered into report claims,
- internal filenames and artifact ids do not appear in prose.

### **18.3 Polish**

Polish must:

- consume the redundancy worklist,
- preserve genuine analytical tensions,
- satisfy `not_longer_than`.

------

# **Part VII — Contracts and Operations**

## **19.** **`sra.py`** **Command Surface**

All subcommands accept:

```text
--data-root
```

after the subcommand.

Default:

```text
DEFAULT_DATA_ROOT = <repo>/data
```

Ticker path:

```python
ticker_dir(data_root, ticker) = data_root / ticker.upper()
```

### **Bronze / fetch**

| **Command**                                     | **Purpose**              |
| ----------------------------------------------- | ------------------------ |
| `init T`                                        | initialize ticker        |
| `status T`                                      | report stale bronze      |
| `prefetch T [--kinds] [--stale-only] [--peers]` | ticker gather            |
| `prefetch-macro [--series] [--stale-only]`      | shared macro gather      |
| `peers-candidates T [--peers] [--top-funds N]`  | build peer candidate set |
| `fetch-urls T [--from ANSWER_ID] [--max N]`     | harvest researcher URLs  |

### **Retrieval**

| **Command**                                          | **Purpose**                   |
| ---------------------------------------------------- | ----------------------------- |
| `manifest T`                                         | rebuild/print source manifest |
| `show TICKER ID`                                     | inspect artifact              |
| `grep T PATTERN [--kinds] [--context N] [--top-k K] [--include-archived]` | search bronze docs |
| `eval-retrieval T [--k 10] [--baseline PATH]`        | retrieval evaluation          |

### **Gates**

| **Command**   | **Purpose**                              |
| ------------- | ---------------------------------------- |
| `validate T`  | fatal provenance and contract validation |
| `wiki-lint T` | advisory silver checks                   |

### **Research bookkeeping**

| **Command**                                       | **Purpose**                |
| ------------------------------------------------- | -------------------------- |
| `questions T [--section S] [--status open\|answered\|dropped\|reopened\|deferred]` | list ledger |
| `add-questions T --section S (--from-file F \| --question Q ...) [--round N] [--origin P]` | add questions; `--question` repeatable, callable from any phase |
| `mark-answered T --question-hash H --sources IDS` | close with bronze evidence |
| `invalidate T [--apply]`                          | compute/apply blast radius |

### **Wiki / peers**

| **Command**                        | **Purpose**                     |
| ---------------------------------- | ------------------------------- |
| `wiki-log T --entry E [--agents N] [--tokens N] [--minutes M] [--run R]` | append phase journal |
| `wiki-index T`                     | rebuild wiki navigation page    |
| `mark-dirty T --section S`         | dirty a report section          |
| `peers-select T [--ranked-file P]` | deterministic peer pin-and-fill |

### **Report**

| **Command**            | **Purpose**                                                  |
| ---------------------- | ------------------------------------------------------------ |
| `charts T [--verdict]` | render chart candidates                                      |
| `assemble T`           | deterministic concatenation, citation processing, and rendering |
| `run-log T [--run R]`  | assemble the run's audit log from its per-agent task logs    |
| `snapshot T`           | create immutable report-run snapshot                         |

### **One-shot**

```text
migrate T
```

Removed after all existing corpora have migrated.

### **Retired commands**

```text
ingest
search
apply-tags
audit-page-citations
render
```

Also removed:

```text
sra-ingest skill
sra-tagger agent
data/*/index/
lancedb
pyarrow
tiktoken
```

`openai` remains because `lib/fetchers/perplexity.py` uses it.

------

## **20. Module Contracts**

### **`lib/provenance.py`**

```python
BRONZE_KINDS: frozenset[str]
MODEL_KINDS: frozenset[str]

DERIVED_SUBDIR = "derived"
SOURCE_COMPUTED = "computed"
```

`BRONZE_KINDS` and `MODEL_KINDS` must be disjoint.

```python
make_source_id(
    kind: str,
    on: date,
    topic: str | None = None
) -> str
```

Picks the smallest free `_<n>` suffix against `sources/` and `sources/archive/` together, so an id is never reused after archiving (§5).

### **`SourceMeta`**

Required fields:

```text
id
ticker
kind
source
url
fetched_at
as_of
title
fetch_tool
fetch_cmd
```

Optional:

```text
request        required for API-fetched artifacts (§5)
supersedes
cited_urls
```

Use:

```python
field(default_factory=list)
```

for mutable list defaults.

### **Source I/O**

```python
write_source(ticker_dir, meta, body) -> Path
```

- writes `sources/<id>.md`,
- raises `FileExistsError` on overwrite,
- rejects kinds outside `BRONZE_KINDS`,
- when `meta.supersedes` is set, moves the superseded file to `sources/archive/<old-id>_<YYYY-MM-DD>.md` before returning, stamped with today's date (§5).

```python
resolve_source(ticker_dir, source_id) -> Path | None
```

Looks in `sources/`, then `sources/archive/`, matching an archived file by its id prefix. Every id-to-path lookup — `show`, citation resolution, reference building — goes through it, so no caller has to know whether a source is current.

```python
write_answer(ticker_dir, meta, body) -> Path
```

- writes `derived/answers/<id>.md`,
- rejects kinds outside `MODEL_KINDS`.

```python
read_source(path) -> tuple[SourceMeta, str]
```

### **`StructuredMeta`**

```text
id
ticker
producer
title
source
as_of
provider_tool
fetch_cmd
url
request
fetched_at
computed_at
period
currency
adjusted
derived_from
```

Use:

```python
field(default_factory=list)
```

for `derived_from`.

### **Structured I/O**

```python
write_structured(ticker_dir, meta, data) -> Path
```

Writes `structured/<id>.json` as `{"_meta": ..., "data": ...}`; overwrite is allowed.

Allowed producers:

```text
fetch
compute
```

Model artifacts are forbidden, and a producer whose shape is unsatisfied raises `ValueError`.

```python
write_derived(
    ticker_dir,
    meta,
    data,
    namespace: str | None = None
) -> Path
```

Writes model output and other non-evidence silver artifacts under:

```text
derived/
```

or a documented namespace such as:

```text
derived/peers/
```

Producer shape must match the artifact’s declared contract. `write_derived` is separate from `write_structured` so a silver artifact cannot reach `structured/` by passing a subdir.

```python
read_structured(path) -> tuple[StructuredMeta, dict | list]
```

### **`lib/statefile.py`**

```python
init_state(ticker_dir, ticker) -> dict
load_state(ticker_dir) -> dict
save_state(ticker_dir, state) -> None
```

`save_state` is atomic.

```python
record_fetch(
    state,
    data_kind,
    current_id: str | list[str],
    fetched_at,
    policy
) -> None
```

The implementation normalizes `current_id` to `current_ids: list[str]`.

Policy:

```python
{"policy_days": int}
```

or:

```python
{"policy": "on_earnings" | "on_new_filing"}
record_derived(
    state,
    key,
    current_id,
    updated_at,
    derived_from
) -> None
stale_kinds(
    state,
    now,
    last_earnings: date | None = None,
    ticker_dir: Path | None = None
) -> list[str]
```

`ticker_dir` enables missing-artifact checks.

```python
mark_section_dirty(state, section) -> None
```

Deduplicates:

```text
state["report"]["sections_dirty"]
```

### **`lib/validate.py`**

```python
validate(ticker_dir, data_root) -> list[Finding]
@dataclass
class Finding:
    severity
    code
    path
    message
```

Any `error` finding causes CLI exit 1.

### **`lib/manifest.py`**

```python
build_manifest(ticker_dir) -> Path
manifest_rows(ticker_dir) -> list[dict]
```

### **`lib/grep.py`**

```python
grep(
    ticker_dir,
    pattern,
    kinds=None,
    context=2,
    top_k=None
) -> list[Hit]
@dataclass
class Hit:
    source_id
    kind
    as_of
    url
    title
    excerpt
    matched_terms
```

Ranking follows §9.

### **`lib/questions.py`**

```python
question_hash(
    section: str,
    question: str
) -> str
```

Implementation:

```python
sha1(
    f"{section}|{question.strip().lower()}".encode()
).hexdigest()[:10]
```

Ledger schema follows §14, including `origin`, `attempts`, and the `deferred` status.

```python
record_attempt(ticker_dir, question_hash) -> str
```

Increments `attempts` and returns the resulting status, flipping `open` to `deferred` at `MAX_ATTEMPTS`. Deterministic; no model in the path.

### **`lib/research.py`**

Tunable constants for the §14 loop. Changing one is a commit, not a runtime flag, and `tests/test_research_limits.py` pins the defaults:

```python
MAX_PARALLEL_AGENTS = 16      # answer batches in flight per wave
MAX_INCREMENTAL_SUBAGENTS = 8 # §23.2's directed-research spend ceiling
QUESTIONS_PER_BATCH = (2, 4)  # questions grouped into one batch
MAX_ATTEMPTS = 3              # empty dispatches before a question defers
DEFAULT_ROUNDS = 3            # R in §14
```

`MAX_PARALLEL_AGENTS` is a concurrency width, not a question ceiling. Raising it widens each wave; it does not change what the run is allowed to spend, which is §23.3's business. It is sized against the harness's Agent-tool concurrency (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`, 24 in `.claude/settings.json`), so a cold build's 10-14 round-1 batches run as one wave.

`MAX_INCREMENTAL_SUBAGENTS` is a SPEND ceiling and deliberately a separate constant. The two were one number until widening a wave would have silently doubled what §23.2 allows an incremental run to cost.

### **`lib/references.py`**

```python
collect_citations(md) -> list[str]
renumber(md, mapping) -> str
build_references_md(ticker_dir, ids) -> str
write_citation_map(run_dir, mapping) -> Path
```

Computed citations expand to upstream bronze evidence.

### **`lib/charts/`**

`base.py` defines shared style.

Each renderer:

```python
render_<name>(ticker_dir) -> ChartResult | None
@dataclass
class ChartResult:
    name
    png_path
    manifest_path
```

Each renderer declares:

```python
requires_verdict: bool = False
```

`charts` runs the `False` set.

`charts --verdict` runs the `True` set.

### **General conventions**

- `pathlib.Path` everywhere.
- Type hints everywhere.
- No bare `except:`.
- Data functions return `(success, data, error_msg)`.
- `main()` returns an exit code.

------

## **21. Skills and Agents**

Project skills:

```text
.claude/skills/<name>/SKILL.md
```

Long prompts:

```text
prompts/
```

### **Skills**

| **Skill**       | **Invocation**                       | **Role**                                                     |
| --------------- | ------------------------------------ | ------------------------------------------------------------ |
| `sra-build`     | `/sra-build TICKER [--length …]`     | cold-build orchestrator                                      |
| `sra-update`    | `/sra-update TICKER ["instruction" ...]` | incremental orchestrator                                     |
| `sra-prefetch`  | `/sra-prefetch TICKER [...]`         | deterministic gather + deep research + harvest               |
| `sra-peers`     | `/sra-peers TICKER [--peers ...]`    | candidate gather + one ranking agent + deterministic selection |
| `sra-research`  | `/sra-research TICKER <section\|entity> [--rounds N]` | question-driven research loop (§14)              |
| `sra-write`     | `/sra-write TICKER <section>`        | incremental section writer                                   |
| `sra-lint`      | `/sra-lint TICKER`                   | model judgment lint                                          |
| `sra-chartbook` | `/sra-chartbook TICKER`              | chart selection                                              |
| `sra-assemble`  | `/sra-assemble TICKER`               | polish/chart orchestration followed by deterministic `sra.py assemble` |
| `sra-status`    | `/sra-status TICKER`                 | freshness and wiki status                                    |

### **Agents**

| **Agent**        | **Tools**                           | **Purpose**        |
| ---------------- | ----------------------------------- | ------------------ |
| `sra-researcher` | inherits all tools and MCP          | research           |
| `sra-writer`     | Read, Write, Edit, Glob, Grep, Bash | writing and checks |
| `sra-rater`      | Read, Write, Edit, Glob, Grep       | peer ranking       |

### **Researcher privilege**

The researcher inherits MCP access only when no explicit `tools:` allowlist is present.

This also leaves it with broad local capabilities.

Mitigations:

- retrieved material is treated as untrusted data,
- instructions embedded in fetched content must not be followed,
- `.env` and credential files must not be read,
- environment variables must not be echoed,
- answer files are included in secret scanning,
- model-selected bulk URL fetching is performed by the hardened driver rather than the agent.

This is mitigation, not containment.

If the harness later permits MCP access with a restricted allowlist, remove Bash from the researcher.

------

## **22. Gates and Error Handling**

### **22.1** **`wiki-lint`**

Deterministic checks:

| **Check**                                   | **Method**                |
| ------------------------------------------- | ------------------------- |
| numeric claim without citation              | paragraph scan            |
| forward-looking number without status/as-of | regex                     |
| section ownership breach                    | ownership map             |
| duplicate figure in multiple pages          | numeric compare; advisory |
| invalid `built_from`                        | graph walk                |
| entity page missing from index              | set difference            |

Model judgment remains limited to:

- whether a cited source actually supports a claim,
- whether an analytical tension is genuine.

`/sra-lint` runs only after deterministic lint.

### **22.2 Citation coverage limits**

Hard guarantee:

Every citation resolves to bronze evidence.

Not a hard guarantee:

Every assertion has a citation.

Defense layers:

| **Layer**          | **Purpose**                            |
| ------------------ | -------------------------------------- |
| numeric-claim scan | catches uncited numerical assertions   |
| section critic     | catches unsupported qualitative claims |
| `sra-lint`         | checks source support                  |

An unsupported qualitative claim may still escape these checks.

The specification does not claim otherwise.

### **22.3 Error handling**

- Fetchers retain structured success/error contracts.
- Macro-series failure is non-fatal.
- multpl shape failures are loud.
- `validate` is fatal.
- `wiki-lint` is advisory.
- `invalidate` is dry-run unless `--apply`.
- `add-questions` never refuses on volume; a large open set is dispatched in waves and the remainder stays `open` (§14).
- a question that returns no citable evidence `MAX_ATTEMPTS` times becomes `deferred`, not dropped (§14.1).
- immutable sources cannot be overwritten.
- model evidence cannot be placed in bronze directories.
- leaked keys cause validation failure.

------

## **23. Flows and Budgets**

### **23.1 Cold build**

```text
0. Ask user for peer list
1. sra.py init T
2. /sra-prefetch
   - deterministic gather
   - deep-research topics
   - fetch-urls
3. sra.py prefetch-macro
4. sra.py manifest T
5. sra.py validate T
6. /sra-peers
7. research loop
8. sra.py wiki-lint T
9. /sra-lint
10. sra.py validate T
11. write wave
12. polish chain → verdict.json
13. sra.py charts T
14. sra.py charts T --verdict
15. /sra-chartbook
16. sra.py assemble T
17. sra.py validate T
18. sra.py snapshot T
```

### **Resume**

Every phase checks output existence and freshness.

All completed state is durable.

Re-running `/sra-build` skips completed fresh phases.

### **Expected subagent graph**

| **Phase**            | **Agents** |
| -------------------- | ---------- |
| deep-research topics | 7          |
| answerers            | ~23        |
| synthesizers         | ~15        |
| peer rater           | 1          |
| judgment lint        | 1          |
| write wave           | 21         |
| polish chain         | 5          |
| chart selection      | 1          |
| **Expected total**   | **74**     |

Cold-build ceiling:

```text
100
```

Reducing research rounds from three to two is the primary mechanism for lowering agent use.

Expected wall clock:

```text
45–60 minutes
```

Wall clock is dominated by research and write depth rather than plumbing. Answer fan-outs and the write wave run wide, so parallel width is what keeps a 74-agent graph inside the §2.5 hour.

### **Concurrency ceilings the host imposes**

Parallel width is bounded by the harness, not by this spec, and by two independent limits that are often confused:

| Dispatch path | Limit | Configurable |
| --- | --- | --- |
| Agent tool (skills: answerers, deep research, rater, lint, chart selection) | `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`, default 20 | yes, via `.claude/settings.json` |
| Workflow `agent()` (`write_wave.js`, `polish_chain.js`) | `min(16, max(2, cpu_cores - 2))` | no — derived from the core count at module load |

The answerer fan-out is the widest phase at ~23 agents, so the Agent-tool limit is raised to 24 in `.claude/settings.json`; at the default of 20 the last three answerers of a round spill into a second wave for no reason but the cap.

The workflow limit cannot be raised. On an 8-core machine it is 6, and the write wave has 7 sections, so one section always queues. Slots are held per `agent()` call rather than per chain and the queue is FIFO, so a chain releases its slot between stages and the backlog self-balances: the cost is roughly one extra stage of slack, not a doubled makespan. §15.2 requires the write wave to order sections longest-first so that the section which queues is the cheapest one.

### **23.2 Incremental flows**

The polish chain is conditional.

When three or more sections are dirty:

```text
full five-stage polish chain
```

When fewer than three are dirty:

```text
cross-section check
+
conclusion/verdict
```

Only `/sra-assemble` makes this model-orchestration decision.

`sra.py assemble` remains deterministic.

### **Bare refresh**

```text
status
→ prefetch --stale-only
→ invalidate --apply
→ research reopened questions
→ write dirty sections
→ /sra-assemble
```

Typical example:

```text
~8 subagents
```

Wall-clock ceiling:

```text
30 minutes
```

Bare refresh carries no separate subagent ceiling. Wall clock is the binding constraint (§2.5).

### **Directed research**

```text
add question(s)
→ one research round
→ fetch-urls
→ synthesize
→ write affected section
→ /sra-assemble
```

Typical example:

```text
~5 subagents
```

Ceilings:

```text
8 model subagents
30 minutes
```

One invocation may carry several instructions (§3). They batch into the same round, so the ceiling binds on total agents, not on question count.

The 8 is `MAX_INCREMENTAL_SUBAGENTS` (§20) — a spend ceiling, distinct from `MAX_PARALLEL_AGENTS`, which is a concurrency width. A larger open set runs as successive waves until the wall clock or token budget is reached; whatever is not reached stays `open` for the next run.

### **Report-only edit**

```text
/sra-write
→ /sra-assemble
```

No new research.

### **23.3 Budgets and gates**

Cold build:

```text
≤100 model subagents
≤6M tokens (target ≤3M)
≤60 minutes
```

Budget check:

```python
check_budgets(
    run_stats,
    max_subagents=100,
    max_tokens=6_000_000,
    max_minutes=60
)
```

`run_stats.json` records `started_at` and `finished_at` (§23.4), so the wall-clock gate is checkable from the same record as the token gate.

The 6M ceiling is the failure threshold, not the goal. The expected 74-agent graph lands near 5M at ~60–80k input per agent, so the ≤3M target requires structural reduction rather than a lower ceiling. The levers, largest first:

- research rounds 3 → 2, removing ~7 answerers and ~3 synthesizers,
- write-wave depth: 7 × (write, critic, rewrite) is the single largest block, and `single_section_critic` (§15.1) already models the cheaper shape,
- fewer, larger answer batches per round, since every batch pays the ~50k context floor of §1.1.

Incremental:

```text
bare refresh or directed research ≤30 minutes
directed research ≤8 model subagents
```

### **Quality gate**

Rebuilt PANW report:

```text
overall evaluator score ≥4.5
```

Baseline:

```text
4.7
```

Reference report:

```text
sra5/work/PANW_20260730/
```

Retain:

- verdict card,
- key-tests table,
- monitoring dashboard.

### **Provenance gate**

Require:

- `sra.py validate` clean,
- all citations resolve to bronze evidence,
- no internal filenames in prose,
- rendered references in Markdown, HTML, and PDF.

### **Retrieval gate**

Must satisfy §9.2.

### **Incremental gate**

After directed research, only affected section files may change.

Compare:

```text
reports/<run>/sections/
```

to the previous snapshot.

### **23.4 Instrumentation**

Three artifacts, each answering a different question.

| Artifact | Question | Written by |
| --- | --- | --- |
| `wiki/log.md` | what phases ran, in order, across every run | orchestrating skills, at phase boundaries |
| `reports/<run>/run_stats.json` | what the run cost | orchestrating skills, per agent |
| `reports/<run>/run_log.md` | what the run actually did | `sra.py run-log`, from per-agent task logs |

#### **The phase journal**

`wiki/log.md` is a phase journal. It is not an audit log — the audit log is `run_log.md`.

Orchestrating skills call:

```text
sra.py wiki-log
```

at phase boundaries. Manual granular commands may leave no log entry.

An entry may carry what the phase cost and a link to the run log that details it:

```text
sra.py wiki-log T --entry "lint: 40 findings" \
    --agents 6 --tokens 412000 --minutes 8.4 --run 2026-08-11
```

```markdown
- 2026-08-12T03:57:11+00:00 lint: 40 findings
  [6 agents · 412k tok · 8.4 min](../reports/2026-08-11/run_log.md)
```

Still one entry per phase boundary. With none of those flags the output is exactly what it has always been.

#### **Per-agent task logs**

Every dispatched agent writes ONE log, and writes it itself:

```text
reports/<run>/log/<NN>_<purpose>_<slug>.md
```

This is not a violation of §14.2's "subagents do not update them directly": that rule protects shared driver-maintained state, where a second writer corrupts a file. A task log has exactly one writer and one reader, so there is no contention, no interleaving, and no ordering to negotiate.

It has to be the agent because nothing else can. A Workflow script has no filesystem and §15.2 bans `Date.now()`, so it can neither log nor time itself; the orchestrating skill sees only an agent's final message. What was read, what was fetched, what was decided and how long it took exists nowhere but in that agent's own context.

Frontmatter, uniform across every task type:

```yaml
purpose: section-write        # the closed vocabulary below
section: valuation            # or null
round: 1
label: "write:valuation"
started_at: 2026-08-12T03:12:04Z
finished_at: 2026-08-12T03:19:41Z
status: ok                    # ok | degraded | failed
outputs: [sections/valuation.md]
```

Body: `## Inputs`, `## Commands`, `## Outputs`, `## Notes` — fixed headings, so every task reads alike and the assembler can find them.

Agents that have Bash stamp themselves with `date -u +%Y-%m-%dT%H:%M:%SZ`. `sra-rater` has no Bash; its log omits the stamps and sorts last. A log is written even when the work failed — a failed stage with no log is indistinguishable from a stage that never ran.

Agents do not record their own token counts. They cannot see them.

#### **Per-run statistics**

```text
reports/<run>/run_stats.json
```

Example:

```json
{
  "started_at": "...",
  "finished_at": "...",
  "degraded_kinds": ["transcript"],
  "subagents": [
    {
      "purpose": "answerer",
      "section": "valuation",
      "round": 1,
      "input_tokens": 61200,
      "output_tokens": 3100
    }
  ],
  "totals": {
    "subagents": 74,
    "input_tokens": 4910000,
    "output_tokens": 388000
  }
}
```

Token counts must be recorded per agent.

Allowed `purpose` values:

```text
deep-research
answerer
synthesizer
rater
lint
section-write
section-critic
section-rewrite
chart-select
<polish-stage-name>
```

The same vocabulary, plus `seed` and `user`, is the `origin` of a ledger question (§14.0), so a question's provenance and an agent's cost record use one set of names. It is also the `purpose` of a task log, which is what lets the two be joined.

#### **The run log**

```text
sra.py run-log T [--run R]   ->   reports/<run>/run_log.md
```

Deterministic and idempotent, like the source manifest and the wiki index. It takes no lock and can be run against a build still in flight, which is when it is most wanted. It contains:

1. the run's start, finish, wall clock, agent count, token totals, `degraded_kinds`, and any `check_budgets` violation verbatim;
2. **cost by purpose** — agents, input, output and logged time per phase. This is the "which phase to cut" table that per-agent token counts were always for and that raw JSON could never present;
3. a **timeline**, one row per task in `started_at` order, each linking to its own log;
4. **unattributed** entries (see below);
5. each task's own account, long blocks truncated with a link to the full text;
6. links to the run's artifacts, the wiki index and the phase journal.

Two rules the assembler enforces:

- **Tokens are joined, never duplicated.** An agent cannot see its own usage, so counts stay in `run_stats.json` and are matched to task logs on `(purpose, section, round)`, consumed one-for-one. Exactly one writer for any given fact.
- **Nothing is silently dropped.** A `run_stats` entry with no task log, and a task log with no `run_stats` entry, both appear under **Unattributed**. A run log that omitted either would read as complete coverage of a run it had barely described.

A malformed task log is read for whatever it does carry rather than skipped. Agents write these by hand, and the run log is the artifact you reach for once something has already gone wrong: failing to build it because one agent mis-quoted a colon would lose the other twenty accounts.

`run_log.md` is a snapshot deliverable (§15.3), so a stamped run carries its own audit trail.

------

## **24. Testing**

Default:

```text
uv run pytest -q -m "not integration"
```

Network tests:

```python
@pytest.mark.integration
```

`tmp_ticker_dir` creates a PANW fixture tree.

### **Required unit and regression coverage**

#### **Layering**

- citation resolving to silver fails,
- historical PANW answer-chain defect is a regression fixture.

#### **Metadata**

Test all producer shapes and required fields, including `request` present on an API-fetched artifact, absent on a page fetch, and never carrying a credential parameter (§5, §8.4 check 6).

Verify:

- `write_structured` rejects a `model` producer and writes only into `structured/`,
- `write_derived` writes silver artifacts into `derived/` and its documented namespaces.

#### **Registry**

Pin:

```text
prices before technical
profile before wikipedia
```

#### **Retrieval**

Run `eval-retrieval` in CI.

#### **Peers**

All peer tests run offline.

Fund-filter fixture must confirm removal of:

```text
CRWL
SZNE
VETS
CIBR.L
```

and expected surviving top five:

```text
VIRS
CLOD
SPAM
VCLO
WEPN
```

Test:

- overlap filter,
- hygiene filter,
- subject exemption,
- user pinning,
- top-five limit,
- ranking-order fill,
- subject exclusion,
- proxy-only ranked candidate,
- stale ranking rejection.

The rater itself is not numerically scored in Python.

Tests validate the candidate data supplied to the model and deterministic selection of the model’s ordered output.

#### **Macro**

Use recorded FRED JSON and multpl HTML.

Test:

- frequency-to-policy mapping,
- malformed-page shape failure.

#### **Key redaction**

No artifact, warning, or log may contain current FRED or FMP key values.

#### **`fetch-urls`**

Test:

- fresh URL dedupe,
- 30-day refetch and supersede,
- failed fetch → warning + null map entry,
- idempotence.

#### **Source archiving**

Test:

- writing a source with `supersedes` moves the old file to `sources/archive/<old-id>_<date>.md` and leaves the new one in `sources/`,
- the archived file is byte-identical to what was current, frontmatter included,
- `make_source_id` does not reuse an archived id: archiving `<date>_news_yahoo` and fetching news again the same day yields `<date>_news_yahoo_2`,
- a citation to an archived id still resolves under `validate`,
- `manifest` and `grep` exclude archived files; `grep --include-archived` finds them; `show` resolves one with no flag,
- archiving is idempotent under a re-run of the same refresh.

#### **Question ledger**

Test:

- repeatable `--question`: N occurrences in one call produce N entries, all carrying the call's section, round, and origin,
- identity: the same question text repeated in one call, or re-added later by a different phase, collapses to one entry and does not reset `attempts`,
- the same text under two sections produces two independent entries,
- any volume of questions is accepted and written; nothing is refused for count,
- `record_attempt` flips `open` to `deferred` exactly at `MAX_ATTEMPTS`, and a question answered before then never defers,
- fan-out selects `open` only: `deferred`, `answered` and `dropped` entries are not dispatched,
- a `deferred` question returns to `open` when `invalidate --apply` sees new bronze of a subscribed kind (§10.3),
- wave scheduling: an open set needing more than `MAX_PARALLEL_AGENTS` batches runs in successive waves, and questions left undispatched when the budget is reached remain `open` rather than being dropped or deferred.

#### **`invalidate`**

Test separately:

1. replacement dependency path,
2. new-period subscription path.

#### **Charts**

Render from an offline fixture tree.

Assert:

- verdict-independent charts render normally,
- verdict-dependent charts require `--verdict`.

#### **Driver idempotency**

Test:

- provenance stamps,
- freshness,
- multi-id missing-artifact detection,
- citation renumbering,
- chart manifests.

### **Failure-mode tests**

#### **Assembled report**

Numeric report citations plus valid `citation_map.json` must pass.

Mapping to silver must fail.

#### **Multiple same-day snapshots**

Second snapshot:

```text
<date>_2
```

`latest` must follow it.

Diff against first snapshot must remain possible.

#### **Crash consistency**

Terminate after a fetcher writes an artifact and before state is saved.

The rerun must neither crash on `FileExistsError` nor double-count; the `_<n>` id disambiguator (§5, §7.1) absorbs the half-completed retry.

#### **Lock contention**

Second mutating command must:

- fail,
- identify lock holder,
- perform no writes.

#### **Same-day source collision**

Two same-kind writes on one date produce unique ids.

#### **SSRF**

Reject:

```text
file:
127.0.0.1
169.254.169.254
DNS → private IP
public redirect → private IP
oversized response
```

#### **Path containment**

Reject:

```text
../evil
A/B
```

Slug topics safely.

#### **Degraded mode**

- missing transcript: build succeeds with gap noted,
- missing financials: build halts.

#### **Migration**

Run twice; second run changes nothing.

Citation repair worklist must be exact.

#### **End-to-end**

Recorded fixture tree, no network.

Verify phase order, including:

```text
verdict before verdict-dependent charts
charts before deterministic assembly
```

------

## **25. Repository Layout**

```text
sra6/
  CLAUDE.md
  sra6-spec.md
  STYLE.md
  sections.yaml
  sra.py

  lib/
    fetchers/
    provenance.py
    statefile.py
    manifest.py
    grep.py
    validate.py
    questions.py
    references.py
    render/
    charts/
    peers_funds.py
    peers_proxy.py
    peers_table.py
    peers_enrich.py
    peers_scoring.py
    fmp_http.py

  prompts/
  workflows/
    write_wave.js
    polish_chain.js

  templates/
    final_report.md.j2
    report.css

  .claude/
    skills/
      sra-build/
      sra-update/
      sra-prefetch/
      sra-peers/
      sra-research/
      sra-write/
      sra-lint/
      sra-chartbook/
      sra-assemble/
      sra-status/

    agents/
      sra-researcher.md
      sra-writer.md
      sra-rater.md

  data/
    <TICKER>/
    _MACRO/

  tests/
  .mcp.json
  pyproject.toml
```

Keep `CLAUDE.md` short.

sra5’s 468-line file added roughly 8.5k tokens to every subagent invocation.

Provider keys are loaded once at the start of `sra.py` through `load_dotenv()`.

Keys:

```text
FMP_API_KEY
FRED_API_KEY
OPENAI_API_KEY
PERPLEXITY_API_KEY
```

`PERPLEXITY_API_KEY` is optional.

Reuse adapted sra5 components where practical:

- profile fetcher,
- technical fetcher,
- fundamental fetchers,
- EDGAR,
- Wikipedia,
- Perplexity,
- hard checks,
- report rendering,
- templates,
- CSS,
- Sankey code,
- chart style,
- MCP proxy,
- MCP probe,
- existing agent definitions where compatible.

Every reused fetcher must be updated for:

- provenance,
- `data/<T>/`,
- source separation,
- state registration.

------

## **26. Migration**

Existing corpora:

```text
data/PANW/
data/CRWD/
data/TOST/
data/TSLA/
```

must be migrated.

Command:

```text
sra.py migrate T
```

Steps:

1. Move:

```text
sources/*_research_answer_*.md
```

1. to:

```text
derived/answers/
```

1. Move peer-selection artifacts, including any existing `structured/peers_selected.json`, into:

```text
derived/peers/
```

1. Delete stale peers intermediates left in `structured/`.

1. Move every superseded source — one named in another source’s `supersedes` — into `sources/archive/`, appending the superseding file’s `fetched_at` date, since the original supersede date is not recorded. Citations are unaffected: ids do not change and resolution covers the archive (§5).

1. Rename the directories this spec renamed: `data/<T>/.research/` → `data/<T>/research/`, and `data/_macro/` → `data/_MACRO/`. The macro rename is case-only, so on a case-insensitive filesystem it must go through a temporary name rather than a single `mv`.

1. Rewrite any source carrying `kind: custom` to `kind: other`.

3. Rewrite computed artifacts to the `compute` shape:

   - remove `url: ""`,
   - add `computed_at`.

4. Backfill `fetch_cmd` only where it can be inferred safely.

   - Flag remaining artifacts for manual repair.

5. Convert:

   

   - `wiki.built_from`,
   - `questions.answer_source_ids`

   from bare ids to stamped objects.

6. Delete legacy `index/`.

7. Report every wiki citation that resolves to silver.

Do not automatically rewrite silver citations.

A directed research pass must recover the underlying evidence, harvest URLs if necessary, and replace the citation with bronze ids.

`migrate` must be idempotent.

------

# **Part VIII — Design Record**

## **27. Rejected Alternatives**

### **27.1 GICS classification**

FMP and Yahoo classifications are not licensed GICS data.

Use:

```text
fmp_sector
fmp_industry
```

SEC SIC was rejected because it is too coarse for peer selection.

### **27.2 10-K competition section as peer source**

Modern technology 10-K competition sections often name categories rather than competitors.

Verified examples included CRWD and PANW.

Not used as a mechanical peer source.

### **27.3 Agent-memory frameworks**

Mem0, Zep/Graphiti, Cognee, and similar systems target conversational memory.

SRA6 instead requires:

- immutable evidence,
- exact citation,
- point-in-time provenance.

CoALA mapping:

| **Type**   | **SRA6**                                                   |
| ---------- | ---------------------------------------------------------- |
| Working    | absent by design                                           |
| Episodic   | `sources/` + `wiki/log.md`                                 |
| Semantic   | wiki                                                       |
| Procedural | missing                                                    |
| Temporal   | partial through timestamps, `supersedes`, and invalidation |

Procedural memory remains a real future gap.

### **27.4 Weighted peer scoring**

Rejected.

Do not assign fixed numeric weights to model-generated or mechanical peer signals.

The model receives candidate:

- metrics,
- descriptions,
- source memberships,
- overlap data,
- taxonomy,
- proxy excerpt,

and chooses the ordered top candidates using a written rubric.

The driver performs no weighted ranking.

Retired:

```text
extract_peer_names
resolve_symbols
WEIGHTS
composite
parse_weights
AXES
--weights
```

The model’s ordered output is consumed directly by deterministic pin-and-fill selection.

### **27.5 Deterministic DEF 14A parsing**

Rejected because heading patterns vary materially between issuers.

Use peer-group excerpts instead.

### **27.6 Seeking Alpha peer pages**

Rejected due to:

- JavaScript rendering,
- Cloudflare,
- lack of public API,
- provenance and usage constraints.

### **27.7 LanceDB vector index**

Removed.

Re-entry depends on the measured threshold in §9.2.

### **27.8 LLM-generated candidate universe**

Not currently used.

The hygiene filter would be required before such candidates could enter selection.

### **27.9 Backfilling old peer selections**

Not required.

Existing tickers rerun the current peer pipeline.

------

## **28. Open Questions**

These do not block initial implementation.

1. **Research-answer retention** Should old files under `derived/answers/` be:retained forever,aged out,pruned when obsolete?

2. **Entity-page routing**

   

   How should directed update instructions choose between:

   - section page,
   - entity page?

3. **Procedural memory**

   

   How should knowledge learned while researching PANW improve later CRWD research?

4. **`on_new_filing`**

   

   Replace the current 7-day fallback with SEC-index-based filing detection.

5. **ALFRED vintage history**

   

   Add only when reports require explicit reconstruction of revised macro series.

6. **Historical re-derivability**

   

   Current structured ids are mutable.

   Options:

   - snapshot cited bronze under `reports/<run>/bronze/`,
   - version structured ids,
   - content-address structured artifacts.

7. **Held-out retrieval relevance set**

   

   Create approximately 30 independently judged question/document pairs once more than two tickers have mature corpora.

8. **Knowledge-graph evolution**

   

   Preserve compatibility with future graph-backed structured knowledge.

9. **Full financial canonicalization**

   

   Future work may include:

   - restatement tracking,
   - constructed TTM,
   - GAAP-to-adjusted bridges,
   - FX conversion.

   None are part of this implementation.