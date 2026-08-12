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

# §15.2's two permitted scripts.
EXPECTED = ("write_wave.js", "polish_chain.js")
POLISH_PROMPTS = ROOT / "prompts" / "polish"


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


def test_polish_chain_meta_phases_match_its_phase_calls():
    """Progress groups are matched by exact title; a phase() with no meta entry
    gets its own ungrouped box, and a meta entry with no phase() is a group that
    never appears."""
    text = source("polish_chain.js")
    declared = set(re.findall(r"title:\s*'([^']+)'", meta_block(text)))
    used = set(re.findall(r"phase:\s*'([^']+)'", text))
    used |= set(re.findall(r"^\s*phase\('([^']+)'\)", text, re.M))
    assert used <= declared, f"undeclared phases {sorted(used - declared)}"
    assert declared <= used, f"unused phases {sorted(declared - used)}"


def test_write_wave_meta_phases_are_the_seven_section_titles():
    """The write wave groups progress by SECTION, not by stage, so its phase
    titles come from `section.title` at runtime and cannot be read out of the
    source. What is checkable — and what actually breaks the display — is that
    `meta.phases` names exactly the seven titles sections.yaml defines."""
    from lib.sections import load_sections

    declared = set(re.findall(r"title:\s*'([^']+)'",
                              meta_block(source("write_wave.js"))))
    titles = {cfg["title"] for cfg in load_sections()["sections"].values()}
    assert declared == titles


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

def test_write_wave_runs_each_section_as_its_own_chain():
    """§15.1's chain, expressed as seven independent chains launched together
    rather than three stages over seven sections. Sections share no state, so
    nothing one does can hold up another, and valuation can be rewriting while
    risk_news still drafts."""
    text = source("write_wave.js")
    assert "parallel(" in text
    assert re.search(r"async function runSection\(section\)", text)
    for label in ("write:", "critic:", "rewrite:"):
        assert f"`{label}" in text, label
    assert "pipeline(" not in text, "stages are per-section, not global"


def test_write_wave_orders_sections_longest_first():
    """The harness caps concurrency below the section count, so one section
    always queues. It should be the cheapest one, not the 2700-word one."""
    text = source("write_wave.js")
    assert re.search(r"sort\(\s*\n?\s*\(a, b\) => \(b\.word_target \|\| 0\) - "
                     r"\(a\.word_target \|\| 0\)\)", text)
    assert "ordered.map((section) => () => runSection(section))" in text


def test_write_wave_groups_progress_by_section():
    """One phase per section, so the display shows what is actually true: seven
    chains at different stages, not three stages waiting on each other."""
    text = source("write_wave.js")
    assert "const phase = section.title" in text
    assert text.count("phase,") >= 3          # passed to all three agents


def test_write_wave_writes_where_the_spec_says():
    assert "reports/${report_date}/sections/${section.id}.md" in \
        source("write_wave.js")


def test_write_wave_uses_absolute_paths_in_every_prompt():
    """A subagent's working directory is not guaranteed, so a relative draft
    path lands somewhere nobody looks."""
    text = source("write_wave.js")
    assert "${workdir}/reports/" in text
    assert "workdir" in text


def test_write_wave_builds_every_prompt_from_the_original_section():
    """A stage whose predecessor died returns `null`; building the next prompt
    out of that result would drop the whole section instead of just that
    stage."""
    text = source("write_wave.js")
    for builder in ("writePrompt(section)", "criticPrompt(section)",
                    "rewritePrompt(section)"):
        assert builder in text, builder
    # And a dead rewrite still leaves the draft to account for.
    assert "const final = rewrite || draft" in text


def test_write_wave_gives_the_critic_a_schema():
    """Without one the critique came back as an unparsed string and nothing
    downstream could count what it found."""
    text = source("write_wave.js")
    assert "CRITIQUE_SCHEMA" in text
    assert "schema: CRITIQUE_SCHEMA" in text
    assert "blocking" in text


def test_write_wave_makes_every_agent_write_a_task_log():
    """A workflow script has no filesystem and §15.2 bans Date.now(), so the
    only account of what an agent did is the one the agent writes (§23.4)."""
    text = source("write_wave.js")
    assert "taskLogContract" in text
    assert "date -u +%Y-%m-%dT%H:%M:%SZ" in text
    assert "/log/" in text
    for stage in ("section-write", "section-critic", "section-rewrite"):
        assert stage in text, stage
    assert "Write the log even when the work failed" in text


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


# --- the polish chain's own contract ----------------------------------------

def test_polish_chain_runs_the_five_stages_sequentially():
    """§15.2's order. Unlike the write wave nothing here is parallelizable —
    every stage reads what the previous one wrote, and the polish stage
    consumes both worklists."""
    text = source("polish_chain.js")
    order = ["Cross-check", "Conclusion", "Critique", "Polish", "Evaluate"]
    positions = [text.index(f"phase('{name}')") for name in order]
    assert positions == sorted(positions)
    assert "pipeline(" not in text, "the stages are sequential, not a pipeline"
    assert "parallel(" not in text


def test_every_polish_stage_has_its_prompt_file():
    """A stage naming a prompt that does not exist fails mid-chain, after the
    conclusion has already been written."""
    text = source("polish_chain.js")
    named = set(re.findall(r"fill\('([a-z_]+\.md)'", text))
    assert named == {"cross_section.md", "conclusion.md", "critique.md",
                     "polish.md", "evaluate.md"}
    for prompt in named:
        assert (POLISH_PROMPTS / prompt).exists(), prompt


def test_polish_chain_writes_the_spec_artifacts():
    text = source("polish_chain.js")
    for artifact in ("verdict.json", "evaluation.json", "cross_check.json",
                     "conclusion.md"):
        assert artifact in text, artifact


def test_polish_chain_gives_the_shrink_gate_a_baseline():
    """`not_longer_than` needs a pre-polish copy to measure against; without
    one the gate silently disappears — which is exactly how sra5's polish pass
    grew a body by 1,933 bytes undetected."""
    text = source("polish_chain.js")
    assert "sections_prepolish" in text
    assert "baseline_dir" in text
    assert "shrink_gate_passed" in text
    assert re.search(r"log\('shrink gate FAILED", text)


def test_polish_chain_does_not_compute_the_implied_return():
    """§15.3: the driver recalculates it and must not trust model arithmetic.
    A workflow doing the sum here would be a second, competing source of a
    number that appears on the front-page card."""
    text = source("polish_chain.js")
    code = "\n".join(line for line in text.splitlines()
                     if not line.strip().startswith("//"))
    assert "fair_value /" not in code
    assert "current_price" not in code or "fair_value" not in code.split(
        "current_price")[1][:200]
    assert "driver recomputes" in text      # and the comment says whose it is


def test_polish_chain_takes_the_same_args_as_the_write_wave():
    """§15.2 lists one arg set for both scripts, so an orchestrator can build
    it once.

    The destructured expression is `input`, not `args`, because both scripts
    normalize a JSON-string `args` first — see the test below. What §15.2
    constrains is the set of names, not the identifier they are read from.
    """
    for name in EXPECTED:
        destructure = re.search(r"const \{([^}]+)\} = input", source(name))
        assert destructure, name
        names = {n.strip() for n in destructure.group(1).split(",")}
        assert {"ticker", "workdir", "report_date", "sections",
                "char_caps"} <= names, name


def test_both_scripts_tolerate_args_arriving_as_a_json_string():
    """Some harness builds deliver `args` as a JSON string. Destructuring that
    directly yields undefined for every field and fails opaquely at the first
    use — which is exactly how it failed during the live PANW build. Both
    scripts normalize before destructuring."""
    for name in EXPECTED:
        text = source(name)
        assert re.search(r"typeof args === 'string'\s*\?\s*JSON\.parse\(args\)"
                         r"\s*:\s*args", text), name


def test_polish_chain_task_logs_use_the_spec_purpose_vocabulary():
    """The progress label is `cross-check`; §23.4's purpose is `cross-section`.
    A log written under the label would be rejected by `record_subagent` and
    would join to nothing in the run log."""
    from lib.run_stats import PURPOSES

    text = source("polish_chain.js")
    assert "taskLogContract" in text
    block = text[text.index("const LOG_PURPOSE = {"):]
    block = block[:block.index("}")]
    declared = set(re.findall(r"'([a-z-]+)'", block))
    assert declared == {"cross-section", "conclusion", "critique", "polish",
                        "evaluate"}
    assert declared <= set(PURPOSES)


def test_polish_prompts_forbid_smoothing_a_genuine_tension():
    """§18.3: polish preserves genuine analytical tensions. An editing instinct
    removes them first, and they are the report's most useful content."""
    polish = (POLISH_PROMPTS / "polish.md").read_text(encoding="utf-8")
    cross = (POLISH_PROMPTS / "cross_section.md").read_text(encoding="utf-8")
    assert "genuine_tension" in polish and "genuine_tension" in cross
    assert re.search(r"[Dd]o not smooth away", polish)


def test_polish_prompt_states_the_shrink_gate_in_words():
    polish = (POLISH_PROMPTS / "polish.md").read_text(encoding="utf-8")
    assert "not_longer_than" in polish
    assert "WORDS" in polish
    assert "1,933" in polish        # the regression the gate exists for


def test_conclusion_prompt_requires_every_verdict_field():
    """§15.3's field list is what the front-page card renders."""
    from lib.render.assemble import VERDICT_FIELDS

    body = (POLISH_PROMPTS / "conclusion.md").read_text(encoding="utf-8")
    for field in VERDICT_FIELDS:
        assert f'"{field}"' in body, field


def test_conclusion_prompt_warns_that_the_driver_overwrites_the_arithmetic():
    body = (POLISH_PROMPTS / "conclusion.md").read_text(encoding="utf-8")
    assert "recalculates" in body
    assert "implied_return_pct" in body


def test_evaluate_prompt_scores_six_dimensions_with_spot_checks():
    body = (POLISH_PROMPTS / "evaluate.md").read_text(encoding="utf-8")
    for dimension in ("factual_accuracy", "completeness", "consistency",
                      "analytical_depth", "actionability", "source_attribution"):
        assert dimension in body, dimension
    assert "overall_score" in body
    assert "spot_checks" in body
    assert "ten" in body


def test_polish_prompts_do_no_independent_retrieval():
    """Same rule as the writers (§15.1): a fact discovered now has no bronze id
    and cannot survive assembly."""
    text = source("polish_chain.js")
    assert re.search(r"[Dd]o no independent retrieval", text)
