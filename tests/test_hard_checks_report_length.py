"""Report-level length budget: a section may grow if the report does not."""

from __future__ import annotations

from pathlib import Path

from lib.hard_checks import run_checks


def _dirs(tmp_path: Path, baseline: dict[str, int],
          current: dict[str, int]) -> tuple[Path, Path]:
    base = tmp_path / "sections_prepolish"
    cur = tmp_path / "sections"
    for directory, spec in ((base, baseline), (cur, current)):
        directory.mkdir(parents=True)
        for name, words in spec.items():
            (directory / f"{name}.md").write_text(" ".join(["w"] * words),
                                                  encoding="utf-8")
    return base, cur


def test_report_within_budget_passes(tmp_path: Path) -> None:
    _, cur = _dirs(tmp_path, {"a": 100, "b": 100}, {"a": 130, "b": 70})
    target = cur / "a.md"

    failures = run_checks(target.read_text(encoding="utf-8"),
                          ["report_not_longer_than: sections_prepolish"],
                          tmp_path, target=target)

    assert failures == []


def test_report_over_budget_fails(tmp_path: Path) -> None:
    _, cur = _dirs(tmp_path, {"a": 100, "b": 100}, {"a": 130, "b": 100})
    target = cur / "a.md"

    failures = run_checks(target.read_text(encoding="utf-8"),
                          ["report_not_longer_than: sections_prepolish"],
                          tmp_path, target=target)

    assert len(failures) == 1
    assert "230" in failures[0] and "200" in failures[0]


def test_factor_widens_the_budget(tmp_path: Path) -> None:
    _, cur = _dirs(tmp_path, {"a": 100, "b": 100}, {"a": 130, "b": 75})
    target = cur / "a.md"

    assert run_checks(target.read_text(encoding="utf-8"),
                      ["report_not_longer_than: sections_prepolish 1.03"],
                      tmp_path, target=target) == []


def test_an_absolute_baseline_resolves(tmp_path: Path) -> None:
    base, cur = _dirs(tmp_path, {"a": 100}, {"a": 90})
    target = cur / "a.md"

    assert run_checks(target.read_text(encoding="utf-8"),
                      [f"report_not_longer_than: {base}"],
                      tmp_path, target=target) == []


def test_report_rule_without_a_target_is_a_clear_failure(tmp_path: Path) -> None:
    failures = run_checks("words",
                          ["report_not_longer_than: sections_prepolish"], tmp_path)

    assert len(failures) == 1
    assert "target" in failures[0]


def test_a_missing_baseline_directory_never_silently_passes(tmp_path: Path) -> None:
    cur = tmp_path / "sections"
    cur.mkdir()
    target = cur / "a.md"
    target.write_text("one two three", encoding="utf-8")

    failures = run_checks("one two three",
                          ["report_not_longer_than: nope"], tmp_path, target=target)

    assert len(failures) == 1
    assert "not found" in failures[0]


def test_section_pct_allows_bounded_growth(tmp_path: Path) -> None:
    _, cur = _dirs(tmp_path, {"a": 100}, {"a": 108})

    assert run_checks((cur / "a.md").read_text(encoding="utf-8"),
                      ["not_longer_than_pct: sections_prepolish/a.md 1.10"],
                      tmp_path) == []


def test_section_pct_rejects_growth_past_the_factor(tmp_path: Path) -> None:
    _, cur = _dirs(tmp_path, {"a": 100}, {"a": 120})

    failures = run_checks((cur / "a.md").read_text(encoding="utf-8"),
                          ["not_longer_than_pct: sections_prepolish/a.md 1.10"],
                          tmp_path)

    assert len(failures) == 1
    assert "120" in failures[0]


def test_section_pct_needs_a_factor(tmp_path: Path) -> None:
    _dirs(tmp_path, {"a": 100}, {"a": 90})

    failures = run_checks("short",
                          ["not_longer_than_pct: sections_prepolish/a.md"], tmp_path)

    assert len(failures) == 1
    assert "factor" in failures[0]


def test_a_baseline_escaping_the_base_dir_is_refused(tmp_path: Path) -> None:
    cur = tmp_path / "sections"
    cur.mkdir()
    target = cur / "a.md"
    target.write_text("one two", encoding="utf-8")

    failures = run_checks("one two",
                          ["report_not_longer_than: ../../etc"],
                          tmp_path, target=target)

    assert len(failures) == 1
    assert "outside" in failures[0]


def test_existing_rules_still_work_without_target(tmp_path: Path) -> None:
    (tmp_path / "base.md").write_text("one two three four five", encoding="utf-8")

    assert run_checks("one two three", ["not_longer_than: base.md"], tmp_path) == []
