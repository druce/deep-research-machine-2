#!/usr/bin/env python3
"""SEC EDGAR fetcher: immutable per-filing sources + structured statement extract."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from lib.fetchers.common import fetch_cmd, find_prior_source, json_safe, source_exists
from lib.fetchers.sec_text_cleaner import clean_sec_text, is_material_8k
from lib.provenance import (SourceMeta, StructuredMeta, read_source, write_source,
                            write_structured)
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()

# sra5 skills/config.py values, carried over with the extraction below.
SEC_LOOKBACK_DAYS = 365
SEC_10K_ITEMS = ["Item 1", "Item 1A", "Item 3", "Item 7", "Item 7A"]

# PART-QUALIFIED, and both halves matter.
#
# A 10-Q reuses item numbers across its two parts — Item 1 is Financial
# Statements in Part I and Legal Proceedings in Part II — so a bare "Item 1"
# resolves to whichever edgartools finds first. Qualifying the key is what makes
# both reachable.
#
# This was `["Item 2"]`: MD&A alone. Everything an analyst actually cites — the
# statements, the notes, the risk factors, the legal proceedings — was silently
# absent, while the MD&A text kept referring to "Note 9" and "Note 16 ...
# included elsewhere in this Quarterly Report" that were never fetched. On the
# SPCX build (a company two months public, so a 10-Q is its ONLY periodic
# filing) Part I Item 1 is 93KB against MD&A's 66KB, and four separate agents
# reported the concentration, termination and contingency disclosures as
# unfillable gaps before one of them went around the pipeline to EDGAR directly.
SEC_10Q_ITEMS = ["Part I Item 1", "Part I Item 2", "Part I Item 3",
                 "Part II Item 1", "Part II Item 1A"]

# Ids follow the contract naming (`<filing-date>_sec_10k`), not make_source_id --
# which would yield `<date>_sec_filing_10k`. The canonical `sec_filing` kind still
# goes in the frontmatter.
FORM_NAMES = {"sec_10k": "10-K", "sec_10q": "10-Q"}
ITEM_10K_LABELS = {"Item 1": "Business", "Item 1A": "Risk Factors",
                   "Item 3": "Legal Proceedings", "Item 7": "MD&A",
                   "Item 7A": "Market Risk Disclosures"}
ITEM_10Q_LABELS = {"Part I Item 1": "Financial Statements and Notes",
                   "Part I Item 2": "MD&A",
                   "Part I Item 3": "Market Risk Disclosures",
                   "Part II Item 1": "Legal Proceedings",
                   "Part II Item 1A": "Risk Factors"}
# Copy of _8K_ITEM_MAP from sra5 skills/fetch_edgar/filing_items.py.
EIGHTK_ITEM_LABELS = {
    "Item 1.01": "Entry into a Material Definitive Agreement",
    "Item 1.02": "Termination of a Material Definitive Agreement",
    "Item 1.03": "Bankruptcy or Receivership",
    "Item 2.01": "Completion of Acquisition or Disposition of Assets",
    "Item 2.02": "Results of Operations and Financial Condition",
    "Item 2.03": "Creation of a Direct Financial Obligation",
    "Item 2.04": "Triggering Events That Accelerate an Obligation",
    "Item 2.05": "Costs Associated with Exit or Disposal Activities",
    "Item 2.06": "Material Impairments",
    "Item 3.01": "Notice of Delisting or Transfer",
    "Item 3.02": "Unregistered Sales of Equity Securities",
    "Item 3.03": "Material Modification to Rights of Security Holders",
    "Item 4.01": "Changes in Registrant's Certifying Accountant",
    "Item 4.02": "Non-Reliance on Previously Issued Financial Statements",
    "Item 5.01": "Changes in Control of Registrant",
    "Item 5.02": "Departure/Appointment of Directors or Officers",
    "Item 5.03": "Amendments to Articles of Incorporation or Bylaws",
    "Item 5.04": "Temporary Suspension of Trading Under Employee Benefit Plans",
    "Item 5.05": "Amendments to Code of Ethics",
    "Item 5.06": "Change in Shell Company Status",
    "Item 5.07": "Submission of Matters to a Vote of Security Holders",
    "Item 5.08": "Shareholder Nominations",
    "Item 7.01": "Regulation FD Disclosure",
    "Item 8.01": "Other Events",
    "Item 9.01": "Financial Statements and Exhibits",
}
# Item text this short is a parse remnant or a bare exhibit stub, not an event
# worth its own immutable source file (sra5 fetch_edgar.get_recent_8k).
MIN_8K_CONTENT_CHARS = 50
FETCH_TOOL = "lib/fetchers/edgar.py"
STATEMENT_ATTRS = ("income_statement", "balance_sheet", "cash_flow_statement")
_DATED_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _browse_url(ticker: str, form: str) -> str:
    """EDGAR full-text search fallback when a filing exposes no URL of its own."""
    return ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&company={ticker.upper()}&type={form}")


# --- default provider, ported from sra5 skills/fetch_edgar/ --------------------------
# `import edgar` is function-local everywhere below: edgartools sets a process-global
# SEC identity and creates ~/.edgar on use, so neither `import sra` nor pytest
# collection may depend on the package being installed or configured.

def _init_edgar() -> None:
    """Set the SEC identity from SEC_FIRM/SEC_USER (sra5 filing_items.init_edgar).

    Raises RuntimeError when the env vars are unset -- SEC blocks unidentified
    clients, so there is no useful degraded mode.
    """
    import edgar  # local import: see module note above

    firm = os.environ.get("SEC_FIRM", "").strip()
    user = os.environ.get("SEC_USER", "").strip()
    if not firm or not user:
        raise RuntimeError(
            "SEC_FIRM and SEC_USER environment variables are required for SEC EDGAR")
    edgar.set_identity(f"{firm} {user}")


def _get_company(symbol: str):
    """edgartools Company (sra5 filing_items.get_company); LookupError when unknown."""
    import edgar  # local import: see module note above

    try:
        company = edgar.Company(symbol)
    except Exception as exc:
        raise LookupError(f"no SEC company for {symbol}: {exc}") from exc
    if company is None or getattr(company, "not_found", False):
        raise LookupError(f"no SEC company for {symbol}")
    return company


def _filing_date(filing) -> str | None:
    """`filing.filing_date` as YYYY-MM-DD, whatever type edgartools handed back."""
    raw = getattr(filing, "filing_date", None)
    if raw is None:
        return None
    # date/datetime -> formatted; a string keeps its date half ("2026-06-02 00:00:00").
    text = (raw.strftime("%Y-%m-%d") if hasattr(raw, "strftime")
            else str(raw).strip()[:10])
    return text if _DATE_RE.fullmatch(text) else None


def _latest_form(company, ticker: str, form: str, item_keys: list[str]) -> dict | None:
    """Latest 10-K/10-Q with its cleaned items (sra5 get_10k_items / get_10q_items).

    Returns the dict the provider contract asks for instead of writing files.
    """
    filings = company.get_filings(form=form)
    if filings is None or len(filings) == 0:
        return None
    # .latest() is edgartools' own "most recent" accessor -- don't assume index order.
    filing = filings.latest() if hasattr(filings, "latest") else filings[0]
    if filing is None:
        return None
    obj = filing.obj()
    items: dict[str, str] = {}
    for key in item_keys:
        try:
            text = obj[key]
        except Exception:  # per-item extraction is best-effort
            continue
        if text is None or not str(text).strip():
            continue
        cleaned = clean_sec_text(str(text).strip(), form_type=form)
        if cleaned:
            items[key] = cleaned
    return {
        "form": str(getattr(filing, "form", "") or form),
        "filing_date": _filing_date(filing),
        "period_end": str(getattr(filing, "period_of_report", "") or "") or None,
        "accession": str(getattr(filing, "accession_number", None)
                         or getattr(filing, "accession_no", "") or "unknown"),
        "url": (getattr(filing, "filing_url", None) or getattr(filing, "url", None)
                or _browse_url(ticker, form)),
        "items": items,
    }


def _items_reported(filing) -> list[str]:
    """8-K item codes off the typed filing object; [] when they cannot be parsed."""
    try:
        obj = filing.obj()
    except Exception:
        return []
    raw = getattr(obj, "items", None)
    if callable(raw):
        raw = raw()
    if isinstance(raw, dict):
        return [str(i) for i in raw]
    if isinstance(raw, (list, tuple)):
        return [str(i) for i in raw]
    return [str(raw)] if raw is not None else []


def _recent_8ks(company, ticker: str, now: datetime) -> list[dict]:
    """Every 8-K within SEC_LOOKBACK_DAYS, newest first (sra5 get_recent_8k).

    Materiality filtering deliberately stays in fetch_filings: the provider is dumb
    so the filter can be tested offline.
    """
    filings = company.get_filings(form="8-K")
    if filings is None:
        return []
    cutoff = (now - timedelta(days=SEC_LOOKBACK_DAYS)).date().isoformat()
    out: list[dict] = []
    for filing in filings:
        date = _filing_date(filing)
        if date is None or date < cutoff:
            continue  # `continue`, not `break`: never assume the SEC's order
        content = ""
        for getter in ("markdown", "text"):
            try:
                content = getattr(filing, getter)() or ""
            except Exception:
                content = ""
            if content.strip():
                break
        out.append({
            "filing_date": date,
            "accession": str(getattr(filing, "accession_number", None)
                             or getattr(filing, "accession_no", "") or "unknown"),
            "url": (getattr(filing, "filing_url", None) or getattr(filing, "url", None)
                    or _browse_url(ticker, "8-K")),
            "items_reported": _items_reported(filing),
            "content": clean_sec_text(content, form_type="8-K"),
        })
    out.sort(key=lambda f: f["filing_date"], reverse=True)
    return out


def _statements(financials_obj) -> dict:
    """{statement_name: dataframe-as-dict} (sra5 _save_financials_object, no CSVs).

    Values go through json_safe: an edgartools statement frame is full of NaN holes
    (unused dimension columns), and json.dumps would emit bare `NaN` -- readable by
    Python but not valid JSON for anything else.
    """
    out: dict = {}
    for attr in STATEMENT_ATTRS:
        statement = getattr(financials_obj, attr, None)
        if statement is None:
            continue
        try:
            if callable(statement):  # edgartools >=5.x exposes these as methods
                statement = statement()
            df = None
            for conv in ("to_dataframe", "to_pandas"):
                if hasattr(statement, conv):
                    df = getattr(statement, conv)()
                    break
            if df is None and hasattr(statement, "to_dict"):
                df = statement  # already a DataFrame
            if df is None:
                continue
            out[attr] = {str(col): {str(row): json_safe(val)
                                    for row, val in column.items()}
                         for col, column in df.to_dict().items()}
        except Exception:  # one unparseable statement must not lose the others
            continue
    return out


def _financials(company) -> dict | None:
    """Annual + quarterly statement dicts (sra5 get_financials); None when neither."""
    out: dict = {}
    for label, attr, getter in (("annual", "financials", "get_financials"),
                                ("quarterly", "quarterly_financials",
                                 "get_quarterly_financials")):
        try:
            obj = getattr(company, attr, None)
            if obj is None and hasattr(company, getter):
                obj = getattr(company, getter)()
            out[label] = _statements(obj) if obj is not None else None
        except Exception:  # a missing half is data, not a failure
            out[label] = None
    return out if any(out.values()) else None


def _edgartools_provider(ticker: str) -> dict:
    """Default provider: the whole EDGAR payload for `ticker` as plain dicts."""
    _init_edgar()
    company = _get_company(ticker)
    now = datetime.now(timezone.utc)
    return {
        "tenk": _latest_form(company, ticker, "10-K", SEC_10K_ITEMS),
        "tenq": _latest_form(company, ticker, "10-Q", SEC_10Q_ITEMS),
        "eightks": _recent_8ks(company, ticker, now),
        "financials": _financials(company),
    }


# --- writers -------------------------------------------------------------------------

def is_amendment(filing: dict) -> bool:
    """Is this filing an amendment (10-K/A over its 10-K)?

    §5's supersede rule turns on this. A NEW 10-K is not a replacement for last
    year's 10-K — it is the next period, and both remain true evidence. Only an
    amendment restates a filing that is already on disk.
    """
    return str(filing.get("form", "")).strip().upper().endswith("/A")


def _superseded_id(ticker_dir: Path, filing: dict, form_slug: str) -> str | None:
    """The stored id this filing REPLACES, or None.

    Non-empty only for an amendment, and only when the original it amends is
    actually on disk: `supersedes:` naming an id that does not resolve would
    fail §8.4's derivation check, and archiving depends on finding the file.
    """
    if not is_amendment(filing):
        return None
    prior = find_prior_source(ticker_dir, form_slug)
    return prior if prior and source_exists(ticker_dir, prior) else None


def _write_filing_source(ticker: str, ticker_dir: Path, now: datetime, *, sid: str,
                         form_slug: str, filing: dict, item_labels: dict[str, str],
                         ) -> Path | None:
    """Write one 10-K/10-Q source file; None when it already exists or has no items."""
    items = {k: v for k, v in (filing.get("items") or {}).items() if v and v.strip()}
    if not items or source_exists(ticker_dir, sid):
        return None
    prior = _superseded_id(ticker_dir, filing, form_slug)
    form_name = FORM_NAMES[form_slug]
    sections = [f"# {form_name} (filed {filing['filing_date']}, "
                f"accession {filing['accession']})"]
    for key, label in item_labels.items():
        if key in items:
            sections.append(f"## {label}\n\n{items[key]}")
    meta = SourceMeta(
        id=sid, ticker=ticker.upper(), kind="sec_filing", source="SEC EDGAR",
        url=filing["url"], fetched_at=now.isoformat(),
        as_of=filing.get("period_end") or filing["filing_date"],
        title=f"{ticker.upper()} {form_name} filed {filing['filing_date']}",
        fetch_tool=FETCH_TOOL, fetch_cmd=fetch_cmd(ticker, "filings"),
        supersedes=prior if prior != sid else None)
    return write_source(ticker_dir, meta, "\n\n".join(sections))


def _stored_8k_ids_by_url(ticker_dir: Path, filing_date: str) -> dict[str, str]:
    """{url: source_id} for the 8-K sources already stored under `filing_date`.

    8-K ids are date-based, so several filings on one date share a base id. Matching
    an incoming filing on its (unique) URL is what makes a re-run a no-op even when
    a newer 8-K lands on a date that already has one.
    """
    out: dict[str, str] = {}
    for path in sorted((ticker_dir / "sources").glob(f"{filing_date}_sec_8k*.md")):
        try:
            meta, _ = read_source(path)
        except Exception:  # a hand-edited source must not break the fetch
            continue
        out[meta.url] = meta.id
    return out


def _next_8k_id(ticker_dir: Path, base: str, claimed: set[str]) -> str:
    """First free `<date>_sec_8k[_n]` id, skipping ids on disk or taken this run."""
    n = 1
    while True:
        sid = base if n == 1 else f"{base}_{n}"
        if sid not in claimed and not source_exists(ticker_dir, sid):
            return sid
        n += 1


def _write_8k_sources(ticker: str, ticker_dir: Path, now: datetime,
                      eightks: list[dict]) -> tuple[list[Path], list[str]]:
    """Write a source per material 8-K. Returns (written paths, ids present after)."""
    paths: list[Path] = []
    present: list[str] = []
    claimed: set[str] = set()
    stored: dict[str, dict[str, str]] = {}
    for ek in eightks:
        date = ek.get("filing_date")
        if not date:
            continue
        items_reported = ek.get("items_reported") or []
        if not is_material_8k(items_reported):
            continue
        content = (ek.get("content") or "").strip()
        if len(content) < MIN_8K_CONTENT_CHARS:
            continue  # near-empty item text: nothing worth an immutable source
        by_url = stored.setdefault(date, _stored_8k_ids_by_url(ticker_dir, date))
        if ek.get("url") in by_url:            # already stored: no-op, still "present"
            present.append(by_url[ek["url"]])
            continue
        sid = _next_8k_id(ticker_dir, f"{date}_sec_8k", claimed)
        claimed.add(sid)
        substantive = [i for i in items_reported if i != "Item 9.01"] or items_reported
        labels = [EIGHTK_ITEM_LABELS.get(i, i) for i in substantive]
        meta = SourceMeta(
            id=sid, ticker=ticker.upper(), kind="sec_filing", source="SEC EDGAR",
            url=ek["url"], fetched_at=now.isoformat(), as_of=date,
            title=f"{ticker.upper()} 8-K {date}: {', '.join(labels) or '8-K filing'}",
            fetch_tool=FETCH_TOOL, fetch_cmd=fetch_cmd(ticker, "filings"))
            # no supersedes: 8-Ks are events, not refreshes
        body = f"# 8-K (filed {date}, accession {ek.get('accession', 'unknown')})\n\n{content}"
        paths.append(write_source(ticker_dir, meta, body))
        present.append(sid)
    return paths, present


def fetch_filings(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    *,
    filings_provider: Callable[[str], dict] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Fetch SEC filings: one immutable source per 10-K/10-Q/material 8-K + financials.

    Sources are immutable and a filing already on disk is left untouched. A newer
    10-K/10-Q writes a NEW dated file and does NOT supersede the prior one: that is
    temporal succession, and last year's 10-K remains true evidence for last year
    (§5). Only an amendment (10-K/A) supersedes the filing it restates.

    The fetch succeeds when a 10-K, a 10-Q or the financials extract is present
    afterwards; 8-K problems never fail it.
    """
    provider = filings_provider or _edgartools_provider
    now = now or datetime.now(timezone.utc)
    try:
        raw = provider(ticker)
    except Exception as exc:  # provider errors are data, not crashes
        return False, [], f"filings fetch failed: {exc}"

    paths: list[Path] = []
    present: list[str] = []

    for key, form_slug, labels in (("tenk", "sec_10k", ITEM_10K_LABELS),
                                   ("tenq", "sec_10q", ITEM_10Q_LABELS)):
        filing = raw.get(key)
        if not filing or not filing.get("filing_date"):
            continue
        sid = f"{filing['filing_date']}_{form_slug}"
        written = _write_filing_source(
            ticker, ticker_dir, now, sid=sid, form_slug=form_slug, filing=filing,
            item_labels=labels)
        if written:
            paths.append(written)
        if source_exists(ticker_dir, sid):   # written now, or written by an earlier run
            present.append(sid)

    eightk_paths, eightk_ids = _write_8k_sources(
        ticker, ticker_dir, now, raw.get("eightks") or [])
    paths.extend(eightk_paths)
    present.extend(eightk_ids)

    fin = raw.get("financials")
    if fin:
        tenk = raw.get("tenk") or {}
        smeta = StructuredMeta(
            id="sec_financials_edgar", ticker=ticker.upper(), producer="fetch",
            title=f"{ticker.upper()} SEC financial statements extract",
            source="SEC EDGAR",
            url=tenk.get("url") or _browse_url(ticker, "10-K"),
            provider_tool="edgartools.Company.financials",
            fetch_cmd=fetch_cmd(ticker, "filings"), fetched_at=now.isoformat(),
            as_of=(tenk.get("period_end") or tenk.get("filing_date")
                   or now.date().isoformat()))
        paths.append(write_structured(ticker_dir, smeta, fin))
        present.append(smeta.id)

    core = [i for i in present
            if i.endswith(("_sec_10k", "_sec_10q")) or i == "sec_financials_edgar"]
    if not core:
        return False, paths, f"no 10-K, 10-Q or financials could be extracted for {ticker}"

    # Every id, not just the newest: §10.1's missing-artifact check can only see
    # a deleted filing if state names it.
    record_fetch(state, "filings", sorted(set(present)), now,
                 {"policy": "on_new_filing"})
    return True, paths, None
