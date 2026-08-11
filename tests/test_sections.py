"""Tests for sections.yaml and its loader (spec §18.1)."""
from pathlib import Path

import pytest

from lib.sections import SECTION_IDS, load_sections, word_target

REPO = Path(__file__).resolve().parents[1]


def test_section_ids_are_report_order():
    assert SECTION_IDS == ("profile", "business_model", "competitive",
                           "supply_chain", "financial", "valuation", "risk_news")


def test_file_order_equals_section_ids():
    """§18.1: the file's `sections:` order must equal SECTION_IDS exactly."""
    import yaml
    raw = yaml.safe_load((REPO / "sections.yaml").read_text(encoding="utf-8"))
    assert tuple(raw["sections"].keys()) == SECTION_IDS


def test_load_real_sections_yaml():
    cfg = load_sections()
    assert set(cfg["sections"]) == set(SECTION_IDS)
    for sid in SECTION_IDS:
        s = cfg["sections"][sid]
        assert s["title"]
        assert s["wiki_page"] == sid
        assert 4 <= len(s["seed_questions"]) <= 6
        assert all(q.strip().endswith("?") for q in s["seed_questions"])
        assert len(s["research_guidance"]) > 200
        assert len(s["write_guidance"]) > 200
        assert isinstance(s["word_target_base"], int)
        assert any(c.startswith("startswith: ## ") for c in s["hard_checks"])
        assert any(c.startswith("not_regex: ") for c in s["hard_checks"])
        assert s["subscribes_to"], f"section {sid}: subscribes_to must be non-empty"
        assert isinstance(s["subscribes_to"], list)


def test_top_level_editorial_blocks_present():
    cfg = load_sections()
    assert "Fact ownership" in cfg["section_ownership"]
    assert "owning section" in cfg["section_ownership"]
    assert "Surface contradictions" in cfg["tension_analysis"]
    for tag in ("[REPORTED]", "[GUIDANCE]", "[CONSENSUS]", "[ESTIMATE]"):
        assert tag in cfg["claim_status_rule"]


def test_length_presets_scale_word_targets():
    cfg = load_sections()
    assert cfg["length_presets"] == {"short": 0.40, "standard": 0.75, "long": 1.00}
    base = cfg["sections"]["financial"]["word_target_base"]
    assert word_target(cfg, "financial", "long") == base
    assert word_target(cfg, "financial", "short") == round(base * 0.40)
    assert word_target(cfg, "financial") == round(base * 0.75)


def test_loader_rejects_missing_section(tmp_path: Path):
    bad = tmp_path / "sections.yaml"
    bad.write_text("sections: {}\nsection_ownership: x\n"
                   "tension_analysis: x\nclaim_status_rule: x\n"
                   "length_presets: {short: 0.4, standard: 0.75, long: 1.0}\n")
    with pytest.raises(ValueError, match="missing section"):
        load_sections(bad)


def test_loader_rejects_missing_subscribes_to(tmp_path: Path):
    """§18.1: subscribes_to is a required per-section key, enforced by the loader."""
    cfg = load_sections()
    section = dict(cfg["sections"]["profile"])
    del section["subscribes_to"]
    sections = {sid: cfg["sections"][sid] for sid in SECTION_IDS}
    sections["profile"] = section

    import yaml
    bad = tmp_path / "sections.yaml"
    bad.write_text(yaml.safe_dump({
        "sections": sections,
        "section_ownership": cfg["section_ownership"],
        "tension_analysis": cfg["tension_analysis"],
        "claim_status_rule": cfg["claim_status_rule"],
        "length_presets": cfg["length_presets"],
    }))
    with pytest.raises(ValueError, match="missing key: subscribes_to"):
        load_sections(bad)
