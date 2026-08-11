"""`sra.py fetch-urls` — harvesting researcher URLs into bronze (spec §8.3, §24).

The point of this command is that a claim can cite its ORIGIN rather than the
aggregator that repeated it: an answer or a news roundup carries `cited_urls`,
and this pulls those pages into `sources/` so a citation terminates at evidence.

The properties that matter, and that §24's `fetch-urls` block names, are all
about not doing the same work twice and not losing track of what failed: two
answers citing one URL produce ONE source, a URL older than
`WEB_PAGE_POLICY_DAYS` is refetched with `supersedes` rather than duplicated, a
failed fetch is a `null` map entry and a warning (never a command failure), and
a rerun changes nothing.

No network: the fetcher is injected everywhere.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sra
from lib.fetchers.urls import WEB_PAGE_POLICY_DAYS, find_source_by_url, harvest_answer
from lib.provenance import SourceMeta, read_source, resolve_source, write_answer, write_source
from lib.validate import validate

DAY1 = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
LATER = DAY1 + timedelta(days=WEB_PAGE_POLICY_DAYS + 1)
SOONER = DAY1 + timedelta(days=WEB_PAGE_POLICY_DAYS - 1)

URL_A = "https://reuters.com/panw-beats"
URL_B = "https://bloomberg.com/panw-guides"


def fake_fetcher(pages: dict[str, str], *, fail: set[str] | None = None):
    """A `fetch_url_to_markdown` stand-in serving `pages`, failing for `fail`."""
    fail = fail or set()
    calls: list[str] = []

    def _fetch(url: str, **kwargs):
        calls.append(url)
        if url in fail:
            return False, None, f"transport_error: {url} (ConnectTimeout)"
        if url not in pages:
            return False, None, f"http_404: {url}"
        return True, {"markdown": pages[url], "final_url": url,
                      "content_type": "text/html", "truncated": False,
                      "title": f"Title for {url}"}, None

    _fetch.calls = calls  # type: ignore[attr-defined]
    return _fetch


def make_answer(ticker_dir: Path, answer_id: str, urls: list[str]) -> Path:
    """A researcher answer under derived/answers/ citing `urls` (§8.1)."""
    return write_answer(ticker_dir, SourceMeta(
        id=answer_id, ticker="PANW", kind="research_answer", source="sra-researcher",
        url="", fetched_at=DAY1.isoformat(), as_of=DAY1.date().isoformat(),
        title="What drove the beat?", fetch_tool="agents/sra-researcher.md",
        fetch_cmd="", cited_urls=urls), "Revenue grew on strong billings.")


def make_aggregator(ticker_dir: Path, urls: list[str], *,
                    source_id: str = "2026-07-30_news_yahoo") -> Path:
    """A news roundup under sources/ carrying `cited_urls` (§5, §11.2)."""
    return write_source(ticker_dir, SourceMeta(
        id=source_id, ticker="PANW", kind="news", source="Yahoo Finance",
        url="https://finance.yahoo.com/quote/PANW/news", fetched_at=DAY1.isoformat(),
        as_of=DAY1.date().isoformat(), title="PANW news",
        fetch_tool="lib/fetchers/news.py",
        fetch_cmd="uv run python sra.py prefetch PANW --kinds news",
        cited_urls=urls), "Headlines.")


def read_map(ticker_dir: Path, artifact_id: str) -> dict:
    return json.loads(
        (ticker_dir / "derived" / "answers" / f"{artifact_id}.urls.json")
        .read_text(encoding="utf-8"))


def web_pages(ticker_dir: Path) -> list[Path]:
    return sorted((ticker_dir / "sources").glob("*_web_page_*.md"))


def _errors(ticker_dir: Path):
    return [f for f in validate(ticker_dir, ticker_dir.parent) if f.severity == "error"]


# --- find_source_by_url ----------------------------------------------------

def test_find_source_by_url_matches_frontmatter_url(tmp_ticker_dir: Path):
    make_aggregator(tmp_ticker_dir, [])
    found = find_source_by_url(tmp_ticker_dir,
                               "https://finance.yahoo.com/quote/PANW/news")
    assert found is not None and found.id == "2026-07-30_news_yahoo"


def test_find_source_by_url_returns_none_when_absent(tmp_ticker_dir: Path):
    make_aggregator(tmp_ticker_dir, [])
    assert find_source_by_url(tmp_ticker_dir, "https://example.com/nope") is None


def test_find_source_by_url_ignores_archived_copies(tmp_ticker_dir: Path):
    """A superseded copy is not the live evidence: matching it would hand out an
    id that `manifest` and `grep` deliberately exclude."""
    make_aggregator(tmp_ticker_dir, [])
    write_source(tmp_ticker_dir, SourceMeta(
        id="2026-08-05_news_yahoo", ticker="PANW", kind="news", source="Yahoo Finance",
        url="https://finance.yahoo.com/quote/PANW/news",
        fetched_at=LATER.isoformat(), as_of=LATER.date().isoformat(),
        title="PANW news", fetch_tool="lib/fetchers/news.py",
        fetch_cmd="uv run python sra.py prefetch PANW --kinds news",
        supersedes="2026-07-30_news_yahoo"), "Newer headlines.",
        today=LATER.date())
    found = find_source_by_url(tmp_ticker_dir,
                               "https://finance.yahoo.com/quote/PANW/news")
    assert found is not None and found.id == "2026-08-05_news_yahoo"


# --- harvest_answer: the happy path ---------------------------------------

def test_harvest_writes_a_web_page_source_with_full_provenance(tmp_ticker_dir: Path):
    path = make_answer(tmp_ticker_dir, "2026-07-30_answer_beat", [URL_A])
    result = harvest_answer(tmp_ticker_dir, path,
                            fetcher=fake_fetcher({URL_A: "Reuters says beat."}),
                            now=DAY1)
    assert result["errors"] == {}
    [source_path] = web_pages(tmp_ticker_dir)
    meta, body = read_source(source_path)
    assert meta.kind == "web_page"
    assert meta.url == URL_A
    assert meta.source == "reuters.com"          # §8.3: site name
    assert meta.title == f"Title for {URL_A}"
    assert meta.ticker == "PANW"
    assert meta.fetch_tool == "lib/fetchers/urls.py"
    assert meta.fetch_cmd == (
        "uv run python sra.py fetch-urls PANW --from 2026-07-30_answer_beat")
    assert meta.as_of == DAY1.date().isoformat()
    assert body.strip() == "Reuters says beat."


def test_harvest_writes_the_url_to_id_map(tmp_ticker_dir: Path):
    path = make_answer(tmp_ticker_dir, "a1", [URL_A])
    harvest_answer(tmp_ticker_dir, path,
                   fetcher=fake_fetcher({URL_A: "text"}), now=DAY1)
    [source_path] = web_pages(tmp_ticker_dir)
    assert read_map(tmp_ticker_dir, "a1") == {URL_A: source_path.stem}


def test_source_id_is_slugged_from_the_url(tmp_ticker_dir: Path):
    path = make_answer(tmp_ticker_dir, "a1", [URL_A])
    harvest_answer(tmp_ticker_dir, path,
                   fetcher=fake_fetcher({URL_A: "text"}), now=DAY1)
    [source_path] = web_pages(tmp_ticker_dir)
    assert source_path.stem.startswith("2026-07-30_web_page_")
    assert "reuters-com" in source_path.stem


def test_truncation_is_recorded_on_the_source(tmp_ticker_dir: Path):
    """§8.3.1: a partial document must say so, or a reader quotes a fragment as
    if it were the whole page."""
    def _fetch(url, **kwargs):
        return True, {"markdown": "cut off here", "final_url": url,
                      "content_type": "text/html", "truncated": True,
                      "title": "Long"}, None
    path = make_answer(tmp_ticker_dir, "a1", [URL_A])
    harvest_answer(tmp_ticker_dir, path, fetcher=_fetch, now=DAY1)
    [source_path] = web_pages(tmp_ticker_dir)
    meta, _ = read_source(source_path)
    assert meta.truncated is True


def test_final_url_after_redirect_is_recorded(tmp_ticker_dir: Path):
    """The document we actually got is the one being cited, so the redirected-to
    URL is what the source records — the map still keys on what was cited."""
    def _fetch(url, **kwargs):
        return True, {"markdown": "moved", "final_url": "https://reuters.com/final",
                      "content_type": "text/html", "truncated": False,
                      "title": "Final"}, None
    path = make_answer(tmp_ticker_dir, "a1", [URL_A])
    harvest_answer(tmp_ticker_dir, path, fetcher=_fetch, now=DAY1)
    meta, _ = read_source(web_pages(tmp_ticker_dir)[0])
    assert meta.url == "https://reuters.com/final"
    assert list(read_map(tmp_ticker_dir, "a1")) == [URL_A]


# --- dedupe ----------------------------------------------------------------

def test_two_answers_citing_one_url_produce_one_source(tmp_ticker_dir: Path):
    """§24 `fetch-urls`: fresh URL dedupe. The second answer reuses the id."""
    a1 = make_answer(tmp_ticker_dir, "a1", [URL_A])
    a2 = make_answer(tmp_ticker_dir, "a2", [URL_A])
    fetcher = fake_fetcher({URL_A: "text"})
    r1 = harvest_answer(tmp_ticker_dir, a1, fetcher=fetcher, now=DAY1)
    r2 = harvest_answer(tmp_ticker_dir, a2, fetcher=fetcher, now=SOONER)

    assert len(web_pages(tmp_ticker_dir)) == 1
    assert fetcher.calls == [URL_A]            # fetched once, not twice
    assert r1["fetched"] == [URL_A] and r2["skipped"] == [URL_A]
    assert read_map(tmp_ticker_dir, "a1") == read_map(tmp_ticker_dir, "a2")


def test_a_url_repeated_within_one_answer_is_fetched_once(tmp_ticker_dir: Path):
    path = make_answer(tmp_ticker_dir, "a1", [URL_A, URL_A])
    fetcher = fake_fetcher({URL_A: "text"})
    harvest_answer(tmp_ticker_dir, path, fetcher=fetcher, now=DAY1)
    assert fetcher.calls == [URL_A]
    assert len(web_pages(tmp_ticker_dir)) == 1


def test_an_existing_bronze_source_of_another_kind_is_reused(tmp_ticker_dir: Path):
    """"Already in bronze" is not "already a web_page": if the aggregator's own
    URL is cited, the evidence is on disk already and refetching it would be a
    second copy of the same document."""
    make_aggregator(tmp_ticker_dir, [])
    yahoo = "https://finance.yahoo.com/quote/PANW/news"
    path = make_answer(tmp_ticker_dir, "a1", [yahoo])
    fetcher = fake_fetcher({yahoo: "text"})
    result = harvest_answer(tmp_ticker_dir, path, fetcher=fetcher, now=SOONER)
    assert fetcher.calls == []
    assert result["skipped"] == [yahoo]
    assert read_map(tmp_ticker_dir, "a1") == {yahoo: "2026-07-30_news_yahoo"}


# --- freshness / supersede -------------------------------------------------

def test_a_stale_web_page_is_refetched_and_superseded(tmp_ticker_dir: Path):
    """§24: 30-day refetch and supersede. The old copy moves to archive/ and the
    new one points back at it, so the earlier citation still resolves (§5)."""
    a1 = make_answer(tmp_ticker_dir, "a1", [URL_A])
    fetcher = fake_fetcher({URL_A: "text"})
    harvest_answer(tmp_ticker_dir, a1, fetcher=fetcher, now=DAY1)
    old_id = web_pages(tmp_ticker_dir)[0].stem

    a2 = make_answer(tmp_ticker_dir, "a2", [URL_A])
    result = harvest_answer(tmp_ticker_dir, a2, fetcher=fetcher, now=LATER)

    assert result["fetched"] == [URL_A]
    [current] = web_pages(tmp_ticker_dir)
    assert current.stem != old_id
    meta, _ = read_source(current)
    assert meta.supersedes == old_id
    assert meta.as_of == LATER.date().isoformat()
    # the superseded copy is archived, and still resolvable for old citations
    assert resolve_source(tmp_ticker_dir, old_id).parent.name == "archive"
    assert read_map(tmp_ticker_dir, "a2") == {URL_A: current.stem}
    assert read_map(tmp_ticker_dir, "a1") == {URL_A: old_id}


def test_a_fresh_web_page_is_not_refetched_at_the_boundary(tmp_ticker_dir: Path):
    a1 = make_answer(tmp_ticker_dir, "a1", [URL_A])
    fetcher = fake_fetcher({URL_A: "text"})
    harvest_answer(tmp_ticker_dir, a1, fetcher=fetcher, now=DAY1)
    a2 = make_answer(tmp_ticker_dir, "a2", [URL_A])
    harvest_answer(tmp_ticker_dir, a2, fetcher=fetcher,
                   now=DAY1 + timedelta(days=WEB_PAGE_POLICY_DAYS))
    assert fetcher.calls == [URL_A]
    assert len(web_pages(tmp_ticker_dir)) == 1


def test_a_stale_source_of_another_kind_is_not_superseded(tmp_ticker_dir: Path):
    """Superseding a `news` source with a `web_page` would break the news
    fetcher's own chain — `find_prior_source` would stop seeing it. A fresh
    web_page is written instead, leaving the other kind's chain intact."""
    make_aggregator(tmp_ticker_dir, [])
    yahoo = "https://finance.yahoo.com/quote/PANW/news"
    path = make_answer(tmp_ticker_dir, "a1", [yahoo])
    harvest_answer(tmp_ticker_dir, path, fetcher=fake_fetcher({yahoo: "text"}),
                   now=LATER)
    assert (tmp_ticker_dir / "sources" / "2026-07-30_news_yahoo.md").exists()
    [page] = web_pages(tmp_ticker_dir)
    meta, _ = read_source(page)
    assert meta.supersedes is None


# --- failure is a warning, never a failure --------------------------------

def test_a_failed_fetch_is_a_null_map_entry_and_a_warning(tmp_ticker_dir: Path):
    """§8.3: a failed target fetch is a warning. The `null` is what tells the
    synthesizer the claim is not citable and must be dropped or re-sourced."""
    path = make_answer(tmp_ticker_dir, "a1", [URL_A, URL_B])
    result = harvest_answer(
        tmp_ticker_dir, path,
        fetcher=fake_fetcher({URL_A: "text", URL_B: "text"}, fail={URL_B}), now=DAY1)

    assert result["fetched"] == [URL_A]
    assert URL_B in result["errors"]
    mapping = read_map(tmp_ticker_dir, "a1")
    assert mapping[URL_B] is None
    assert mapping[URL_A] is not None
    assert len(web_pages(tmp_ticker_dir)) == 1


def test_an_ssrf_rejection_is_recorded_as_a_null_entry(tmp_ticker_dir: Path):
    """A model citing `http://169.254.169.254/` is refused by §8.3.1 and lands
    here as a reason code, not an exception."""
    bad = "http://169.254.169.254/latest/meta-data"
    path = make_answer(tmp_ticker_dir, "a1", [bad])
    result = harvest_answer(tmp_ticker_dir, path, now=DAY1)  # real fetcher, no network
    assert read_map(tmp_ticker_dir, "a1") == {bad: None}
    assert "cloud_metadata" in result["errors"][bad]
    assert web_pages(tmp_ticker_dir) == []


# --- idempotence -----------------------------------------------------------

def test_rerun_is_idempotent(tmp_ticker_dir: Path):
    """§24 `fetch-urls`: idempotence. A second harvest of the same answer must
    add no source and change no map — sources are immutable, so a re-fetch here
    would mean a permanent duplicate on every run."""
    path = make_answer(tmp_ticker_dir, "a1", [URL_A, URL_B])
    fetcher = fake_fetcher({URL_A: "a", URL_B: "b"})
    harvest_answer(tmp_ticker_dir, path, fetcher=fetcher, now=DAY1)
    before_map = read_map(tmp_ticker_dir, "a1")
    before_sources = [p.stem for p in web_pages(tmp_ticker_dir)]

    harvest_answer(tmp_ticker_dir, path, fetcher=fetcher, now=SOONER)

    assert read_map(tmp_ticker_dir, "a1") == before_map
    assert [p.stem for p in web_pages(tmp_ticker_dir)] == before_sources
    assert fetcher.calls == [URL_A, URL_B]  # no second round of fetches


# --- --max -----------------------------------------------------------------

def test_max_caps_the_number_of_fetches(tmp_ticker_dir: Path):
    path = make_answer(tmp_ticker_dir, "a1", [URL_A, URL_B])
    fetcher = fake_fetcher({URL_A: "a", URL_B: "b"})
    result = harvest_answer(tmp_ticker_dir, path, max_n=1, fetcher=fetcher, now=DAY1)
    assert fetcher.calls == [URL_A]
    assert result["fetched"] == [URL_A]
    assert len(web_pages(tmp_ticker_dir)) == 1


def test_urls_beyond_max_stay_unharvested_rather_than_null(tmp_ticker_dir: Path):
    """A URL we never tried must not be recorded as a failure: `null` means "not
    citable", and a later run would then never pick it up."""
    path = make_answer(tmp_ticker_dir, "a1", [URL_A, URL_B])
    fetcher = fake_fetcher({URL_A: "a", URL_B: "b"})
    harvest_answer(tmp_ticker_dir, path, max_n=1, fetcher=fetcher, now=DAY1)
    assert list(read_map(tmp_ticker_dir, "a1")) == [URL_A]

    harvest_answer(tmp_ticker_dir, path, max_n=1, fetcher=fetcher, now=DAY1)
    assert read_map(tmp_ticker_dir, "a1")[URL_B] is not None


# --- validate stays clean --------------------------------------------------

def test_harvested_sources_pass_validate(tmp_ticker_dir: Path):
    path = make_answer(tmp_ticker_dir, "a1", [URL_A])
    harvest_answer(tmp_ticker_dir, path, fetcher=fake_fetcher({URL_A: "text"}),
                   now=DAY1)
    assert _errors(tmp_ticker_dir) == []


def test_the_url_map_is_not_a_structured_artifact_and_passes_validate(
        tmp_ticker_dir: Path):
    """§8.3 fixes the map's format as a bare `{url: id|null}` object — it carries
    no `_meta`, so `validate` must not read it as a structured artifact."""
    path = make_answer(tmp_ticker_dir, "a1", [URL_A])
    harvest_answer(tmp_ticker_dir, path, fetcher=fake_fetcher({URL_A: "text"}),
                   now=DAY1)
    assert "_meta" not in read_map(tmp_ticker_dir, "a1")
    assert _errors(tmp_ticker_dir) == []


# --- the CLI ---------------------------------------------------------------

@pytest.fixture
def cli(tmp_path: Path, monkeypatch):
    """An initialized PANW tree with the URL fetcher stubbed out."""
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    fetcher = fake_fetcher({URL_A: "reuters text", URL_B: "bloomberg text"})
    monkeypatch.setattr("lib.fetchers.urls.fetch_url_to_markdown", fetcher)
    monkeypatch.setattr(sra, "_utcnow", lambda: DAY1)
    return {"root": tmp_path, "dir": tmp_path / "PANW", "fetcher": fetcher}


def run(cli, *args) -> int:
    return sra.main(["fetch-urls", "PANW", "--data-root", str(cli["root"]), *args])


def test_cli_harvests_every_answer_by_default(cli, capsys):
    make_answer(cli["dir"], "a1", [URL_A])
    make_answer(cli["dir"], "a2", [URL_B])
    capsys.readouterr()
    assert run(cli) == 0
    out = json.loads(capsys.readouterr().out)
    assert sorted(out["fetched"]) == sorted([URL_A, URL_B])
    assert len(web_pages(cli["dir"])) == 2


def test_cli_harvests_aggregator_sources_too(cli, capsys):
    """§5/§11.2 aggregators carry `cited_urls` for exactly this reason — the
    roundup is not the origin of the claim."""
    make_aggregator(cli["dir"], [URL_A])
    capsys.readouterr()
    assert run(cli) == 0
    assert json.loads(capsys.readouterr().out)["fetched"] == [URL_A]
    assert read_map(cli["dir"], "2026-07-30_news_yahoo")[URL_A] is not None


def test_cli_from_restricts_to_one_answer(cli, capsys):
    make_answer(cli["dir"], "a1", [URL_A])
    make_answer(cli["dir"], "a2", [URL_B])
    capsys.readouterr()
    assert run(cli, "--from", "a1") == 0
    assert json.loads(capsys.readouterr().out)["fetched"] == [URL_A]
    assert cli["fetcher"].calls == [URL_A]
    assert not (cli["dir"] / "derived" / "answers" / "a2.urls.json").exists()


def test_cli_from_an_unknown_answer_is_an_error(cli):
    """§8.3: exit 0 unless the answer file itself cannot be read."""
    assert run(cli, "--from", "nosuch") == 1


def test_cli_from_rejects_a_traversing_id(cli):
    """§8.4 check 7: an id is a bare filename component, never a path."""
    assert run(cli, "--from", "../../etc/passwd") == 1


def test_cli_exits_zero_when_a_target_fetch_fails(cli, capsys):
    make_answer(cli["dir"], "a1", ["https://dead.example.com/x"])
    capsys.readouterr()
    assert run(cli) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["fetched"] == []
    assert "https://dead.example.com/x" in out["errors"]


def test_cli_skips_answers_already_harvested(cli, capsys):
    make_answer(cli["dir"], "a1", [URL_A])
    run(cli)
    capsys.readouterr()
    assert run(cli) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["fetched"] == []
    assert cli["fetcher"].calls == [URL_A]
    assert len(web_pages(cli["dir"])) == 1


def test_cli_does_not_harvest_the_web_pages_it_writes(cli):
    """Harvested pages carry no `cited_urls`, so the command cannot turn into a
    crawler that follows links out of the pages it just fetched."""
    make_answer(cli["dir"], "a1", [URL_A])
    run(cli)
    meta, _ = read_source(web_pages(cli["dir"])[0])
    assert meta.cited_urls == []


def test_cli_max_caps_fetches_per_answer(cli, capsys):
    make_answer(cli["dir"], "a1", [URL_A, URL_B])
    capsys.readouterr()
    assert run(cli, "--max", "1") == 0
    assert cli["fetcher"].calls == [URL_A]


def test_cli_is_a_noop_on_an_uninitialized_ticker(tmp_path: Path):
    assert sra.main(["fetch-urls", "PANW", "--data-root", str(tmp_path)]) == 1


def test_cli_rejects_an_invalid_ticker(tmp_path: Path):
    assert sra.main(["fetch-urls", "../evil", "--data-root", str(tmp_path)]) == 1
