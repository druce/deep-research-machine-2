"""`/sra-assemble` must stay consistent with the code it drives (§15.3, §16.4, §23.2).

A skill file is instructions to a model, so nothing enforces it at runtime: a
renamed subcommand or a phase order that drifted fails mid-build as a confusing
agent error. These tests are the enforcement.
"""
from __future__ import annotations

import re
from pathlib import Path

import frontmatter

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "sra-assemble" / "SKILL.md"
POLISH_CHAIN = ROOT / "workflows" / "polish_chain.js"


def skill_body() -> str:
    return SKILL.read_text(encoding="utf-8")


def flat() -> str:
    """The skill with line wrapping collapsed, so a phrase that happens to sit
    across a line break still matches."""
    return " ".join(skill_body().split())


def test_skill_exists_with_frontmatter():
    post = frontmatter.load(SKILL)
    assert post.metadata["name"] == "sra-assemble"
    assert post.metadata["description"].strip()


def test_every_sra_subcommand_the_skill_names_is_registered():
    import sra

    registered = set(sra.build_parser()._subparsers._group_actions[0].choices)
    named = set(re.findall(r"sra\.py ([a-z][a-z-]+)", skill_body()))
    assert named <= registered, f"unregistered: {sorted(named - registered)}"


def test_skill_states_the_phase_order():
    """§16.4: charts before chart selection, selection before assembly,
    assembly before snapshot. Each wrong order fails silently."""
    block = re.search(r"```text\n(write wave.*?)```", skill_body(), re.S)
    assert block, "the skill must state §16.4's order as a block"
    steps = block.group(1)
    order = ["write wave", "polish chain", "sra.py charts T",
             "sra.py charts T --verdict", "/sra-chartbook", "sra.py assemble T",
             "sra.py validate T", "sra.py snapshot T"]
    positions = [steps.index(step) for step in order]
    assert positions == sorted(positions)


def test_skill_walks_the_steps_in_the_order_it_states():
    """The prose steps must not contradict the block above them."""
    body = skill_body()
    steps = ["## Step 1", "## Step 2", "## Step 3", "## Step 4"]
    assert [body.index(s) for s in steps] == sorted(body.index(s) for s in steps)
    assert body.index("/sra-chartbook TICKER") < body.index("sra.py assemble <TICKER>")
    assert body.index("sra.py assemble <TICKER>") < body.index("sra.py snapshot <TICKER>")


def test_skill_carries_the_dirty_section_threshold():
    """§23.2: three or more dirty sections earns the full chain; fewer earns
    the cross-section check plus conclusion/verdict."""
    body = skill_body()
    assert "3 or more" in body and "fewer than 3" in body
    assert "cross_section" in body and "conclusion" in body


def test_the_reduced_shape_the_skill_asks_for_is_one_the_chain_supports():
    """The skill tells the orchestrator to pass `stages`; a chain that ignored
    it would silently run all five and blow the incremental budget."""
    chain = POLISH_CHAIN.read_text(encoding="utf-8")
    # `input` is `args` after the JSON-string normalization at the top of the
    # script; what matters is that the subset is read, not which name it is
    # read from.
    assert "input.stages" in chain
    for stage in ("cross_section", "conclusion", "critique", "polish", "evaluate"):
        assert f"'{stage}'" in chain, stage
    assert 'stages: ["cross_section", "conclusion"]' in skill_body()


def test_skill_says_validate_is_fatal_and_has_no_force():
    body = flat()
    assert "no `--force`" in body
    assert "Do not snapshot over a failing gate" in body


def test_skill_explains_the_immutable_run_rule():
    """A snapshotted run is never written into again — that is what makes the
    §23.3 incremental gate checkable."""
    body = skill_body()
    assert "snapshot.json" in body
    assert "immutable" in body
    assert "<date>_2" in body


def test_skill_distinguishes_a_contract_failure_from_a_render_failure():
    """§22.3: exit 1 is a build defect; a degraded pandoc/weasyprint is a
    reported error over a report that still exists."""
    body = skill_body()
    assert "render_errors" in body
    assert "exit 1" in body
    assert "§22.3" in body


def test_skill_does_not_hand_write_the_chartbook():
    assert "Do not hand-write that file" in skill_body()
