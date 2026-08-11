"""Secret scanning in the fatal gate (spec §5, §8.4 check 6).

Redaction happens at the fetch boundary (§11.1, §12.1); this scan is the
backstop, not the mechanism. It matters most for a key the current environment
no longer holds — a rotated credential already written into an artifact is
invisible to any env-value comparison, which is why the patterns exist.
"""
from __future__ import annotations

import json
from pathlib import Path

from lib.provenance import SourceMeta, StructuredMeta, write_source, write_structured
from lib.validate import Finding, validate

FMP_KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"          # 32 hex, FMP shape
FRED_KEY = "0123456789abcdef0123456789abcdef"          # 32 lowercase hex, FRED shape
OPENAI_KEY = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


def _source_meta(source_id: str, **overrides) -> SourceMeta:
    fields = dict(
        id=source_id, ticker="PANW", kind="news", source="yahoo",
        url="https://example.com/x", fetched_at="2026-07-30T12:00:00+00:00",
        as_of="2026-07-30", title="A headline", fetch_tool="httpx",
        fetch_cmd="sra.py prefetch PANW --kinds news",
    )
    fields.update(overrides)
    return SourceMeta(**fields)


def _plant_json(ticker_dir: Path, relpath: str, payload: dict) -> Path:
    path = ticker_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _errors(ticker_dir: Path) -> list[Finding]:
    return [f for f in validate(ticker_dir, ticker_dir.parent) if f.severity == "error"]


def _secret_findings(ticker_dir: Path) -> list[Finding]:
    return [f for f in _errors(ticker_dir) if f.code == "secret"]


# --- literal env values ---------------------------------------------------

def test_a_live_env_key_appearing_in_an_artifact_is_an_error(
        tmp_ticker_dir: Path, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "SUPERSECRETVALUE123456")
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"),
                 "The key is SUPERSECRETVALUE123456.")
    assert _secret_findings(tmp_ticker_dir)


def test_the_key_value_is_never_echoed_in_the_finding(
        tmp_ticker_dir: Path, monkeypatch):
    """A gate that prints the secret it found puts it in CI logs — §5's rule
    is that no raw provider key appears in any log line either."""
    monkeypatch.setenv("FMP_API_KEY", "SUPERSECRETVALUE123456")
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"),
                 "The key is SUPERSECRETVALUE123456.")
    finding = _secret_findings(tmp_ticker_dir)[0]
    assert "SUPERSECRETVALUE123456" not in finding.message
    assert "FMP_API_KEY" in finding.message


def test_an_empty_env_var_does_not_match_everything(
        tmp_ticker_dir: Path, monkeypatch):
    """An unset or empty key must not turn into a substring that matches every
    file — that would make the gate fire on a clean tree."""
    monkeypatch.setenv("FMP_API_KEY", "")
    monkeypatch.setenv("FRED_API_KEY", "")
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"), "Revenue grew.")
    assert _secret_findings(tmp_ticker_dir) == []


# --- provider key patterns ------------------------------------------------

def test_an_openai_key_pattern_is_an_error(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"),
                 f"Authorization: Bearer {OPENAI_KEY}")
    assert _secret_findings(tmp_ticker_dir)


def test_a_bare_fred_shaped_key_is_an_error(tmp_ticker_dir: Path):
    """§8.4: pattern matching is what catches a rotated key the current
    environment no longer holds."""
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"),
                 f"api_key={FRED_KEY}")
    assert _secret_findings(tmp_ticker_dir)


def test_an_fmp_shaped_key_in_credential_context_is_an_error(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"),
                 f"https://financialmodelingprep.com/stable/quote?apikey={FMP_KEY}")
    assert _secret_findings(tmp_ticker_dir)


def test_a_mixed_case_hex_string_without_credential_context_is_ignored(
        tmp_ticker_dir: Path):
    """A 32-char mixed-case hex string with nothing key-like around it is far
    more likely a checksum than a credential; flagging it would train people to
    ignore the gate."""
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"),
                 f"The document digest is {FMP_KEY.upper()}.")
    assert _secret_findings(tmp_ticker_dir) == []


def test_a_clean_tree_produces_no_secret_findings(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"), "Revenue grew.")
    assert _secret_findings(tmp_ticker_dir) == []


# --- credential query parameters -----------------------------------------

def test_a_credential_query_param_in_a_recorded_url_is_an_error(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir,
                 _source_meta("2026-07-30_news_yahoo",
                              url="https://api.example.com/v1/quote?symbol=PANW&apikey=zzz"),
                 "Revenue grew.")
    findings = _secret_findings(tmp_ticker_dir)
    assert findings
    assert any("apikey" in f.message for f in findings)


def test_a_token_query_param_is_an_error(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir,
                 _source_meta("2026-07-30_news_yahoo",
                              url="https://api.example.com/v1/q?token=abc123"),
                 "Revenue grew.")
    assert _secret_findings(tmp_ticker_dir)


def test_a_blanked_credential_param_is_still_an_error(tmp_ticker_dir: Path):
    """§5: a credential parameter must be ABSENT, not blanked — a masked value
    still records that the call carried a key, and the next person to 'fix' the
    fetcher fills it back in."""
    _plant_json(tmp_ticker_dir, "structured/prices_yahoo.json", {
        "_meta": {
            "id": "prices_yahoo", "ticker": "PANW", "producer": "fetch",
            "title": "Prices", "source": "fmp", "url": "https://example.com/p",
            "as_of": "2026-07-30", "provider_tool": "httpx",
            "fetch_cmd": "sra.py prefetch PANW --kinds prices",
            "fetched_at": "2026-07-30T12:00:00+00:00",
            "request": {"endpoint": "https://example.com/p",
                        "params": {"symbol": "PANW", "apikey": ""}},
        },
        "data": {},
    })
    assert _secret_findings(tmp_ticker_dir)


def test_an_ordinary_query_param_is_fine(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir,
                 _source_meta("2026-07-30_news_yahoo",
                              url="https://api.example.com/v1/q?symbol=PANW&limit=10"),
                 "Revenue grew.")
    assert _secret_findings(tmp_ticker_dir) == []


# --- coverage: every file, not just bronze -------------------------------

def test_answer_files_are_scanned(tmp_ticker_dir: Path):
    """§8.4: the scan covers every artifact, log line and ANSWER file — an
    answer records what a researcher saw, including any URL they pasted."""
    answers = tmp_ticker_dir / "derived" / "answers"
    answers.mkdir(parents=True, exist_ok=True)
    (answers / "2026-07-30_answer_moat.md").write_text(
        f"---\nid: x\n---\n\nFetched from ?apikey={FMP_KEY}\n", encoding="utf-8")
    assert _secret_findings(tmp_ticker_dir)


def test_the_wiki_log_is_scanned(tmp_ticker_dir: Path):
    (tmp_ticker_dir / "wiki").mkdir(parents=True, exist_ok=True)
    (tmp_ticker_dir / "wiki" / "log.md").write_text(
        f"- 2026-07-30 fetched with {OPENAI_KEY}\n", encoding="utf-8")
    assert _secret_findings(tmp_ticker_dir)


def test_the_state_file_is_scanned(tmp_ticker_dir: Path):
    (tmp_ticker_dir / ".state.json").write_text(json.dumps({
        "ticker": "PANW", "data": {}, "derived": {}, "wiki": {},
        "report": {"last_generated": None, "sections_dirty": []},
        "note": f"key {OPENAI_KEY}",
    }), encoding="utf-8")
    assert _secret_findings(tmp_ticker_dir)


def test_reports_are_scanned(tmp_ticker_dir: Path):
    run = tmp_ticker_dir / "reports" / "2026-08-11"
    run.mkdir(parents=True, exist_ok=True)
    (run / "report.md").write_text(f"Fetched with {OPENAI_KEY}\n", encoding="utf-8")
    assert _secret_findings(tmp_ticker_dir)


def test_a_clean_structured_artifact_with_a_request_block_is_fine(tmp_ticker_dir: Path):
    """The sanctioned shape: endpoint plus non-credential params, key omitted."""
    write_structured(tmp_ticker_dir, StructuredMeta(
        id="prices_fmp", ticker="PANW", producer="fetch", title="Prices",
        source="fmp", url="https://financialmodelingprep.com/stable/quote",
        as_of="2026-07-30", provider_tool="httpx",
        fetch_cmd="sra.py prefetch PANW --kinds prices",
        fetched_at="2026-07-30T12:00:00+00:00",
        request={"endpoint": "https://financialmodelingprep.com/stable/quote",
                 "params": {"symbol": "PANW"}},
    ), {"close": [1]})
    assert _secret_findings(tmp_ticker_dir) == []
