"""Static checks on the two checked-in Workflow scripts (spec §15.2).

A workflow script is executed by the harness, not by pytest, so nothing here
runs it. What these tests can do is catch the failure modes that would only
surface mid-build, when a 21-agent wave is already running:

- `export const meta` missing or computed — the harness requires a pure literal
  and rejects the script outright;
- TypeScript syntax, which does not parse;
- `Date.now()` / `Math.random()` / `new Date()`, which the runtime forbids
  because they would break resume;
- an `agentType` naming an agent file that does not exist.

The parse is deliberately shallow — regex over source, not a JS engine. It is
enough to catch every one of the above, and a real parser would be a dependency
this repo does not otherwise need.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / "workflows"
AGENTS = ROOT / ".claude" / "agents"

# Files that must exist. Added to as Phase 11 lands the polish chain.
EXPECTED = ("write_wave.js",)


def source(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def workflow_files() -> list[str]:
    return sorted(p.name for p in WORKFLOWS.glob("*.js"))


def meta_block(text: str) -> str:
    """The `export const meta = {...}` object literal, braces balanced."""
    start = text.index("export const meta = {")
    depth, i = 0, text.index("{", start)
    for end in range(i, len(text)):
        if text[end] == "{":
            depth += 1
        elif text[end] == "}":
            depth -= 1
            if depth == 0:
                return text[i:end + 1]
    raise AssertionError("unbalanced meta literal")


# --- the files themselves ---------------------------------------------------

def test_the_expected_workflows_exist():
    assert set(EXPECTED) <= set(workflow_files())


def test_only_the_permitted_workflows_are_checked_in():
    """§15.2: "Two checked-in Workflow scripts are permitted." Model work
    belongs in skills; a third script here is scope creeping into the driver."""
    assert set(workflow_files()) <= {"write_wave.js", "polish_chain.js"}


@pytest.mark.parametrize("name", EXPECTED)
def test_meta_is_the_first_export_and_a_pure_literal(name):
    """The harness parses `meta` before running anything, so a computed value —
    a variable, a call, a template interpolation — fails the whole script."""
    text = source(name)
    exports = re.findall(r"^export\s+(?:const|function|default)\s+(\w+)?",
                         text, re.M)
    assert exports and exports[0] == "meta"

    literal = meta_block(text)
    assert "${" not in literal, "template interpolation in meta"
    assert "..." not in literal, "spread in meta"
    # Only the STRUCTURE has to be literal — the string values may say anything,
    # so they are removed before looking for a call or a bare identifier.
    skeleton = re.sub(r"'(?:[^'\\]|\\.)*'", "''", literal)
    assert "(" not in skeleton, "function call in meta"
    values = re.findall(r":\s*([^,\n}]+)", skeleton)
    for value in values:
        value = value.strip()
        assert value in ("''", "[", "[]", "") or value.startswith("["), \
            f"non-literal meta value: {value!r}"


@pytest.mark.parametrize("name", EXPECTED)
def test_meta_has_the_required_fields(name):
    literal = meta_block(source(name))
    assert re.search(r"\bname:\s*'[a-z0-9-]+'", literal)
    assert re.search(r"\bdescription:\s*'", literal)


@pytest.mark.parametrize("name", EXPECTED)
def test_meta_phases_match_the_phase_calls(name):
    """Progress groups are matched by exact title; a phase() with no meta entry
    gets its own ungrouped box, and a meta entry with no phase() is a group that
    never appears."""
    text = source(name)
    declared = set(re.findall(r"title:\s*'([^']+)'", meta_block(text)))
    used = set(re.findall(r"phase:\s*'([^']+)'", text))
    used |= set(re.findall(r"^\s*phase\('([^']+)'\)", text, re.M))
    assert used <= declared, f"{name}: undeclared phases {sorted(used - declared)}"
    assert declared <= used, f"{name}: unused phases {sorted(declared - used)}"


# --- runtime constraints ----------------------------------------------------

@pytest.mark.parametrize("name", EXPECTED)
@pytest.mark.parametrize("forbidden", ["Date.now(", "Math.random(", "new Date("])
def test_no_nondeterminism(name, forbidden):
    """These throw at runtime: a resumed run replays cached agent results, and
    a script that read the clock would take a different path the second time."""
    assert forbidden not in source(name)


@pytest.mark.parametrize("name", EXPECTED)
def test_no_typescript_syntax(name):
    """Scripts are plain JS — a type annotation is a syntax error, and the only
    signal is the whole workflow failing to start."""
    text = source(name)
    for pattern in (r"^\s*interface\s+\w+", r"^\s*type\s+\w+\s*=",
                    r":\s*(string|number|boolean)\s*[,)=]", r"\bas\s+\w+\s*;",
                    r"function\s+\w+\([^)]*:\s*\w+"):
        assert not re.search(pattern, text, re.M), f"{name}: {pattern}"


@pytest.mark.parametrize("name", EXPECTED)
def test_no_filesystem_or_node_apis(name):
    """Workflow scripts have no filesystem. Every agent reads its own inputs
    off disk instead, which is also what keeps one copy of each prompt."""
    text = source(name)
    for forbidden in ("require(", "readFileSync", "import fs", "process.env"):
        assert forbidden not in text, f"{name}: {forbidden}"


@pytest.mark.parametrize("name", EXPECTED)
def test_every_agent_type_names_an_agent_that_exists(name):
    named = set(re.findall(r"agentType:\s*'([a-z-]+)'", source(name)))
    assert named, f"{name}: no agentType declared"
    for agent in named:
        assert (AGENTS / f"{agent}.md").exists(), f"{name}: missing {agent}"


@pytest.mark.parametrize("name", EXPECTED)
def test_the_script_actually_parses_as_javascript(name):
    """The regex checks above catch known traps; this catches the unknown ones.

    Skipped when node is absent — it is not a project dependency, and the
    regex checks are the portable floor. The body is wrapped in an async
    function first because the runtime supplies that context, which is what
    makes the script's top-level `await` and `return` legal.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")

    body = source(name).replace("export const meta =", "const meta =", 1)
    wrapped = (
        "const args={},log=()=>{},agent=async()=>({}),"
        "pipeline=async()=>[],parallel=async()=>[],budget={};\n"
        f"async function __wf() {{\n{body}\n}}\n"
    )
    result = subprocess.run([node, "--input-type=module", "--check"],
                            input=wrapped, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# --- the write wave's own contract ------------------------------------------

def test_write_wave_runs_all_three_stages_as_a_pipeline():
    """§15.1's chain, and a pipeline rather than three barriers: sections are
    independent, so valuation can be rewriting while risk_news still drafts."""
    text = source("write_wave.js")
    assert "pipeline(" in text
    for label in ("write:", "critic:", "rewrite:"):
        assert f"`{label}" in text, label
    assert "parallel(" not in text, "a barrier would idle every fast section"


def test_write_wave_writes_where_the_spec_says():
    assert "reports/${report_date}/sections/${section.id}.md" in \
        source("write_wave.js")


def test_write_wave_uses_absolute_paths_in_every_prompt():
    """A subagent's working directory is not guaranteed, so a relative draft
    path lands somewhere nobody looks."""
    text = source("write_wave.js")
    assert "${workdir}/reports/" in text
    assert "workdir" in text


def test_write_wave_takes_its_stage_input_from_the_original_section():
    """A stage whose predecessor died gets `null`; rebuilding the prompt from it
    would drop the whole section instead of just that stage."""
    text = source("write_wave.js")
    assert "(_draft, section) =>" in text
    assert "(_critique, section) =>" in text


def test_write_wave_surfaces_failed_hard_checks():
    """A section whose checks failed still has a file on disk, and the
    assembler would embed it without knowing."""
    text = source("write_wave.js")
    assert "hard_checks_passed" in text
    assert "hard_checks_failed" in text
    assert re.search(r"log\(`hard checks FAILED", text)


def test_write_wave_returns_a_summary_the_orchestrator_can_act_on():
    text = source("write_wave.js")
    assert re.search(r"return\s*\{", text)
    assert "sections: written" in text


def test_write_wave_reads_prompts_and_config_from_disk_not_from_the_script():
    """One copy of each prompt: §18.1 owns section content, STYLE.md owns prose,
    prompts/write owns the process. A script that inlined any of them would be
    a fourth copy nobody updates."""
    text = source("write_wave.js")
    assert "prompts/write/_shared.md" in text
    assert "prompts/write/${section.id}.md" in text
    assert "STYLE.md" in text
    assert "load_sections()" in text


def test_write_wave_stage_schema_is_valid_json_schema():
    """The schema is enforced at the tool-call layer, so a malformed one fails
    every agent in the wave rather than one."""
    text = source("write_wave.js")
    block = text[text.index("const STAGE_SCHEMA = {"):]
    block = block[block.index("{"):block.index("\n}\n") + 2]
    # Quote the JS identifiers so the literal parses as JSON.
    as_json = re.sub(r"(\w+):", r'"\1":', block).replace("'", '"')
    as_json = re.sub(r",(\s*[}\]])", r"\1", as_json)
    schema = json.loads(as_json)
    assert schema["type"] == "object"
    assert "section" in schema["required"]
    assert "hard_checks_passed" in schema["required"]
