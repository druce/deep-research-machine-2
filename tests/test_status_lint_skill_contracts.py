"""`/sra-status` and `/sra-lint` must stay consistent with the code they drive
(spec §10.1, §22.1, §23.3, §23.4).

Nothing enforces a skill file at runtime, so a renamed subcommand or a helper
that moved fails mid-run as a confusing agent error. These tests are the
enforcement.
"""
from __future__ import annotations

import re
from pathlib import Path

import frontmatter

from lib.run_stats import MAX_MINUTES, MAX_SUBAGENTS

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / ".claude" / "skills" / "sra-status" / "SKILL.md"
LINT = ROOT / ".claude" / "skills" / "sra-lint" / "SKILL.md"
JUDGMENT = ROOT / "prompts" / "lint" / "judgment.md"


def body(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def registered_commands() -> set[str]:
    import sra

    return set(sra.build_parser()._subparsers._group_actions[0].choices)


# --- both skills ----------------------------------------------------------

def test_skills_exist_with_frontmatter():
    for path, name in ((STATUS, "sra-status"), (LINT, "sra-lint")):
        post = frontmatter.load(path)
        assert post.metadata["name"] == name
        assert post.metadata["description"].strip()


def test_every_sra_subcommand_named_is_registered():
    for path in (STATUS, LINT, JUDGMENT):
        named = set(re.findall(r"sra\.py ([a-z][a-z-]+)", body(path)))
        assert named <= registered_commands(), (
            f"{path.name}: unregistered {sorted(named - registered_commands())}")


def test_python_helpers_the_status_skill_calls_exist():
    """The inline snippet imports these by name; a moved helper breaks the
    skill silently, at the moment someone is asking what state a ticker is in."""
    from lib.render.runs import is_snapshotted, run_dirs      # noqa: F401
    from lib.run_stats import check_budgets, load_run_stats   # noqa: F401

    text = body(STATUS)
    for symbol in ("is_snapshotted", "run_dirs", "check_budgets", "load_run_stats"):
        assert symbol in text, symbol


# --- sra-status -----------------------------------------------------------

def test_status_skill_is_read_only():
    """No lock, no agent, no writes — running it during a build is the point."""
    text = body(STATUS)
    assert "Read-only" in text
    for mutating in ("sra.py prefetch ", "sra.py assemble", "sra.py snapshot",
                     "add-questions"):
        assert mutating not in text, mutating


def test_status_skill_quotes_the_real_budget_ceilings():
    text = body(STATUS)
    assert str(MAX_SUBAGENTS) in text
    assert f"{MAX_MINUTES} minutes" in text
    assert "6M tokens" in text


def test_status_skill_names_the_minimum_viable_inputs():
    """§11.1: these three block a build; everything else degrades it."""
    from lib.fetchers.registry import FATAL_KINDS

    text = body(STATUS)
    for kind in FATAL_KINDS:
        assert f"`{kind}`" in text, kind


def test_status_skill_ends_with_one_recommendation():
    assert "One recommendation, not a menu" in body(STATUS)


# --- sra-lint -------------------------------------------------------------

def test_lint_skill_requires_deterministic_lint_first():
    """§22.1: "/sra-lint runs only after deterministic lint"."""
    text = body(LINT)
    assert text.index("sra.py wiki-lint") < text.index("## Step 2")
    assert "only after deterministic lint" in text


def test_lint_skill_is_limited_to_the_two_judgments():
    text = " ".join(body(LINT).split())
    assert "does each cited source actually support the claim" in text.lower()
    assert "is each claimed tension genuine" in text.lower()


def test_lint_findings_become_ledger_questions_with_the_lint_origin():
    """§23.4: the purpose vocabulary doubles as a question's `origin`, so a
    lint-raised question stays distinguishable from a seed question."""
    text = body(LINT)
    assert "--origin lint" in text
    assert "add-questions" in text


def test_lint_skill_uses_one_subagent():
    text = body(LINT)
    assert "ONE subagent" in text
    assert text.count('subagent_type: "sra-writer"') == 1


def test_lint_findings_land_in_silver():
    """Lint output is model judgment: durable working state, never citable."""
    text = body(LINT)
    assert "derived/lint_findings.json" in text
    assert "structured/lint" not in text


def test_lint_skill_points_at_the_judgment_prompt():
    assert JUDGMENT.exists()
    assert "prompts/lint/judgment.md" in body(LINT)


# --- the judgment prompt --------------------------------------------------

def test_judgment_prompt_forbids_repeating_the_deterministic_checks():
    text = body(JUDGMENT)
    assert "not** your job" in text or "not your job" in text
    assert "Do not repeat them" in text


def test_judgment_prompt_defines_every_verdict_it_asks_for():
    text = body(JUDGMENT)
    for verdict in ("supported", "partial", "unsupported", "wrong-layer",
                    "genuine", "not-genuine"):
        assert verdict in text, verdict


def test_judgment_prompt_requires_opening_the_source():
    """A citation checked without reading the source is a citation not checked
    — and reporting it as verified is worse than skipping it."""
    text = " ".join(body(JUDGMENT).split())
    assert "sra.py show" in text
    assert "citation you did not open is a citation you did not check" in text


def test_judgment_prompt_forbids_the_judge_touching_the_ledger_or_the_wiki():
    """§3: the driver owns every ledger transition; lint reports, research
    fixes."""
    text = body(JUDGMENT)
    assert "Do not edit any wiki page" in text
    assert "Do not add questions yourself" in text
    assert "Do not fetch anything" in text
