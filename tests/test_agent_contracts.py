"""The research agent and the skills that drive it must match the code they call.

An agent file is instructions to a model: nothing enforces it at runtime, so a
renamed command, a moved directory or a dropped safety rule fails mid-build as a
confusing agent error — or, worse, quietly writes model prose where evidence
belongs. These tests are the enforcement. They assert the privilege mitigations
of §21 are actually present in the file, that every `sra.py` subcommand named
exists, and that the answer-writing snippet the agent is told to run still
matches `SourceMeta`.

Companion to `tests/test_peers_skill_contract.py`, which does the same for the
peer-selection skill, agent and rubric.
"""
from __future__ import annotations

import ast
import re
from dataclasses import MISSING, fields
from pathlib import Path

import frontmatter
import pytest

from lib.provenance import MODEL_KINDS, SourceMeta

ROOT = Path(__file__).resolve().parent.parent
RESEARCHER = ROOT / ".claude" / "agents" / "sra-researcher.md"
WRITER = ROOT / ".claude" / "agents" / "sra-writer.md"
RESEARCH_SKILL = ROOT / ".claude" / "skills" / "sra-research" / "SKILL.md"
PREFETCH_SKILL = ROOT / ".claude" / "skills" / "sra-prefetch" / "SKILL.md"
CHARTBOOK_SKILL = ROOT / ".claude" / "skills" / "sra-chartbook" / "SKILL.md"
CHARTBOOK_PROMPT = ROOT / "prompts" / "chartbook.md"
TOPIC_PROMPTS = ROOT / "prompts" / "prefetch_research"


def researcher_text() -> str:
    return RESEARCHER.read_text(encoding="utf-8")


def research_skill_text() -> str:
    return RESEARCH_SKILL.read_text(encoding="utf-8")


def prefetch_skill_text() -> str:
    return PREFETCH_SKILL.read_text(encoding="utf-8")


def python_blocks(text: str) -> list[str]:
    return re.findall(r"^```python\n(.*?)^```", text, re.M | re.S)


def registered_subcommands() -> set[str]:
    import sra

    return set(sra.build_parser()._subparsers._group_actions[0].choices)


def named_subcommands(text: str) -> set[str]:
    return set(re.findall(r"sra\.py ([a-z][a-z-]+)", text))


def bash_subcommands(text: str) -> set[str]:
    """Subcommands a skill tells you to RUN, from its ```bash fences only.

    Narrower than `named_subcommands` on purpose: a skill may legitimately
    describe a phase ordering that names commands owned by a later phase, and
    that prose is documentation rather than an instruction. What must exist
    today is what the steps actually execute.
    """
    return {command
            for block in re.findall(r"^```bash\n(.*?)^```", text, re.M | re.S)
            for command in re.findall(r"sra\.py ([a-z][a-z-]+)", block)}


# --- the file itself -------------------------------------------------------

def test_researcher_agent_exists_with_frontmatter():
    post = frontmatter.load(RESEARCHER)
    assert post.metadata["name"] == "sra-researcher"
    assert post.metadata["description"].strip()


def test_researcher_declares_no_tools_allowlist():
    """§21: the researcher inherits MCP access only when no `tools:` allowlist
    is present — a custom agent type that declares one does not receive the
    session's MCP servers in this Claude Code build. Research depends on MCP,
    so the omission is deliberate and the file has to say why."""
    assert "tools" not in frontmatter.load(RESEARCHER).metadata
    body = researcher_text()
    assert re.search(r"no\s+`?tools:?`?\s+allowlist|NO\s+`tools:`", body), \
        "the omission must be documented, or a later edit will 'fix' it"
    assert "MCP" in body


# --- retrieval: the commands and paths have to exist -----------------------

def test_researcher_names_the_retrieval_commands():
    """§14 step 2: researchers retrieve through manifest, grep and show."""
    body = researcher_text()
    for command in ("manifest", "grep", "show"):
        assert f"sra.py {command}" in body, command


def test_every_sra_subcommand_the_researcher_names_is_registered():
    named = named_subcommands(researcher_text())
    registered = registered_subcommands()
    assert named <= registered, f"unregistered: {sorted(named - registered)}"


def test_researcher_does_not_reference_retired_retrieval():
    """`search`, `ingest` and the LanceDB index are retired (CLAUDE.md); the
    ported EXP agent told researchers to use exactly those."""
    body = researcher_text()
    for retired in ("sra.py search", "sra.py ingest", "search_index", "LanceDB"):
        assert retired not in body, retired


# --- the answer contract ---------------------------------------------------

def test_researcher_writes_answers_to_silver_only():
    """§1.2's defect: a `research_answer` landing in `sources/` becomes citable
    evidence, so a report citation can terminate at model-generated text."""
    body = researcher_text()
    assert "derived/answers/" in body
    assert "write_answer" in body
    assert re.search(r"never .{0,40}sources/|not .{0,40}sources/", body), \
        "the agent must be told not to write into sources/"


def test_researcher_answer_snippet_matches_source_meta():
    """The snippet is copied verbatim by a model that cannot see the dataclass,
    so a renamed or newly-required `SourceMeta` field would surface as a
    TypeError mid-research. Fail here instead."""
    calls = [
        node
        for block in python_blocks(researcher_text())
        for node in ast.walk(ast.parse(block))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "SourceMeta"
    ]
    assert len(calls) == 1, "expected exactly one SourceMeta call in the snippet"

    passed = {kw.arg for kw in calls[0].keywords if kw.arg}
    known = {f.name for f in fields(SourceMeta)}
    assert passed <= known, f"unknown SourceMeta fields: {sorted(passed - known)}"

    required = {f.name for f in fields(SourceMeta)
                if f.default is MISSING and f.default_factory is MISSING}
    assert required <= passed, f"missing required fields: {sorted(required - passed)}"

    kinds = {kw.value.value for kw in calls[0].keywords
             if kw.arg == "kind" and isinstance(kw.value, ast.Constant)}
    assert kinds <= MODEL_KINDS, f"answer kind must be a MODEL_KIND, got {kinds}"


def test_researcher_snippet_is_run_as_a_heredoc_from_the_repo_root():
    """`from lib.provenance import ...` only resolves from the repo root, and
    the body is piped in rather than inlined because research prose is full of
    quotes and backticks."""
    body = researcher_text()
    assert re.search(r"uv run python - <<", body)


def test_researcher_records_every_url_it_used():
    """§8.3: `fetch-urls` harvests exactly `cited_urls`. A URL cited in the body
    but missing from frontmatter never becomes bronze, so the claim resting on
    it is unciteable and the synthesizer has to drop it."""
    body = researcher_text()
    assert "cited_urls" in body
    assert "fetch-urls" in body


# --- §21 privilege mitigations ---------------------------------------------

@pytest.mark.parametrize("pattern", [
    r"untrusted",                      # retrieved material is untrusted data
    r"instructions .{0,60}(fetched|retrieved|content)",  # never follow them
    r"\.env",                          # never read credential files
    r"environment variable",           # never echo them
    r"fetch-urls",                     # bulk fetching is the driver's job
])
def test_researcher_carries_the_privilege_mitigations(pattern):
    """§21: the researcher has broad local capability by construction, and
    these five sentences are the whole mitigation. Dropping one in an edit is
    exactly the regression worth failing a test over."""
    assert re.search(pattern, researcher_text(), re.I), pattern


# --- the synthesizer agent -------------------------------------------------

def test_writer_agent_exists_with_frontmatter():
    post = frontmatter.load(WRITER)
    assert post.metadata["name"] == "sra-writer"
    assert post.metadata["description"].strip()


def test_writer_has_exactly_the_tools_the_spec_gives_it():
    """§21's agent table: Read, Write, Edit, Glob, Grep, Bash — Bash because
    all ledger and wiki bookkeeping runs through `sra.py`. No web and no MCP:
    the synthesizer writes from evidence already gathered, and a claim it
    fetched itself is a claim no citation resolves to."""
    tools = {t.strip() for t in frontmatter.load(WRITER).metadata["tools"].split(",")}
    assert tools == {"Read", "Write", "Edit", "Glob", "Grep", "Bash"}


def test_writer_never_cites_silver():
    """§8.2/§1.2: a citation that terminates in an answer file makes model
    output look like evidence."""
    body = WRITER.read_text(encoding="utf-8")
    assert re.search(r"[Nn]ever cite an answer file", body)
    assert "derived/answers/" in body and ".urls.json" in body


def test_writer_bookkeeps_through_the_driver():
    """§3: a subagent that hand-edits questions.json or 00_index.md loses the
    edit the next time the driver rewrites the file."""
    body = WRITER.read_text(encoding="utf-8")
    for command in ("mark-answered", "drop-question", "add-questions"):
        assert command in body, command
    named = named_subcommands(body)
    assert named <= registered_subcommands(), \
        f"unregistered: {sorted(named - registered_subcommands())}"


# --- the research skill ----------------------------------------------------

def test_research_skill_exists_with_frontmatter():
    post = frontmatter.load(RESEARCH_SKILL)
    assert post.metadata["name"] == "sra-research"
    assert post.metadata["description"].strip()


def test_every_sra_subcommand_the_research_skill_names_is_registered():
    named = named_subcommands(research_skill_text())
    registered = registered_subcommands()
    assert named <= registered, f"unregistered: {sorted(named - registered)}"


def test_research_skill_dispatches_only_agents_that_exist():
    """A `subagent_type` naming a missing agent file fails mid-round."""
    agents_dir = ROOT / ".claude" / "agents"
    named = set(re.findall(r'subagent_type: "([a-z-]+)"', research_skill_text()))
    assert named, "the skill must name the agent types it dispatches"
    for agent in named:
        assert (agents_dir / f"{agent}.md").exists(), agent


def test_research_skill_runs_the_whole_loop():
    """§14's five steps: select, fan out, harvest, synthesize, stop."""
    body = research_skill_text()
    for command in ("questions", "add-questions", "record-attempt",
                    "fetch-urls", "manifest", "validate", "wiki-log"):
        assert f"sra.py {command}" in body, command


def test_research_skill_batches_through_the_driver():
    """§3: batch size and concurrency are library constants — a skill that
    grouped questions by hand would let them drift from `lib/research.py`."""
    body = research_skill_text()
    assert "batch_questions" in body
    assert "waves" in body
    assert "open_questions" in body


def test_research_skill_dispatches_reopened_questions_too():
    """`invalidate` reopens a question precisely so the next run re-answers it;
    a skill that filtered on `--status open` alone would strand it forever."""
    assert "reopened" in research_skill_text()


def test_research_skill_keeps_the_answerer_out_of_the_ledger():
    """§14.1: the answerer does not close questions, the synthesizer decides,
    and the driver defers by counting. Three actors, no overlap."""
    body = research_skill_text()
    assert re.search(r"never closes a question|does not close", body, re.I)
    assert re.search(r"only the synthesizer drops|only it marks", body, re.I)
    assert "MAX_ATTEMPTS" in body


def test_research_skill_harvests_before_it_synthesizes():
    """§14 step 3: harvest is a BARRIER — a synthesizer that ran first would
    have no bronze ids to cite."""
    body = research_skill_text()
    assert body.index("fetch-urls") < body.index("subagent_type: \"sra-writer\"")
    assert re.search(r"barrier", body, re.I)


def test_research_skill_forbids_citing_answers():
    body = research_skill_text()
    assert re.search(r"NEVER cite an answer file", body)
    assert "cited_urls" in body and ".urls.json" in body


# --- the prefetch skill ----------------------------------------------------

def test_prefetch_skill_exists_with_frontmatter():
    post = frontmatter.load(PREFETCH_SKILL)
    assert post.metadata["name"] == "sra-prefetch"
    assert post.metadata["description"].strip()


def test_every_sra_subcommand_the_prefetch_skill_names_is_registered():
    named = named_subcommands(prefetch_skill_text())
    registered = registered_subcommands()
    assert named <= registered, f"unregistered: {sorted(named - registered)}"


def test_prefetch_skill_runs_the_whole_flow():
    """§11: gather, macro, topics, harvest, manifest, gate."""
    body = prefetch_skill_text()
    for command in ("init", "prefetch", "prefetch-macro", "fetch-urls",
                    "manifest", "validate"):
        assert f"sra.py {command}" in body, command


def test_prefetch_skill_initializes_macro_before_gathering_it():
    """`prefetch-macro` exits 1 on an uninitialized `_MACRO`, and nothing else
    in a cold build creates that tree."""
    body = prefetch_skill_text()
    assert "sra.py init _MACRO" in body
    assert body.index("sra.py init _MACRO") < body.index("sra.py prefetch-macro")


def test_prefetch_skill_names_every_topic_prompt_that_exists():
    """§11.2's seven topics, matched against the prompt files themselves — a
    prompt added or renamed without touching the skill is a topic that never
    runs."""
    topics = {p.stem for p in TOPIC_PROMPTS.glob("*.md")}
    assert len(topics) == 7, sorted(topics)
    body = prefetch_skill_text()
    for topic in topics:
        assert topic in body, topic
    assert "prompts/prefetch_research/" in body


def test_prefetch_skill_has_a_fallback_for_the_workflow():
    """§11.2: `deep-research` is harness-provided, so it may not be there."""
    body = prefetch_skill_text()
    assert "deep-research" in body
    assert "sra-researcher" in body
    assert re.search(r"fallback", body, re.I)


def test_prefetch_skill_reads_prefetch_exit_codes():
    """A failed kind exits 2 and is degraded-but-continue; exit 1 means nothing
    ran. A skill that treated them alike would either abort a usable build or
    research against an empty corpus."""
    body = prefetch_skill_text()
    assert re.search(r"\bdegraded\b", body, re.I)
    assert "**2**" in body and "**1**" in body


def test_prefetch_answers_are_silver_and_carry_their_urls():
    """§11.2: the research answer is never evidence; its `cited_urls` are what
    `fetch-urls` turns into evidence."""
    body = prefetch_skill_text()
    assert "write_answer" in body
    assert "derived/answers/" in body
    assert "cited_urls" in body
    assert re.search(r"never .{0,20}evidence", body, re.I)


def test_chartbook_skill_exists_and_points_at_its_rubric():
    post = frontmatter.load(CHARTBOOK_SKILL)
    assert post.metadata["name"] == "sra-chartbook"
    assert post.metadata["description"].strip()
    assert CHARTBOOK_PROMPT.exists()
    assert "prompts/chartbook.md" in CHARTBOOK_SKILL.read_text(encoding="utf-8")


def test_every_sra_subcommand_the_chartbook_skill_runs_is_registered():
    """Scoped to the bash fences: the §16.4 phase-order block also names
    `sra.py assemble`, which Phase 12 owns and which this skill never runs."""
    named = bash_subcommands(CHARTBOOK_SKILL.read_text(encoding="utf-8"))
    assert named <= registered_subcommands(), \
        f"unregistered: {sorted(named - registered_subcommands())}"


def test_chartbook_skill_renders_both_passes_in_order():
    """§16.4: the verdict pass cannot run before the polish chain, and selecting
    from the first pass alone yields a chartbook missing exactly the exhibits
    the conclusion needs."""
    body = CHARTBOOK_SKILL.read_text(encoding="utf-8")
    plain = body.index("sra.py charts <TICKER>\n")
    with_verdict = body.index("sra.py charts <TICKER> --verdict")
    assert plain < with_verdict
    assert "verdict.json" in body


def test_chartbook_skill_dispatches_one_existing_agent():
    """§16.2: "uses one model subagent"."""
    body = CHARTBOOK_SKILL.read_text(encoding="utf-8")
    named = set(re.findall(r'subagent_type: "([a-z-]+)"', body))
    assert len(named) == 1
    for agent in named:
        assert (ROOT / ".claude" / "agents" / f"{agent}.md").exists(), agent


def test_chartbook_skill_verifies_the_selection_resolves():
    """A selected name with no PNG behind it is a hole in the assembled report."""
    body = CHARTBOOK_SKILL.read_text(encoding="utf-8")
    assert "chartbook.json" in body
    assert "missing_png" in body


def test_prefetch_answer_snippet_matches_source_meta():
    """Same guard as the researcher's: the snippet is copied verbatim by a
    model that cannot see the dataclass."""
    calls = [
        node
        for block in python_blocks(prefetch_skill_text())
        for node in ast.walk(ast.parse(block))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "SourceMeta"
    ]
    assert len(calls) == 1

    passed = {kw.arg for kw in calls[0].keywords if kw.arg}
    known = {f.name for f in fields(SourceMeta)}
    required = {f.name for f in fields(SourceMeta)
                if f.default is MISSING and f.default_factory is MISSING}
    assert passed <= known, f"unknown SourceMeta fields: {sorted(passed - known)}"
    assert required <= passed, f"missing required fields: {sorted(required - passed)}"
