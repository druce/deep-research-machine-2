"""Deterministic hard checks on a section draft (spec §18.1, §8.2, §22).

These are the gate a writer agent must pass before its draft counts as done, so
every rule needs one passing and one failing case — a check that cannot fail is
a check that never caught anything.

Two rules carry history. `not_longer_than` counts WORDS because sra5's polish
pass, gated on characters, GREW an INTC body by 1,933 bytes while leaving every
flagged redundancy in place and nothing detected it. `no_internal_filenames` is
§8.2's requirement that internal artifact names never reach report prose.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lib.hard_checks import run_checks

REPO = Path(__file__).resolve().parent.parent

SECTION = """## 3. Competitive Landscape

Palo Alto holds roughly 24% of the network-firewall market [^2026-05-21_sec_10q],
against CrowdStrike's 19% in endpoint [^estimates].

### SWOT

| Strengths | Weaknesses |
|---|---|
| Platform pull-through | Services drag |
"""


def failures(text: str, rules: list, base_dir: Path | None = None) -> list[str]:
    return run_checks(text, rules, base_dir or REPO)


# --- length rules are explicit about their unit ----------------------------

def test_min_and_max_length_count_characters():
    """sra5 conflated characters and words across two rules and shipped a
    length gate that measured the wrong thing. The unit is documented and
    pinned here so the next reader does not have to guess."""
    assert failures("x" * 100, ["min_length: 50", "max_length: 200"]) == []

    short = failures("x" * 10, ["min_length: 50"])
    assert len(short) == 1 and "10" in short[0] and "50" in short[0]

    long = failures("x" * 500, ["max_length: 200"])
    assert len(long) == 1 and "500" in long[0]


def test_length_rules_reject_a_non_numeric_threshold():
    problems = failures("text", ["max_length: lots"])
    assert len(problems) == 1
    assert "lots" in problems[0]


# --- structural rules -------------------------------------------------------

def test_startswith_checks_the_first_non_empty_line():
    """A draft that opens with a blank line still starts with its heading."""
    assert failures("\n\n## 3. Competitive Landscape\n\nBody.",
                    ["startswith: ## 3. Competitive Landscape"]) == []
    problems = failures("## 4. Supply Chain\n",
                        ["startswith: ## 3. Competitive Landscape"])
    assert len(problems) == 1 and "## 4. Supply Chain" in problems[0]


def test_contains_and_its_failure():
    assert failures(SECTION, ["contains: ### SWOT"]) == []
    assert len(failures(SECTION, ["contains: ### Monitoring Dashboard"])) == 1


def test_regex_requires_presence():
    assert failures(SECTION, [r"regex: ^\| Strengths"]) == []
    assert len(failures(SECTION, [r"regex: ^## 9\."])) == 1


def test_not_regex_is_an_absence_assertion_and_reports_where():
    """The single-H2 rule: a section owns exactly one level-2 heading, and a
    second one means the writer wrote two sections into one file."""
    rule = r"not_regex: ^## [\s\S]*?^## "
    assert failures(SECTION, [rule]) == []

    two_sections = SECTION + "\n## 4. Supply Chain Positioning\n\nBody.\n"
    problems = failures(two_sections, [rule])
    assert len(problems) == 1
    assert "line 1" in problems[0]


def test_every_section_in_sections_yaml_passes_its_own_shape():
    """The configured rules have to be satisfiable by a draft that follows
    them — a hard check nothing can pass would block every build."""
    from lib.sections import load_sections

    cfg = load_sections()
    for sid, section in cfg["sections"].items():
        heading = next(c.split(": ", 1)[1] for c in section["hard_checks"]
                       if c.startswith("startswith: "))
        draft = f"{heading}\n\nBody with a claim [^2026-05-21_sec_10q].\n"
        assert failures(draft, section["hard_checks"]) == [], sid


# --- not_longer_than counts words ------------------------------------------

def test_not_longer_than_compares_word_counts(tmp_path: Path):
    previous = tmp_path / "before.md"
    previous.write_text("one two three four five", encoding="utf-8")

    assert failures("one two three", [f"not_longer_than: {previous}"]) == []
    problems = failures("one two three four five six seven",
                        [f"not_longer_than: {previous}"])
    assert len(problems) == 1
    assert "GREW by 2" in problems[0]


def test_not_longer_than_counts_words_not_characters(tmp_path: Path):
    """The regression this rule exists for: a rewrite that swaps short words
    for long ones grows the byte count while shrinking the word count, and a
    character gate would fail a draft that genuinely got shorter."""
    previous = tmp_path / "before.md"
    previous.write_text("a b c d e f g h", encoding="utf-8")     # 8 words, 15 chars
    verbose = "extraordinarily circumlocutory phrasing"           # 3 words, 39 chars

    assert len(verbose) > len(previous.read_text())
    assert failures(verbose, [f"not_longer_than: {previous}"]) == []


def test_not_longer_than_fails_loudly_on_a_missing_reference(tmp_path: Path):
    """Silently passing when the baseline is absent would disable the shrink
    gate exactly when a path substitution went wrong."""
    problems = failures("text", [f"not_longer_than: {tmp_path / 'gone.md'}"])
    assert len(problems) == 1 and "not found" in problems[0]


def test_not_longer_than_resolves_a_relative_path_against_base_dir(tmp_path: Path):
    (tmp_path / "before.md").write_text("one two three", encoding="utf-8")
    assert failures("one two", ["not_longer_than: before.md"], tmp_path) == []


def test_not_longer_than_refuses_to_escape_base_dir(tmp_path: Path):
    """§8.4's containment rule: a rule value is interpolated into a path, and
    a relative rule reaching outside the run directory is a defect either way."""
    problems = failures("text", ["not_longer_than: ../../etc/passwd"], tmp_path)
    assert len(problems) == 1
    assert "outside" in problems[0] or "not found" in problems[0]


# --- §8.2 internal filenames ------------------------------------------------

@pytest.mark.parametrize("prose", [
    "See key_facts.json for the breakdown.",
    "Pulled from data/PANW/structured/profile_yahoo.json.",
    "The structured/ artifacts show a 24% share.",
    "Recorded in derived/answers/ for audit.",
    "Listed in sources/00_manifest.md.",
])
def test_internal_filenames_are_rejected(prose):
    """§8.2: "Internal artifact names must never appear in report prose." The
    reader has no filesystem — a path is a dangling reference to them."""
    problems = failures(prose, ["no_internal_filenames"])
    assert len(problems) == 1, prose


def test_citation_markers_are_not_internal_filenames():
    """`[^2026-05-21_sec_10q]` is the draft's citation form; assembly turns it
    into `[^1]`. Flagging it would fail every properly cited section."""
    assert failures(SECTION, ["no_internal_filenames"]) == []


def test_the_english_word_manifest_is_not_a_filename():
    """A hard check gates the build, so a false positive costs a rewrite cycle.
    The artifact's actual name is `00_manifest.md`, which IS caught above."""
    assert failures("These pressures manifest in gross margin.",
                    ["no_internal_filenames"]) == []


def test_internal_filename_failures_name_what_they_found():
    problems = failures("See key_facts.json and structured/ for detail.",
                        ["no_internal_filenames"])
    assert "key_facts.json" in problems[0]
    assert "structured/" in problems[0]


# --- rule handling ----------------------------------------------------------

def test_an_unknown_rule_is_a_failure_not_a_silent_pass():
    """A typo'd rule name that passed would disable a gate invisibly."""
    problems = failures("text", ["max_lenght: 100"])
    assert len(problems) == 1 and "max_lenght" in problems[0]


def test_a_malformed_rule_is_a_failure():
    problems = failures("text", ["max_length"])
    assert len(problems) == 1


def test_rules_may_be_given_as_single_key_mappings():
    """`sections.yaml` carries strings, but a caller building rules in code
    should not have to format them back into text first."""
    assert failures("x" * 100, [{"min_length": 50}]) == []
    assert len(failures("x" * 10, [{"min_length": 50}])) == 1


def test_every_failing_rule_is_reported_not_just_the_first():
    problems = failures("short", ["min_length: 500", "contains: ### SWOT"])
    assert len(problems) == 2


def test_no_rules_is_a_pass():
    assert failures(SECTION, []) == []


# --- the CLI writer agents call --------------------------------------------

def test_cli_reports_failures_as_json(tmp_path: Path):
    draft = tmp_path / "competitive.md"
    draft.write_text("## 4. Wrong Heading\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "lib.hard_checks", str(draft), "--rules-json",
         json.dumps(["startswith: ## 3. Competitive Landscape"])],
        capture_output=True, text=True, cwd=REPO)

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["passed"] is False
    assert len(payload["failures"]) == 1


def test_cli_exits_zero_when_every_check_passes(tmp_path: Path):
    draft = tmp_path / "competitive.md"
    draft.write_text(SECTION, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "lib.hard_checks", str(draft), "--rules-json",
         json.dumps(["startswith: ## 3. Competitive Landscape",
                     "no_internal_filenames"])],
        capture_output=True, text=True, cwd=REPO)

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"passed": True, "failures": []}


def test_cli_reports_a_missing_file_rather_than_passing(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "-m", "lib.hard_checks", str(tmp_path / "gone.md"),
         "--rules-json", "[]"],
        capture_output=True, text=True, cwd=REPO)

    assert result.returncode == 1
    assert "not found" in json.loads(result.stdout)["failures"][0]
