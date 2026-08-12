---
name: sra-lint
description: Model-judgment lint over the wiki — does each cited source actually support its claim, and is each claimed tension genuine. Runs after the deterministic wiki-lint and turns its findings into ledger questions. Use when asked to lint, audit or sanity-check a ticker's research notes before writing.
---

# sra-lint — the two judgments a program cannot make (§22.1)

`sra.py wiki-lint` already catches everything mechanical: uncited numbers,
untagged forward-looking values, ownership breaches, duplicate figures, broken
`built_from` links, unindexed entity pages. §22.1 limits model judgment to
exactly two questions, and this skill is the only place they are asked:

1. does each cited source actually support the claim it is attached to,
2. is each claimed tension genuine.

One subagent. Its findings become ledger questions with `--origin lint`; nothing
here edits a wiki page.

## Usage

`/sra-lint TICKER`

## Step 1 — Deterministic lint first, and it is not optional (Bash)

```bash
uv run python sra.py wiki-lint <TICKER>
```

§22.1: "`/sra-lint` runs only after deterministic lint." The order is what keeps
the model out of work a regex already does — and its output tells the judge
which pages are already known to be shaky. `wiki-lint` is advisory and always
exits 0, so read the findings rather than the exit code.

If it reports nothing and the wiki has fewer than three pages, stop: there is
not enough written down to audit. Run `/sra-research` first.

## Step 2 — Judge (ONE subagent)

Dispatch via the Agent tool with `subagent_type: "sra-writer"` — this is
judgment over local files with `sra.py show` for the sources, and needs no web
or MCP — and this prompt:

> Read `<repo>/prompts/lint/judgment.md`. It is your instructions; follow it.
>
> `{workdir}` = `<abs path to data/<TICKER>>`
> `{findings_path}` = `<abs path to data/<TICKER>/derived/lint_findings.json>`
>
> The deterministic lint has already run. Its findings, for context on which
> pages are already known to be weak:
>
> ```json
> <paste the wiki-lint output here>
> ```
>
> Read the wiki, open the sources you check, and write the findings file in
> exactly the shape the prompt specifies. Do not edit any wiki page and do not
> add questions — the driver owns the ledger.
>
> Your task log (§23.4): stamp `date -u +%Y-%m-%dT%H:%M:%SZ` before you start
> and again when you finish, then write ONE log to
> `<abs run dir>/log/<NN>_lint_judgment.md` with frontmatter `purpose: lint`,
> `section: null`, `round: 1`, `label: "lint:judgment"`, `started_at`,
> `finished_at`, `status`, `outputs`, and body headings `## Inputs`,
> `## Commands`, `## Outputs`, `## Notes`. In `## Notes`, say which citations
> you actually opened and which you took on trust — that is the one thing the
> findings file cannot record. Write the log even if the judgment failed.

`derived/lint_findings.json` is silver working state (§4.2): durable, never
citable, and safe to overwrite on the next lint.

## Step 3 — Turn findings into questions (Bash)

Every finding that carries a `question` becomes a ledger entry, one call per
section:

```bash
uv run python sra.py add-questions <TICKER> --section <SECTION> --origin lint \
  --question "<question>" --question "<question>"
```

`--origin lint` is what makes these traceable later — §23.4's purpose
vocabulary is the same set of names a question's `origin` uses, so a question
raised by this audit is distinguishable from a seed question forever.

Capture is idempotent (identity is `sha1(section|question)`), so re-running the
lint after a research round does not duplicate what is already open. It is also
never refused for volume — a large open set is a backlog signal, not an error.

## Step 4 — Bookkeeping (Bash)

```bash
uv run python sra.py wiki-index <TICKER>   # the lint pass edits pages; the index must follow
uv run python sra.py wiki-log <TICKER> \
    --entry "lint: <c> citations and <t> tensions checked, <n> findings, <q> questions raised" \
    --agents <N> --tokens <T> --minutes <M> --run <RUN>
```

## Report

Say how many citations and tensions were actually opened and read (the judge
reports this in `checked` — quote its number, do not estimate), the count by
verdict, and the two or three findings that would change the report if they
hold. Name any page where a claim is `unsupported` or `wrong-layer`: those are
attribution defects, which is the failure mode this whole system exists to
prevent (§1.2), and they are worth more of your report than a list of `partial`
citations.

If the judge raised no questions, say so plainly rather than manufacturing a
recommendation.
