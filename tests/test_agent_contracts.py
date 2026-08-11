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


def researcher_text() -> str:
    return RESEARCHER.read_text(encoding="utf-8")


def python_blocks(text: str) -> list[str]:
    return re.findall(r"^```python\n(.*?)^```", text, re.M | re.S)


def registered_subcommands() -> set[str]:
    import sra

    return set(sra.build_parser()._subparsers._group_actions[0].choices)


def named_subcommands(text: str) -> set[str]:
    return set(re.findall(r"sra\.py ([a-z][a-z-]+)", text))


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
