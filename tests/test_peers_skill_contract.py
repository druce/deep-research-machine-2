"""The skill, agent and rubric must stay consistent with the code they drive.

A skill file is instructions to a model, so nothing enforces it at runtime: a
path that moved or a command that was renamed fails at the worst possible moment,
mid-build, as a confusing agent error. These tests are the enforcement — every
path and subcommand the skill names has to exist, and the retired ones must not
reappear.
"""
from __future__ import annotations

import re
from pathlib import Path

import frontmatter

from lib.fetchers.peers import MIN_USER_PEERS
from lib.peers_scoring import PEER_SET_SIZE

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "sra-peers" / "SKILL.md"
AGENT = ROOT / ".claude" / "agents" / "sra-rater.md"
RUBRIC = ROOT / "prompts" / "peers_rubric.md"


def skill_body() -> str:
    return SKILL.read_text(encoding="utf-8")


# --- the files themselves --------------------------------------------------

def test_skill_and_agent_exist_with_frontmatter():
    for path, name in ((SKILL, "sra-peers"), (AGENT, "sra-rater")):
        post = frontmatter.load(path)
        assert post.metadata["name"] == name
        assert post.metadata["description"].strip()


def test_rubric_exists_and_the_skill_points_the_rater_at_it():
    assert RUBRIC.exists()
    assert "prompts/peers_rubric.md" in skill_body()


def test_rater_agent_is_tool_scoped():
    """No Bash, no network: the candidate table IS the evidence, and a ranking
    that reaches outside it is not reproducible from the recorded artifacts."""
    tools = frontmatter.load(AGENT).metadata["tools"]
    for forbidden in ("Bash", "WebFetch", "WebSearch"):
        assert forbidden not in tools


# --- commands and paths actually exist -------------------------------------

def test_skill_uses_the_real_subcommands():
    body = skill_body()
    assert "sra.py peers-candidates" in body
    assert "sra.py peers-select" in body


def test_every_sra_subcommand_the_skill_names_is_registered():
    """Catches a renamed or not-yet-implemented command before a build does."""
    import sra

    registered = set(sra.build_parser()._subparsers._group_actions[0].choices)
    named = set(re.findall(r"sra\.py ([a-z][a-z-]+)", skill_body()))
    assert named <= registered, f"unregistered: {sorted(named - registered)}"


def test_skill_uses_the_silver_peers_paths():
    """§4.2/§13.3: every peers artifact is silver under derived/peers/."""
    body = skill_body()
    for artifact in ("peers_candidates.json", "peers_user.json",
                     "peers_proxy.json", "peers_ranked.json"):
        assert f"derived/peers/{artifact}" in body, artifact


def test_skill_does_not_reference_retired_locations():
    """`.tmp/` was never realized in sra6 (§4.2 makes the peers working set
    durable silver), and peers_selected is silver — putting it in structured/
    would make a citation resolve into peer-selection lineage (§13.6)."""
    body = skill_body()
    assert ".tmp/" not in body
    assert "structured/peers_selected" not in body
    assert "peers_scores" not in body        # the retired weighted composite


# --- the contract the skill has to get right -------------------------------

def test_skill_skip_threshold_matches_min_user_peers():
    assert re.search(rf"\b{MIN_USER_PEERS}\b or more peers", skill_body())


def test_skill_asks_for_exactly_the_peer_set_size():
    assert re.search(rf"exactly {PEER_SET_SIZE} entries", skill_body(), re.I)


def test_skill_tells_the_rater_to_write_the_meta_envelope():
    """peers-select reads `_meta.generated_at` for §13.5's staleness guard and
    exits 1 without it, so a bare list would break the pipeline."""
    body = skill_body()
    assert '"_meta"' in body
    assert '"producer": "model"' in body
    assert "generated_at" in body


def test_skill_supplies_a_timestamp_because_the_rater_has_no_clock():
    """The rater's tools are Read/Write/Edit/Glob/Grep — it cannot run `date`,
    so the orchestrator has to hand it the stamp."""
    body = skill_body()
    assert "date -u" in body
    assert "verbatim" in body.lower()


def test_skill_warns_against_ranking_by_fund_count():
    """The TSLA failure: a broad sector ETF put ABNB/EXPE above F/GM, so the
    skill must tell the rater fund overlap is weak evidence, not a score."""
    body = skill_body()
    assert "peers_ranked.json" in body
    assert "fund_count" in body
    assert "weak" in body.lower()
    assert "above all the source labels" in body.lower()


def test_skill_explains_the_proxy_is_a_talent_market_cohort():
    assert "talent" in skill_body().lower()


def test_skill_says_the_subject_is_not_selectable():
    assert "is_subject" in skill_body()


# --- the rubric is a judgment aid, not a formula ---------------------------

def test_rubric_forbids_a_weighted_composite():
    """§13: "These signals guide judgment. They are not converted into a
    weighted composite." """
    body = RUBRIC.read_text(encoding="utf-8").lower()
    assert "composite" in body
    assert "do not" in body
    assert "judgment, not an arithmetic" in body


def test_rubric_covers_every_axis_the_spec_lists():
    body = RUBRIC.read_text(encoding="utf-8").lower()
    for axis in ("business-model similarity", "product and customer overlap",
                 "competitive substitutability", "end-market similarity",
                 "scale", "growth profile", "revenue profile",
                 "company description", "agreement between mechanical sources",
                 "proxy excerpt"):
        assert axis in body, axis


def test_rubric_allows_a_proxy_only_candidate():
    """§13.3, and the behavior `peers-select` implements: a ranked row may name
    a company no mechanical source surfaced."""
    body = RUBRIC.read_text(encoding="utf-8").lower()
    assert "only" in body and "excerpt" in body
    assert "never invent a ticker" in body
