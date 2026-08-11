#!/usr/bin/env python3
"""Load and validate sections.yaml — the per-section editorial config (spec §9)."""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SECTIONS_PATH = REPO_ROOT / "sections.yaml"

# Report order §1..§7 — sections.yaml file order must match.
SECTION_IDS: tuple[str, ...] = (
    "profile", "business_model", "competitive", "supply_chain",
    "financial", "valuation", "risk_news",
)
REQUIRED_SECTION_KEYS = ("title", "wiki_page", "seed_questions", "research_guidance",
                         "write_guidance", "word_target_base", "hard_checks", "subscribes_to")
REQUIRED_TOP_KEYS = ("sections", "section_ownership", "tension_analysis",
                     "claim_status_rule", "length_presets")


def load_sections(path: Path | None = None) -> dict:
    """Parse and validate sections.yaml; raises ValueError on structural defects."""
    cfg = yaml.safe_load((path or SECTIONS_PATH).read_text(encoding="utf-8"))
    for key in REQUIRED_TOP_KEYS:
        if key not in cfg:
            raise ValueError(f"sections.yaml missing top-level key: {key}")
    sections = cfg["sections"]
    for sid in SECTION_IDS:
        if sid not in sections:
            raise ValueError(f"sections.yaml missing section: {sid}")
    # §18.1: "File order must equal lib.sections.SECTION_IDS. The loader enforces this."
    # PyYAML's safe_load builds mapping nodes into plain dicts in document order, and
    # Python (3.7+) dicts preserve insertion order, so this reflects the file's actual order.
    actual_order = tuple(sections.keys())
    if actual_order != SECTION_IDS:
        raise ValueError(
            f"sections.yaml section order must equal {SECTION_IDS}, got {actual_order}"
        )
    for sid in SECTION_IDS:
        section = sections[sid]
        for key in REQUIRED_SECTION_KEYS:
            if key not in section:
                raise ValueError(f"section {sid} missing key: {key}")
        n_seeds = len(section["seed_questions"])
        if not 4 <= n_seeds <= 6:
            raise ValueError(f"section {sid}: need 4-6 seed_questions, got {n_seeds}")
    return cfg


def word_target(cfg: dict, section: str, length: str = "standard") -> int:
    """Word target for a section at a length preset (base is the `long` preset)."""
    scale = cfg["length_presets"][length]
    return round(cfg["sections"][section]["word_target_base"] * scale)
