"""Tests for `sra.py show TICKER ID` (spec §9)."""
from __future__ import annotations

import json
from pathlib import Path

import sra
from lib.provenance import SourceMeta, StructuredMeta, write_derived, write_source, write_structured


def _source_meta(source_id: str, *, supersedes: str | None = None,
                 ticker: str = "PANW") -> SourceMeta:
    return SourceMeta(
        id=source_id, ticker=ticker, kind="news", source="yahoo",
        url="https://example.com/x", fetched_at="2026-07-30T12:00:00+00:00",
        as_of="2026-07-30", title="A headline", fetch_tool="httpx",
        fetch_cmd="sra.py prefetch PANW --kinds news", supersedes=supersedes,
    )


def _fetch_meta(artifact_id: str, *, ticker: str = "PANW") -> StructuredMeta:
    return StructuredMeta(
        id=artifact_id, ticker=ticker, producer="fetch", title="Prices",
        source="yahoo", url="https://example.com/p", as_of="2026-07-30",
        provider_tool="yfinance", fetch_cmd="sra.py prefetch PANW --kinds prices",
        fetched_at="2026-07-30T12:00:00+00:00",
    )


def _model_meta(artifact_id: str) -> StructuredMeta:
    return StructuredMeta(
        id=artifact_id, ticker="PANW", producer="model", title="Ranked peers",
        source="sra-rater", as_of="2026-07-30",
        generated_at="2026-07-30T12:00:00+00:00",
        derived_from=["peers_candidates"],
    )


def _init(tmp_path: Path, ticker: str = "PANW") -> Path:
    sra.main(["init", ticker, "--data-root", str(tmp_path)])
    return tmp_path / ticker.upper()


# --- resolution -----------------------------------------------------------

def test_shows_a_current_source_with_its_frontmatter(tmp_path: Path, capsys):
    """The whole file is printed, provenance included — a researcher reading a
    document should see where it came from."""
    d = _init(tmp_path)
    write_source(d, _source_meta("2026-07-30_news_yahoo"), "Revenue grew.")
    capsys.readouterr()

    assert sra.main(["show", "PANW", "2026-07-30_news_yahoo",
                     "--data-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Revenue grew." in out
    assert "https://example.com/x" in out


def test_shows_an_archived_source_without_a_flag(tmp_path: Path, capsys):
    """§9: `show` resolves any id, current or archived, without a flag — a
    citation to superseded evidence must still be readable."""
    d = _init(tmp_path)
    write_source(d, _source_meta("2026-07-30_news_yahoo"), "Old text.")
    write_source(d, _source_meta("2026-07-31_news_yahoo",
                                 supersedes="2026-07-30_news_yahoo"), "New text.")
    capsys.readouterr()

    assert sra.main(["show", "PANW", "2026-07-30_news_yahoo",
                     "--data-root", str(tmp_path)]) == 0
    assert "Old text." in capsys.readouterr().out


def test_shows_a_structured_artifact_as_pretty_json(tmp_path: Path, capsys):
    d = _init(tmp_path)
    write_structured(d, _fetch_meta("prices_yahoo"), {"close": [1, 2, 3]})
    capsys.readouterr()

    assert sra.main(["show", "PANW", "prices_yahoo", "--data-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["data"] == {"close": [1, 2, 3]}
    assert "\n  " in out  # indented, not a single line


def test_shows_a_namespaced_derived_artifact(tmp_path: Path, capsys):
    d = _init(tmp_path)
    write_derived(d, _model_meta("peers_ranked"), {"peers": ["CRWD"]}, namespace="peers")
    capsys.readouterr()

    assert sra.main(["show", "PANW", "peers_ranked", "--data-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == {"peers": ["CRWD"]}


def test_shows_a_derived_artifact_at_the_top_level(tmp_path: Path, capsys):
    d = _init(tmp_path)
    write_derived(d, _model_meta("some_working_note"), {"x": 1})
    capsys.readouterr()

    assert sra.main(["show", "PANW", "some_working_note",
                     "--data-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == {"x": 1}


def test_sources_win_over_structured_on_an_id_collision(tmp_path: Path, capsys):
    """§9 fixes the resolution order; bronze text is checked first."""
    d = _init(tmp_path)
    write_source(d, _source_meta("collide"), "I am the source.")
    write_structured(d, _fetch_meta("collide"), {"x": 1})
    capsys.readouterr()

    sra.main(["show", "PANW", "collide", "--data-root", str(tmp_path)])
    assert "I am the source." in capsys.readouterr().out


def test_macro_ids_resolve_under_the_macro_tree(tmp_path: Path, capsys):
    """§9: shared macro evidence is reached with `_MACRO` as the ticker, since
    structured ids are reused across tickers."""
    d = _init(tmp_path, "_MACRO")
    write_structured(d, _fetch_meta("fred_dgs10", ticker="_MACRO"), {"obs": [4.2]})
    capsys.readouterr()

    assert sra.main(["show", "_MACRO", "fred_dgs10", "--data-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == {"obs": [4.2]}


def test_a_ticker_does_not_fall_back_to_macro(tmp_path: Path):
    """The caller names `_MACRO` explicitly; silently falling back would make
    `show PANW fred_dgs10` succeed and hide which tree the evidence is in."""
    _init(tmp_path, "PANW")
    macro = _init(tmp_path, "_MACRO")
    write_structured(macro, _fetch_meta("fred_dgs10", ticker="_MACRO"), {"obs": [4.2]})
    assert sra.main(["show", "PANW", "fred_dgs10", "--data-root", str(tmp_path)]) == 1


# --- failure modes --------------------------------------------------------

def test_unknown_id_exits_1_with_a_message(tmp_path: Path, capsys):
    _init(tmp_path)
    capsys.readouterr()
    assert sra.main(["show", "PANW", "no_such_id", "--data-root", str(tmp_path)]) == 1
    assert "no_such_id" in capsys.readouterr().err


def test_uninitialized_ticker_exits_1(tmp_path: Path):
    assert sra.main(["show", "MSFT", "anything", "--data-root", str(tmp_path)]) == 1


def test_traversal_ticker_exits_1(tmp_path: Path):
    assert sra.main(["show", "../evil", "anything", "--data-root", str(tmp_path)]) == 1


def test_traversal_id_is_refused(tmp_path: Path, capsys):
    """§8.4 check 7: the id is interpolated into a path, so an id carrying a
    separator or `..` must be refused rather than resolved outside the ticker
    directory."""
    d = _init(tmp_path)
    (d.parent / "secret.md").write_text("not evidence", encoding="utf-8")
    capsys.readouterr()
    for bad in ("../secret", "../../etc/passwd", "sub/dir", ".."):
        assert sra.main(["show", "PANW", bad, "--data-root", str(tmp_path)]) == 1
    assert "not evidence" not in capsys.readouterr().out


def test_malformed_json_is_printed_raw(tmp_path: Path, capsys):
    """A hand-mangled artifact should still be readable — you cannot fix what
    `show` refuses to display. `validate` (§8.4) is what makes it fatal."""
    d = _init(tmp_path)
    (d / "structured" / "broken.json").write_text("{ not json", encoding="utf-8")
    capsys.readouterr()
    assert sra.main(["show", "PANW", "broken", "--data-root", str(tmp_path)]) == 0
    assert "{ not json" in capsys.readouterr().out


def test_a_held_lock_does_not_block_show(tmp_path: Path):
    """`show` is read-only, so it takes no lock (§7.1)."""
    d = _init(tmp_path)
    write_source(d, _source_meta("2026-07-30_news_yahoo"), "Revenue grew.")
    (d / ".lock").write_text(json.dumps({
        "pid": 4242, "command": "prefetch",
        "acquired_at": "2026-08-11T12:00:00+00:00",
    }), encoding="utf-8")
    assert sra.main(["show", "PANW", "2026-07-30_news_yahoo",
                     "--data-root", str(tmp_path)]) == 0
