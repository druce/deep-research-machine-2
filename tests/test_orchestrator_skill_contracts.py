"""`/sra-build` and `/sra-update` must match §23.1 and §23.2 exactly.

These two skills are the only place the whole pipeline's order lives. A step
that drifts here is a phase silently skipped or run twice in a build nobody
watches end to end, so every command, phase order and ceiling they name is
checked against the code and the spec.
"""
from __future__ import annotations

import re
from pathlib import Path

import frontmatter

from lib.fetchers.registry import FATAL_KINDS
from lib.peers_scoring import PEER_SET_SIZE  # noqa: F401  (imported for parity with §13)
from lib.run_stats import MAX_MINUTES, MAX_SUBAGENTS

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / ".claude" / "skills" / "sra-build" / "SKILL.md"
UPDATE = ROOT / ".claude" / "skills" / "sra-update" / "SKILL.md"
SKILLS_DIR = ROOT / ".claude" / "skills"


def body(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def flat(path: Path) -> str:
    return " ".join(body(path).split())


def registered_commands() -> set[str]:
    import sra

    return set(sra.build_parser()._subparsers._group_actions[0].choices)


# --- both -----------------------------------------------------------------

def test_skills_exist_with_frontmatter():
    for path, name in ((BUILD, "sra-build"), (UPDATE, "sra-update")):
        post = frontmatter.load(path)
        assert post.metadata["name"] == name
        assert post.metadata["description"].strip()


def test_every_sra_subcommand_named_is_registered():
    for path in (BUILD, UPDATE):
        named = set(re.findall(r"sra\.py ([a-z][a-z-]+)", body(path)))
        assert named <= registered_commands(), (
            f"{path.name}: unregistered {sorted(named - registered_commands())}")


def test_every_skill_invoked_exists():
    for path in (BUILD, UPDATE):
        for name in set(re.findall(r"/(sra-[a-z]+)", body(path))):
            assert (SKILLS_DIR / name / "SKILL.md").exists(), f"{path.name}: {name}"


def test_neither_skill_runs_a_workflow_through_the_shell():
    """Workflow scripts are driven by the Workflow tool; `node workflows/...`
    would fail, and only after the phases before it had already been paid for."""
    for path in (BUILD, UPDATE):
        assert "node workflows/" not in body(path), path.name


# --- sra-build: §23.1's eighteen steps ------------------------------------

def test_build_covers_every_phase_of_the_cold_build_in_order():
    text = body(BUILD)
    order = [
        "Which companies do you consider",     # 0. peers, once
        "sra.py init <TICKER>",                # 1
        "/sra-prefetch TICKER",                # 2
        "sra.py prefetch-macro",               # 3
        "sra.py manifest <TICKER>",            # 4
        "sra.py validate <TICKER>",            # 5
        "/sra-peers TICKER",                   # 6
        "/sra-research TICKER all",            # 7
        "sra.py wiki-lint <TICKER>",           # 8
        "/sra-lint TICKER",                    # 9
        "workflows/write_wave.js",             # 11
        "/sra-assemble TICKER",                # 12-18
    ]
    positions = [text.index(step) for step in order]
    assert positions == sorted(positions), "phases are out of §23.1 order"


def test_build_asks_for_peers_exactly_once_and_before_anything_else():
    text = body(BUILD)
    assert "exactly once" in text or "Ask about peers exactly once" in text
    assert text.index("## Step 0") < text.index("## Steps 1–2")
    assert "do not ask" in text.lower()


def test_build_delegates_the_tail_to_sra_assemble_rather_than_repeating_it():
    """Steps 12-18 are /sra-assemble's contract (§16.4). Running them here too
    would polish twice and snapshot an unvalidated run."""
    text = body(BUILD)
    for command in ("sra.py charts", "sra.py assemble", "sra.py snapshot"):
        assert f"uv run python {command}" not in text, command
    assert "Do not run those commands here as well" in text


def test_build_is_resumable():
    """§23.1: re-running skips completed fresh phases."""
    text = body(BUILD)
    assert text.count("**Resume:**") >= 3
    assert "skips completed fresh phases" in text


def test_build_names_the_minimum_viable_inputs_as_fatal():
    text = body(BUILD)
    for kind in FATAL_KINDS:
        assert f"`{kind}`" in text, kind
    assert "minimum viable input" in flat(BUILD)


def test_build_quotes_the_real_budget_ceilings():
    text = body(BUILD)
    assert str(MAX_SUBAGENTS) in text and f"{MAX_MINUTES} minutes" in text
    assert "check_budgets" in text


def test_build_records_agents_under_the_real_vocabulary():
    """`record_subagent` rejects an unknown purpose, so a skill naming one
    would fail mid-build."""
    from lib.run_stats import PURPOSES

    for purpose in re.findall(r'purpose="([a-z-]+)"', body(BUILD)):
        assert purpose in PURPOSES, purpose


def test_build_offers_the_length_presets_that_exist():
    from lib.sections import load_sections

    presets = load_sections()["length_presets"]
    text = body(BUILD)
    for name, scale in presets.items():
        assert name in text, name
        assert f"{scale:.2f}" in text, name


def test_build_knows_the_run_directory_rule():
    assert "no `snapshot.json`" in body(BUILD)


# --- sra-update: §23.2's three flows --------------------------------------

def test_update_carries_all_three_flows():
    text = body(UPDATE)
    for flow in ("bare refresh", "directed research", "report-only edit"):
        assert flow in text.lower(), flow


def test_update_never_asks_about_peers():
    """§23.2: no re-asking for peers."""
    text = flat(UPDATE)
    assert "Never ask about peers" in text
    assert "Which companies do you consider" not in text


def test_update_states_the_incremental_ceilings():
    text = body(UPDATE)
    assert "≤30 minutes" in text
    assert "≤8 model subagents" in text
    assert "max_subagents=8" in text and "max_minutes=30" in text


def test_update_distinguishes_the_spend_ceiling_from_the_wave_width():
    """They were one constant, so widening a wave would have silently doubled
    what §23.2 allows an incremental run to cost. The skill has to name which
    of the two its 8 is."""
    text = flat(UPDATE)
    assert "MAX_INCREMENTAL_SUBAGENTS" in text
    assert "MAX_PARALLEL_AGENTS" in text
    assert "SPEND limit" in text


def test_directed_instructions_come_from_the_shell_not_from_prose_splitting():
    """§3: instruction boundaries are the quotes the user typed. A model
    splitting prose is a model editing the request."""
    text = flat(UPDATE)
    assert "boundaries come from the shell" in text
    assert "never inferred by splitting prose" in text
    assert "--origin user" in text


def test_update_runs_invalidate_as_a_dry_run_first():
    text = body(UPDATE)
    assert text.index("# dry run first, always") < text.index("invalidate <TICKER> --apply")
    assert "without `--apply` is a dry run" in flat(UPDATE)


def test_update_checks_the_incremental_gate():
    """§23.3: after directed research, only affected section files may change."""
    text = body(UPDATE)
    assert "only affected section files may change" in text
    assert "diff -rq" in text
    assert "reports/latest/sections" in text


def test_update_directs_one_research_round_not_three():
    assert "one round" in flat(UPDATE).lower()


def test_update_ceilings_match_the_constants_they_name():
    """A skill quoting a number the code no longer holds is worse than one
    quoting none — it reads as authoritative."""
    from lib.research import MAX_INCREMENTAL_SUBAGENTS

    assert f"≤{MAX_INCREMENTAL_SUBAGENTS} model subagents" in body(UPDATE)
    assert f"max_subagents={MAX_INCREMENTAL_SUBAGENTS}" in body(UPDATE)
