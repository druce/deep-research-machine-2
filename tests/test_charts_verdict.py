"""Verdict-dependent exhibits and the chartbook contract (§16.2, §16.3, §16.4).

The football field is the one chart in the system whose input is a model's
conclusion rather than a provider's number, which is exactly why §16.3 requires
`verdict` in its `data_sources`: a reader has to be able to tell "the model said
$250" from "Yahoo said $250". The first test here is that declaration.

The chartbook half tests the schema `sra.py assemble` will later depend on — a
selected name that resolves to no PNG is a hole in the assembled report, and it
is far cheaper to catch it here than at render time.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import sra
from lib.charts import registry, verdict
from tests.test_charts_tier1 import (
    ratios, write_peer, write_selection, write_structured)

FAIR_VALUE = 250.0


def write_verdict(ticker_dir: Path, run: str = "2026-08-11", **overrides) -> Path:
    payload = {
        "rating": "buy", "conviction": "medium", "fair_value": FAIR_VALUE,
        "horizon_months": 12, "current_price": 205.0,
        "implied_return_pct": 21.95, "valuation_method": "peer multiple",
        "thesis": "Platform consolidation is compounding.",
        "key_risk": "Seat growth stalls.", "base_case_probability": 0.55,
        "vs_consensus": "in line",
    }
    payload.update(overrides)
    run_dir = ticker_dir / "reports" / run
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "verdict.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_targets(ticker_dir: Path, low: float = 190.0, high: float = 280.0) -> Path:
    return write_structured(ticker_dir, "price_targets_yahoo",
                            {"price_targets": {"low": low, "high": high,
                                               "mean": 240.0, "current": 205.0},
                             "recent_actions": []})


def write_prices(ticker_dir: Path, lo: float = 150.0, hi: float = 230.0) -> Path:
    closes = [lo + (hi - lo) * (i % 50) / 49 for i in range(252)]
    dates = [f"2026-{1 + i // 21:02d}-{1 + i % 21:02d}" for i in range(252)]
    return write_structured(ticker_dir, "prices_yahoo",
                            {"daily": {"dates": dates, "open": closes,
                                       "high": closes, "low": closes,
                                       "close": closes,
                                       "volume": [1] * len(closes)},
                             "benchmark": None})


def manifest_of(result) -> dict:
    return json.loads(result.manifest_path.read_text())


# --- the verdict dependency -------------------------------------------------

def test_football_field_declares_the_verdict_as_an_input(tmp_ticker_dir: Path):
    """§16.3: "Their manifests must list `verdict` as an input dependency." It
    is the one chart whose number came from a model, and the manifest is where
    a reader finds that out."""
    write_verdict(tmp_ticker_dir)
    write_targets(tmp_ticker_dir)

    result = verdict.render_valuation_football_field(tmp_ticker_dir,
                                                     write_png=False)
    sources = manifest_of(result)["data_sources"]
    assert sources[0] == "verdict"
    assert "price_targets_yahoo" in sources


def test_football_field_refuses_without_a_verdict(tmp_ticker_dir: Path):
    write_targets(tmp_ticker_dir)
    assert verdict.render_valuation_football_field(tmp_ticker_dir,
                                                   write_png=False) is None


def test_football_field_refuses_without_a_fair_value(tmp_ticker_dir: Path):
    """A football field with no fair value line is a chart of ranges with no
    conclusion on it — the one thing the exhibit exists to place."""
    write_verdict(tmp_ticker_dir, fair_value=None)
    write_targets(tmp_ticker_dir)
    assert verdict.render_valuation_football_field(tmp_ticker_dir,
                                                   write_png=False) is None


def test_football_field_needs_at_least_one_band(tmp_ticker_dir: Path):
    write_verdict(tmp_ticker_dir)
    assert verdict.render_valuation_football_field(tmp_ticker_dir,
                                                   write_png=False) is None


# --- the bands --------------------------------------------------------------

def test_a_one_sided_target_range_is_dropped_not_drawn_from_zero(
        tmp_ticker_dir: Path):
    """Drawing a missing low as $0 would make the analyst band span the axis and
    swallow every other band on the chart."""
    write_verdict(tmp_ticker_dir)
    write_structured(tmp_ticker_dir, "price_targets_yahoo",
                     {"price_targets": {"high": 280.0, "current": 205.0}})
    assert verdict.render_valuation_football_field(tmp_ticker_dir,
                                                   write_png=False) is None


def test_the_fifty_two_week_band_comes_from_the_price_series(tmp_ticker_dir: Path):
    write_verdict(tmp_ticker_dir)
    write_prices(tmp_ticker_dir, lo=150.0, hi=230.0)
    result = verdict.render_valuation_football_field(tmp_ticker_dir,
                                                     write_png=False)
    caption = manifest_of(result)["auto_caption"]
    assert "52-week range $150-$230" in caption
    assert "prices_yahoo" in manifest_of(result)["data_sources"]


def test_the_peer_band_spans_the_cohort_not_a_median(tmp_ticker_dir: Path):
    """A single median multiplied out reads as a precision the comparison does
    not have."""
    root = tmp_ticker_dir.parent
    write_verdict(tmp_ticker_dir)
    subject = ratios()
    subject["per_share"] = {"eps_forward": 5.0}
    write_structured(tmp_ticker_dir, "key_ratios_computed", subject)
    write_selection(tmp_ticker_dir, ["CRWD", "ZS"])
    write_peer(root, "CRWD", forward_pe=70.0)
    write_peer(root, "ZS", forward_pe=40.0)

    result = verdict.render_valuation_football_field(tmp_ticker_dir,
                                                     write_png=False)
    caption = manifest_of(result)["auto_caption"]
    assert "Peer multiple $200-$350" in caption      # 40x and 70x on $5.00
    assert "peers_selected" in manifest_of(result)["data_sources"]


def test_a_fair_value_outside_every_band_is_flagged_in_salience(
        tmp_ticker_dir: Path):
    """Not a judgment — a fair value nobody's range supports is exactly the
    exhibit a reader should be shown."""
    write_verdict(tmp_ticker_dir, fair_value=400.0)
    write_targets(tmp_ticker_dir, low=190.0, high=280.0)
    result = verdict.render_valuation_football_field(tmp_ticker_dir,
                                                     write_png=False)
    assert "outside" in manifest_of(result)["salience"]["variance_note"]

    write_verdict(tmp_ticker_dir, fair_value=250.0)
    result = verdict.render_valuation_football_field(tmp_ticker_dir,
                                                     write_png=False)
    assert "inside" in manifest_of(result)["salience"]["variance_note"]


def test_the_caption_carries_the_method_and_the_implied_return(
        tmp_ticker_dir: Path):
    write_verdict(tmp_ticker_dir)
    write_targets(tmp_ticker_dir)
    caption = manifest_of(verdict.render_valuation_football_field(
        tmp_ticker_dir, write_png=False))["auto_caption"]
    assert "peer multiple" in caption
    assert "+21.9% implied" in caption
    assert "fair value from verdict.json" in caption


# --- the two passes ---------------------------------------------------------

def test_the_football_field_sits_out_the_first_pass():
    assert registry.RENDERERS["valuation_football_field"].requires_verdict is True


def test_dcf_is_not_built_from_a_verdict_that_carries_no_assumptions():
    """§16.1 puts DCF sensitivity in Tier 2 (no producer), and §15.3's verdict
    carries a fair value and a method string — no discount rate, no cash-flow
    forecast. Anything plotted would be invented here."""
    assert "dcf" not in " ".join(registry.RENDERERS)


def test_verdict_pass_renders_it_end_to_end(tmp_path: Path, capsys):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    d = tmp_path / "PANW"
    write_verdict(d)
    write_targets(d)

    capsys.readouterr()
    assert sra.main(["charts", "PANW", "--data-root", str(tmp_path),
                     "--verdict"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rendered"] == ["valuation_football_field"]
    assert (d / "charts" / "candidates" / "valuation_football_field.json").exists()


def test_the_first_pass_never_touches_the_verdict(tmp_path: Path, capsys):
    """§16.4's ordering exists so `charts T` can run before a conclusion does."""
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    d = tmp_path / "PANW"
    write_targets(d)

    capsys.readouterr()
    assert sra.main(["charts", "PANW", "--data-root", str(tmp_path)]) == 0
    assert "valuation_football_field" not in \
        json.loads(capsys.readouterr().out)["rendered"]


# --- the chartbook contract -------------------------------------------------

CHARTBOOK = {
    "selected": [
        {"name": "price_weekly", "section": "profile", "order": 1,
         "caption": "Weekly price. Source: Yahoo Finance, as of 2026-08-07."},
        {"name": "valuation_football_field", "section": "valuation", "order": 2,
         "caption": "Fair value against the bands. Source: verdict.json."},
    ]
}


def test_chartbook_schema_is_what_assemble_will_read():
    """§16.2's shape: `{selected: [{name, section, order, caption}]}`."""
    for entry in CHARTBOOK["selected"]:
        assert set(entry) == {"name", "section", "order", "caption"}
        assert isinstance(entry["order"], int)
        assert entry["caption"].strip()


def test_chartbook_orders_are_strictly_increasing():
    orders = [e["order"] for e in CHARTBOOK["selected"]]
    assert orders == sorted(set(orders))


def test_every_chartbook_section_is_a_real_report_section():
    from lib.sections import SECTION_IDS

    for entry in CHARTBOOK["selected"]:
        assert entry["section"] in SECTION_IDS


def test_every_chartbook_name_can_name_a_registered_renderer():
    """A selected name that resolves to no candidate is a hole in the report."""
    for entry in CHARTBOOK["selected"]:
        assert entry["name"] in registry.RENDERERS


@pytest.mark.parametrize("required", ["provider", "as-of", "10–16"])
def test_the_rubric_states_the_caption_and_target_rules(required):
    """§16.2: 10-16 exhibits, and every caption carries provider and as-of from
    bronze metadata. The prompt is where the selector learns both."""
    rubric = (Path(__file__).resolve().parent.parent
              / "prompts" / "chartbook.md").read_text(encoding="utf-8")
    assert required in rubric
