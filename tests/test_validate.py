"""Tests for the fatal validation gate: producer contracts, layer boundary,
path containment (spec §8.4 checks 1, 2, 3, 7)."""
from __future__ import annotations

import json
from pathlib import Path

import sra
from lib.provenance import SourceMeta, StructuredMeta, write_derived, write_source, write_structured
from lib.validate import Finding, validate


# --- fixtures for planting both clean and broken artifacts ----------------

def _source_meta(source_id: str, *, kind: str = "news") -> SourceMeta:
    return SourceMeta(
        id=source_id, ticker="PANW", kind=kind, source="yahoo",
        url="https://example.com/x", fetched_at="2026-07-30T12:00:00+00:00",
        as_of="2026-07-30", title="A headline", fetch_tool="httpx",
        fetch_cmd="sra.py prefetch PANW --kinds news",
    )


def _fetch_meta(artifact_id: str) -> StructuredMeta:
    return StructuredMeta(
        id=artifact_id, ticker="PANW", producer="fetch", title="Prices",
        source="yahoo", url="https://example.com/p", as_of="2026-07-30",
        provider_tool="yfinance", fetch_cmd="sra.py prefetch PANW --kinds prices",
        fetched_at="2026-07-30T12:00:00+00:00",
    )


def _compute_meta(artifact_id: str) -> StructuredMeta:
    return StructuredMeta(
        id=artifact_id, ticker="PANW", producer="compute", title="Key ratios",
        source="computed", as_of="2026-07-30", provider_tool="lib.fetchers.fundamentals",
        fetch_cmd="sra.py prefetch PANW --kinds financials",
        computed_at="2026-07-30T12:00:00+00:00", derived_from=["income_statement_yahoo"],
    )


def _plant_source(ticker_dir: Path, source_id: str, metadata: dict,
                  body: str = "Revenue grew.", *, subdir: str = "sources") -> Path:
    """Write a source document bypassing `write_source`, so violations the
    writer refuses can be planted on disk the way a hand-edit or a legacy
    corpus would produce them."""
    lines = ["---"]
    lines += [f"{k}: {json.dumps(v)}" for k, v in metadata.items()]
    lines += ["---", "", body, ""]
    path = ticker_dir / subdir / f"{source_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _plant_json(ticker_dir: Path, relpath: str, meta: dict, data: object = None) -> Path:
    path = ticker_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"_meta": meta, "data": data or {}}, indent=2),
                    encoding="utf-8")
    return path


def _codes(findings: list[Finding]) -> set[str]:
    return {f.code for f in findings}


def _errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]


def _clean(ticker_dir: Path) -> None:
    write_source(ticker_dir, _source_meta("2026-07-30_news_yahoo"), "Revenue grew.")
    write_structured(ticker_dir, _fetch_meta("prices_yahoo"), {"close": [1]})
    # The compute artifact declares this input, and check 5 requires every
    # declared derivation to actually exist.
    write_structured(ticker_dir, _fetch_meta("income_statement_yahoo"), {"rev": [1]})
    write_structured(ticker_dir, _compute_meta("key_ratios_computed"), {"pe": 30})


# --- clean baseline -------------------------------------------------------

def test_a_clean_tree_has_no_findings(tmp_ticker_dir: Path):
    _clean(tmp_ticker_dir)
    assert validate(tmp_ticker_dir, tmp_ticker_dir.parent) == []


def test_an_empty_tree_has_no_findings(tmp_ticker_dir: Path):
    assert validate(tmp_ticker_dir, tmp_ticker_dir.parent) == []


def test_a_clean_silver_model_artifact_is_fine(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, _fetch_meta("peers_candidates"), {"peers": []})
    write_derived(tmp_ticker_dir, StructuredMeta(
        id="peers_ranked", ticker="PANW", producer="model", title="Ranked peers",
        source="sra-rater", as_of="2026-07-30",
        generated_at="2026-07-30T12:00:00+00:00", derived_from=["peers_candidates"],
    ), {"peers": ["CRWD"]}, namespace="peers")
    assert _errors(validate(tmp_ticker_dir, tmp_ticker_dir.parent)) == []


# --- check 3: layer boundary ---------------------------------------------

def test_answer_chain_regression(tmp_ticker_dir: Path):
    """§1.2's historical defect, kept as a regression fixture (§24): a
    `research_answer` landed in `sources/`, was indexed and cited exactly like
    a filing, and a report citation could then terminate at model-generated
    text rather than evidence. It must be fatal, not advisory."""
    meta = {
        "id": "2026-07-30_research_answer_moat", "ticker": "PANW",
        "kind": "research_answer", "source": "sra-researcher",
        "url": "", "fetched_at": "2026-07-30T12:00:00+00:00",
        "as_of": "2026-07-30", "title": "What is the moat?",
        "fetch_tool": "model", "fetch_cmd": "n/a",
    }
    _plant_source(tmp_ticker_dir, "2026-07-30_research_answer_moat", meta)

    findings = _errors(validate(tmp_ticker_dir, tmp_ticker_dir.parent))
    assert "layer-boundary" in _codes(findings)
    assert any("research_answer" in f.message for f in findings)


def test_model_shape_json_under_structured_is_an_error(tmp_ticker_dir: Path):
    _plant_json(tmp_ticker_dir, "structured/peers_ranked.json", {
        "id": "peers_ranked", "ticker": "PANW", "producer": "model",
        "title": "Ranked peers", "source": "sra-rater", "as_of": "2026-07-30",
        "generated_at": "2026-07-30T12:00:00+00:00",
        "derived_from": ["peers_candidates"],
    })
    assert "layer-boundary" in _codes(_errors(validate(tmp_ticker_dir,
                                                       tmp_ticker_dir.parent)))


def test_an_archived_answer_is_caught_too(tmp_ticker_dir: Path):
    """`sources/archive/` is still bronze; citations resolve into it (§5), so
    the layer boundary has to hold there as well."""
    meta = {
        "id": "2026-07-30_research_answer_moat", "ticker": "PANW",
        "kind": "research_answer", "source": "sra-researcher", "url": "",
        "fetched_at": "2026-07-30T12:00:00+00:00", "as_of": "2026-07-30",
        "title": "Moat", "fetch_tool": "model", "fetch_cmd": "n/a",
    }
    _plant_source(tmp_ticker_dir, "2026-07-30_research_answer_moat_2026-08-01",
                  meta, subdir="sources/archive")
    assert "layer-boundary" in _codes(_errors(validate(tmp_ticker_dir,
                                                       tmp_ticker_dir.parent)))


def test_an_unknown_source_kind_is_an_error(tmp_ticker_dir: Path):
    meta = {
        "id": "2026-07-30_news_yahoo", "ticker": "PANW", "kind": "not_a_kind",
        "source": "yahoo", "url": "https://example.com/x",
        "fetched_at": "2026-07-30T12:00:00+00:00", "as_of": "2026-07-30",
        "title": "A headline", "fetch_tool": "httpx", "fetch_cmd": "sra.py prefetch",
    }
    _plant_source(tmp_ticker_dir, "2026-07-30_news_yahoo", meta)
    assert "layer-boundary" in _codes(_errors(validate(tmp_ticker_dir,
                                                       tmp_ticker_dir.parent)))


# --- check 1: producer shape ---------------------------------------------

def test_compute_artifact_without_derived_from_is_an_error(tmp_ticker_dir: Path):
    _plant_json(tmp_ticker_dir, "structured/key_ratios_computed.json", {
        "id": "key_ratios_computed", "ticker": "PANW", "producer": "compute",
        "title": "Key ratios", "source": "computed", "as_of": "2026-07-30",
        "provider_tool": "lib.fetchers.fundamentals",
        "fetch_cmd": "sra.py prefetch PANW --kinds financials",
        "computed_at": "2026-07-30T12:00:00+00:00", "derived_from": [],
    })
    findings = _errors(validate(tmp_ticker_dir, tmp_ticker_dir.parent))
    assert "producer-shape" in _codes(findings)
    assert any("derived_from" in f.message for f in findings)


def test_compute_artifact_with_a_url_is_an_error(tmp_ticker_dir: Path):
    """§6.2: a computed artifact has no url — carrying one would present a
    derivation as if it had been fetched from somewhere."""
    _plant_json(tmp_ticker_dir, "structured/key_ratios_computed.json", {
        "id": "key_ratios_computed", "ticker": "PANW", "producer": "compute",
        "title": "Key ratios", "source": "computed", "as_of": "2026-07-30",
        "provider_tool": "lib.fetchers.fundamentals",
        "fetch_cmd": "sra.py prefetch PANW --kinds financials",
        "computed_at": "2026-07-30T12:00:00+00:00",
        "derived_from": ["income_statement_yahoo"], "url": "https://example.com/x",
    })
    assert "producer-shape" in _codes(_errors(validate(tmp_ticker_dir,
                                                       tmp_ticker_dir.parent)))


def test_fetch_artifact_missing_a_required_field_is_an_error(tmp_ticker_dir: Path):
    _plant_json(tmp_ticker_dir, "structured/prices_yahoo.json", {
        "id": "prices_yahoo", "ticker": "PANW", "producer": "fetch",
        "title": "Prices", "source": "yahoo", "as_of": "2026-07-30",
        "provider_tool": "yfinance", "fetch_cmd": "sra.py prefetch PANW",
        "fetched_at": "2026-07-30T12:00:00+00:00",
    })  # no url
    assert "producer-shape" in _codes(_errors(validate(tmp_ticker_dir,
                                                       tmp_ticker_dir.parent)))


def test_an_unknown_producer_is_an_error(tmp_ticker_dir: Path):
    _plant_json(tmp_ticker_dir, "structured/weird.json", {
        "id": "weird", "ticker": "PANW", "producer": "vibes",
        "title": "Weird", "source": "?", "as_of": "2026-07-30",
    })
    assert "producer-shape" in _codes(_errors(validate(tmp_ticker_dir,
                                                       tmp_ticker_dir.parent)))


def test_unparseable_json_is_an_error(tmp_ticker_dir: Path):
    (tmp_ticker_dir / "structured" / "broken.json").write_text("{ not json",
                                                               encoding="utf-8")
    assert "producer-shape" in _codes(_errors(validate(tmp_ticker_dir,
                                                       tmp_ticker_dir.parent)))


def test_json_without_a_meta_block_is_an_error(tmp_ticker_dir: Path):
    (tmp_ticker_dir / "structured" / "nometa.json").write_text('{"data": {}}',
                                                               encoding="utf-8")
    assert "producer-shape" in _codes(_errors(validate(tmp_ticker_dir,
                                                       tmp_ticker_dir.parent)))


def test_derived_artifacts_are_shape_checked_too(tmp_ticker_dir: Path):
    _plant_json(tmp_ticker_dir, "derived/peers/peers_ranked.json", {
        "id": "peers_ranked", "ticker": "PANW", "producer": "model",
        "title": "Ranked peers", "source": "sra-rater", "as_of": "2026-07-30",
        "generated_at": "2026-07-30T12:00:00+00:00", "derived_from": [],
    })
    assert "producer-shape" in _codes(_errors(validate(tmp_ticker_dir,
                                                       tmp_ticker_dir.parent)))


# --- check 2: fetch_cmd ---------------------------------------------------

def test_bronze_source_without_fetch_cmd_is_an_error(tmp_ticker_dir: Path):
    """§6: every bronze artifact carries `fetch_cmd` — it is what makes the
    evidence reproducible rather than merely present."""
    meta = {
        "id": "2026-07-30_news_yahoo", "ticker": "PANW", "kind": "news",
        "source": "yahoo", "url": "https://example.com/x",
        "fetched_at": "2026-07-30T12:00:00+00:00", "as_of": "2026-07-30",
        "title": "A headline", "fetch_tool": "httpx",
    }
    _plant_source(tmp_ticker_dir, "2026-07-30_news_yahoo", meta)
    assert "fetch-cmd" in _codes(_errors(validate(tmp_ticker_dir,
                                                  tmp_ticker_dir.parent)))


def test_model_artifact_carrying_fetch_cmd_is_an_error(tmp_ticker_dir: Path):
    """§6.2 forbids `fetch_cmd` on `model`: nothing re-runs to reproduce a
    model artifact, and claiming otherwise would make silver look citable."""
    _plant_json(tmp_ticker_dir, "derived/peers/peers_ranked.json", {
        "id": "peers_ranked", "ticker": "PANW", "producer": "model",
        "title": "Ranked peers", "source": "sra-rater", "as_of": "2026-07-30",
        "generated_at": "2026-07-30T12:00:00+00:00",
        "derived_from": ["peers_candidates"], "fetch_cmd": "sra.py prefetch PANW",
    })
    findings = _errors(validate(tmp_ticker_dir, tmp_ticker_dir.parent))
    assert {"fetch-cmd", "producer-shape"} & _codes(findings)


def test_computed_bronze_still_needs_fetch_cmd(tmp_ticker_dir: Path):
    _plant_json(tmp_ticker_dir, "structured/key_ratios_computed.json", {
        "id": "key_ratios_computed", "ticker": "PANW", "producer": "compute",
        "title": "Key ratios", "source": "computed", "as_of": "2026-07-30",
        "provider_tool": "lib.fetchers.fundamentals",
        "computed_at": "2026-07-30T12:00:00+00:00",
        "derived_from": ["income_statement_yahoo"],
    })  # no fetch_cmd
    codes = _codes(_errors(validate(tmp_ticker_dir, tmp_ticker_dir.parent)))
    assert {"fetch-cmd", "producer-shape"} & codes


# --- check 7: path containment -------------------------------------------

def test_a_symlink_escaping_the_ticker_dir_is_an_error(tmp_ticker_dir: Path):
    """Containment is about where a path RESOLVES, not how it is spelled —
    otherwise a symlink would let evidence live outside the tree that
    `validate` and `snapshot` reason about."""
    outside = tmp_ticker_dir.parent.parent / "outside.md"
    outside.write_text("not evidence", encoding="utf-8")
    (tmp_ticker_dir / "sources" / "escape.md").symlink_to(outside)
    assert "path-containment" in _codes(_errors(validate(tmp_ticker_dir,
                                                         tmp_ticker_dir.parent)))


def test_an_invalid_ticker_directory_name_is_an_error(tmp_path: Path):
    bad = tmp_path / "data" / "not-a-ticker"
    (bad / "sources").mkdir(parents=True)
    assert "path-containment" in _codes(_errors(validate(bad, bad.parent)))


def test_macro_is_exempt_from_the_ticker_pattern(tmp_macro_dir: Path):
    assert _errors(validate(tmp_macro_dir, tmp_macro_dir.parent)) == []


def test_another_underscore_directory_is_not_exempt(tmp_path: Path):
    bad = tmp_path / "data" / "_EVIL"
    (bad / "sources").mkdir(parents=True)
    assert "path-containment" in _codes(_errors(validate(bad, bad.parent)))


def test_a_ticker_dir_outside_the_data_root_is_an_error(tmp_path: Path):
    elsewhere = tmp_path / "elsewhere" / "PANW"
    (elsewhere / "sources").mkdir(parents=True)
    data_root = tmp_path / "data"
    data_root.mkdir()
    assert "path-containment" in _codes(_errors(validate(elsewhere, data_root)))


# --- Finding shape --------------------------------------------------------

def test_findings_carry_severity_code_path_and_message(tmp_ticker_dir: Path):
    (tmp_ticker_dir / "structured" / "broken.json").write_text("{ not json",
                                                               encoding="utf-8")
    finding = _errors(validate(tmp_ticker_dir, tmp_ticker_dir.parent))[0]
    assert finding.severity == "error"
    assert finding.code
    assert "broken.json" in finding.path
    assert finding.message


# --- CLI ------------------------------------------------------------------

def test_validate_command_exits_0_on_a_clean_tree(tmp_path: Path):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    _clean(tmp_path / "PANW")
    assert sra.main(["validate", "PANW", "--data-root", str(tmp_path)]) == 0


def test_validate_command_exits_1_on_an_error(tmp_path: Path, capsys):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    (tmp_path / "PANW" / "structured" / "broken.json").write_text("{ not json",
                                                                  encoding="utf-8")
    capsys.readouterr()
    assert sra.main(["validate", "PANW", "--data-root", str(tmp_path)]) == 1
    assert "broken.json" in capsys.readouterr().out


def test_validate_command_prints_findings_as_json(tmp_path: Path, capsys):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    (tmp_path / "PANW" / "structured" / "broken.json").write_text("{ not json",
                                                                  encoding="utf-8")
    capsys.readouterr()
    sra.main(["validate", "PANW", "--data-root", str(tmp_path)])
    findings = json.loads(capsys.readouterr().out)
    assert findings[0]["severity"] == "error"
    assert findings[0]["code"]


def test_validate_has_no_force_flag(tmp_path: Path):
    """§8.4: "There is no --force." A gate you can wave through is not a gate."""
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    (tmp_path / "PANW" / "structured" / "broken.json").write_text("{ not json",
                                                                  encoding="utf-8")
    import pytest
    with pytest.raises(SystemExit):
        sra.main(["validate", "PANW", "--force", "--data-root", str(tmp_path)])


def test_validate_command_exits_1_when_uninitialized(tmp_path: Path):
    assert sra.main(["validate", "MSFT", "--data-root", str(tmp_path)]) == 1


def test_validate_command_rejects_a_traversal_ticker(tmp_path: Path):
    assert sra.main(["validate", "../evil", "--data-root", str(tmp_path)]) == 1
