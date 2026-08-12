"""Chart base style, the renderer registry, and the `charts` command (§16, §17).

§17.1 is a palette that was validated, not chosen — the hexes here are pinned
against the spec text so a later "small tweak" to a color or a margin fails
rather than silently shipping a figure that no longer matches `report.css`.

The registry tests use fake renderers rather than real ones: Task 10.1 builds
the plumbing, and the plumbing's contract (a renderer may return `None`, the
verdict split is by flag, a manifest carries §16.1's keys) has to hold for
every renderer added later.
"""
from __future__ import annotations

import json
from pathlib import Path

import plotly.graph_objects as go
import pytest

import sra
from lib.charts import base, registry


# --- §17.1 palette ---------------------------------------------------------

def test_chrome_and_ink_are_the_spec_hexes():
    assert base.INK == "#23282f"
    assert base.MUTED == "#5b636e"
    assert base.NAVY == "#0f2942"
    assert base.RULE == "#dde1e6"
    assert base.GRID == "#eef0f3"


def test_price_series_slots_are_fixed_assignments():
    """§17.1: "Never cycle these assignments." The slot IS the meaning — a
    reader who learns orange = relative strength on one exhibit must not meet
    orange as a moving average on the next."""
    assert base.SERIES_SLOTS == ("#2a78d6", "#4a3aa7", "#5b636e", "#eb6834")
    assert base.S1_MA13 == "#2a78d6"
    assert base.S2_MA52 == "#4a3aa7"
    assert base.S3_VOLUME == "#5b636e"
    assert base.S4_RS == "#eb6834"


def test_categorical_set_and_its_fold_limit():
    assert base.CATEGORICAL == ("#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7")
    assert base.CATEGORICAL_MAX == len(base.CATEGORICAL)
    assert base.OTHER_LABEL == "Other"


def test_third_categorical_color_needs_direct_labeling():
    """§17.1: #1baf7a measures 2.82:1 on white, below the 3:1 swatch threshold,
    so it may not be identified by a legend swatch alone."""
    assert base.NEEDS_DIRECT_LABEL == ("#1baf7a",)


def test_status_colors_and_the_semantic_restriction():
    assert base.UP == "#1a7f37"
    assert base.DOWN == "#b3261e"
    assert base.SEMANTIC_COLOR_USES == ("candles", "sankey")


def test_export_geometry():
    assert base.CHART_WIDTH == 980
    assert base.CHART_SCALE == 2
    assert base.PRICE_HEIGHT == 520
    assert base.SANKEY_HEIGHT == 420
    assert base.MARGINS == dict(l=52, r=64, t=8, b=28)


def test_font_stack_matches_the_report_css():
    assert base.FONT_FAMILY == "Helvetica Neue, Helvetica, Arial, sans-serif"
    assert base.FONT_SIZE == 11
    assert base.BASE_FONT["color"] == base.INK


def test_rgba_is_a_transform_of_the_same_constants():
    """The Sankey's link colors are the status colors at low alpha (§17.3);
    deriving them keeps node and link provably in step."""
    assert base.rgba(base.UP, 0.85) == "rgba(26,127,55,0.85)"
    assert base.rgba(base.DOWN, 0.16) == "rgba(179,38,30,0.16)"
    with pytest.raises(ValueError):
        base.rgba("#abc", 0.5)


# --- §17.4 base layout -----------------------------------------------------

def test_base_layout_strips_plotly_defaults():
    """§17.4: horizontal gridlines only, no box, no zero line, no rangeslider,
    and no title — the report template owns the exhibit heading."""
    fig = go.Figure(go.Scatter(x=["2026-01-01"], y=[1]))
    base.apply_base_layout(fig)
    layout = fig.layout

    assert layout.title.text is None
    assert layout.showlegend is False
    assert layout.width == base.CHART_WIDTH
    assert layout.margin.l == 52 and layout.margin.t == 8
    assert layout.yaxis.showgrid is True and layout.yaxis.gridcolor == base.GRID
    assert layout.xaxis.showgrid is False
    assert layout.yaxis.zeroline is False and layout.xaxis.zeroline is False
    assert layout.plot_bgcolor == "#ffffff"
    assert layout.font.family == base.FONT_FAMILY


def test_base_layout_removes_the_rangeslider_on_a_candlestick():
    """Plotly turns the rangeslider ON by default for candlesticks, and it
    prints as a gray smear at PDF scale."""
    fig = go.Figure(go.Candlestick(x=["2026-01-01"], open=[1], high=[2],
                                   low=[0], close=[1.5]))
    base.apply_base_layout(fig)
    assert fig.layout.xaxis.rangeslider.visible is False


# --- §16.1 candidate manifest ----------------------------------------------

def test_write_candidate_emits_png_and_manifest(tmp_path: Path):
    fig = go.Figure(go.Scatter(x=["2026-01-01", "2026-02-01"], y=[1, 2]))
    base.apply_base_layout(fig)
    result = base.write_candidate(
        tmp_path, fig, name="demo", title="Demo",
        data_sources=["prices_yahoo"], auto_caption="Yahoo Finance, as of 2026-07-30",
        salience={"recency_days": 3, "coverage": 0.92, "variance_note": "n/a"},
        height=400, write_png=False)

    assert isinstance(result, base.ChartResult)
    assert result.manifest_path == tmp_path / "charts" / "candidates" / "demo.json"
    assert result.png_path == tmp_path / "charts" / "candidates" / "demo.png"

    manifest = json.loads(result.manifest_path.read_text())
    assert set(manifest) == {"name", "title", "data_sources", "derived_from_urls",
                             "auto_caption", "salience"}
    assert manifest["data_sources"] == ["prices_yahoo"]
    assert manifest["derived_from_urls"] == []
    assert set(manifest["salience"]) == {"recency_days", "coverage", "variance_note"}


def test_write_candidate_rejects_an_undeclared_lineage(tmp_path: Path):
    """§16.1/§8.1: a candidate whose manifest names no input cannot be traced
    back to evidence, and its caption's provider claim is unverifiable."""
    fig = go.Figure(go.Scatter(x=["2026-01-01"], y=[1]))
    with pytest.raises(ValueError, match="data_sources"):
        base.write_candidate(tmp_path, fig, name="demo", title="Demo",
                             data_sources=[], auto_caption="x",
                             salience={}, height=400, write_png=False)


def test_write_candidate_rejects_a_path_traversing_name(tmp_path: Path):
    fig = go.Figure(go.Scatter(x=["2026-01-01"], y=[1]))
    with pytest.raises(ValueError):
        base.write_candidate(tmp_path, fig, name="../escape", title="Demo",
                             data_sources=["prices_yahoo"], auto_caption="x",
                             salience={}, height=400, write_png=False)


# --- the registry ----------------------------------------------------------

def test_registry_splits_on_requires_verdict():
    """§16.3/§16.4: conclusion-dependent exhibits render in a separate pass,
    after the polish chain has produced verdict.json."""
    fake = {
        "plain": registry.Renderer("plain", lambda d: None),
        "field": registry.Renderer("field", lambda d: None, requires_verdict=True),
    }
    assert list(registry.select(fake, verdict=False)) == ["plain"]
    assert list(registry.select(fake, verdict=True)) == ["field"]


def test_every_registered_renderer_agrees_with_its_key():
    for name, renderer in registry.RENDERERS.items():
        assert renderer.name == name
        assert callable(renderer.fn)
        assert isinstance(renderer.requires_verdict, bool)


# --- the `charts` command --------------------------------------------------

def _init(tmp_path: Path) -> Path:
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    return tmp_path / "PANW"


def run(tmp_path: Path, *args) -> int:
    return sra.main([*args, "--data-root", str(tmp_path)])


def out(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def a_chart(ticker_dir: Path, name: str) -> base.ChartResult:
    fig = go.Figure(go.Scatter(x=["2026-01-01"], y=[1]))
    base.apply_base_layout(fig)
    return base.write_candidate(ticker_dir, fig, name=name, title=name,
                                data_sources=["prices_yahoo"], auto_caption="c",
                                salience={}, height=400, write_png=False)


def test_charts_renders_the_verdict_independent_set(tmp_path: Path, capsys,
                                                    monkeypatch):
    d = _init(tmp_path)
    monkeypatch.setattr(sra, "RENDERERS", {
        "one": registry.Renderer("one", lambda t: a_chart(t, "one")),
        "later": registry.Renderer("later", lambda t: a_chart(t, "later"),
                                   requires_verdict=True),
    })
    capsys.readouterr()
    assert run(tmp_path, "charts", "PANW") == 0
    payload = out(capsys)
    assert payload["rendered"] == ["one"]
    assert (d / "charts" / "candidates" / "one.json").exists()
    assert not (d / "charts" / "candidates" / "later.json").exists()


def test_a_renderer_returning_none_is_normal_degradation(tmp_path: Path, capsys,
                                                         monkeypatch):
    """§16.1: `None` means the inputs are not there. It is reported as skipped,
    writes nothing, and does not fail the command."""
    d = _init(tmp_path)
    monkeypatch.setattr(sra, "RENDERERS", {
        "absent": registry.Renderer("absent", lambda t: None),
    })
    capsys.readouterr()
    assert run(tmp_path, "charts", "PANW") == 0
    payload = out(capsys)
    assert payload["rendered"] == [] and payload["skipped"] == ["absent"]
    assert not any((d / "charts" / "candidates").iterdir())


def test_a_raising_renderer_is_an_error_not_a_crash(tmp_path: Path, capsys,
                                                    monkeypatch):
    """One broken renderer must not cost the other exhibits."""
    def boom(ticker_dir: Path):
        raise ValueError("bad input")

    _init(tmp_path)
    monkeypatch.setattr(sra, "RENDERERS", {
        "ok": registry.Renderer("ok", lambda t: a_chart(t, "ok")),
        "boom": registry.Renderer("boom", boom),
    })
    capsys.readouterr()
    assert run(tmp_path, "charts", "PANW") == 2
    payload = out(capsys)
    assert payload["rendered"] == ["ok"]
    assert "bad input" in payload["errors"]["boom"]


def test_verdict_pass_refuses_without_a_verdict(tmp_path: Path, monkeypatch):
    """§16.3: these exhibits READ verdict.json. Rendering them without one
    would silently produce a football field with no fair value on it."""
    _init(tmp_path)
    monkeypatch.setattr(sra, "RENDERERS", {
        "field": registry.Renderer("field", lambda t: a_chart(t, "field"),
                                   requires_verdict=True),
    })
    assert run(tmp_path, "charts", "PANW", "--verdict") == 1


def test_verdict_pass_runs_once_the_verdict_exists(tmp_path: Path, capsys,
                                                   monkeypatch):
    d = _init(tmp_path)
    run_dir = d / "reports" / "2026-08-11"
    run_dir.mkdir(parents=True)
    (run_dir / "verdict.json").write_text(json.dumps({"rating": "buy",
                                                      "fair_value": 250.0}))
    monkeypatch.setattr(sra, "RENDERERS", {
        "field": registry.Renderer("field", lambda t: a_chart(t, "field"),
                                   requires_verdict=True),
    })
    capsys.readouterr()
    assert run(tmp_path, "charts", "PANW", "--verdict") == 0
    assert out(capsys)["rendered"] == ["field"]


def test_load_verdict_prefers_the_latest_symlink(tmp_path: Path):
    d = _init(tmp_path)
    old = d / "reports" / "2026-01-01"
    new = d / "reports" / "2026-08-11"
    for run_dir, fair in ((old, 100.0), (new, 250.0)):
        run_dir.mkdir(parents=True)
        (run_dir / "verdict.json").write_text(json.dumps({"fair_value": fair}))
    assert base.load_verdict(d)["fair_value"] == 250.0

    (d / "reports" / "latest").symlink_to(old.name)
    assert base.load_verdict(d)["fair_value"] == 100.0


def test_charts_needs_an_initialized_ticker(tmp_path: Path):
    assert run(tmp_path, "charts", "PANW") == 1


# --- the actual PNG --------------------------------------------------------

@pytest.mark.integration
def test_write_candidate_exports_a_real_png(tmp_path: Path):
    """kaleido drives a headless browser: ~7s cold, which is why every other
    test here passes `write_png=False`. This one proves the export path works
    at all, including the orjson trap that makes a `pd.Timestamp` x-value fail
    only at write time."""
    fig = go.Figure(go.Scatter(x=["2026-01-01", "2026-02-01"], y=[1, 2]))
    base.apply_base_layout(fig)
    result = base.write_candidate(
        tmp_path, fig, name="demo", title="Demo",
        data_sources=["prices_yahoo"], auto_caption="c", salience={},
        height=400)
    assert result.png_path.stat().st_size > 5_000
