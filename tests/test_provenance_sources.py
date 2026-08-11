from datetime import date

from lib import provenance as prov


def test_kind_sets_disjoint():
    assert prov.BRONZE_KINDS.isdisjoint(prov.MODEL_KINDS)
    assert "other" in prov.BRONZE_KINDS and "custom" not in prov.BRONZE_KINDS


def test_make_source_id_basic(tmp_ticker_dir):
    sid = prov.make_source_id("news", date(2026, 8, 11), ticker_dir=tmp_ticker_dir)
    assert sid == "2026-08-11_news"


def test_make_source_id_topic_slugged(tmp_ticker_dir):
    sid = prov.make_source_id("web_page", date(2026, 8, 11),
                              topic="Zscaler'S SASE Win-Rates!", ticker_dir=tmp_ticker_dir)
    assert sid == "2026-08-11_web_page_zscaler-s-sase-win-rates"


def test_make_source_id_counts_archive(tmp_ticker_dir):
    # §5: ids unique across sources/ AND sources/archive/
    (tmp_ticker_dir / "sources" / "archive" / "2026-08-11_news_2026-08-12.md").write_text("x")
    (tmp_ticker_dir / "sources" / "2026-08-11_news_2.md").write_text("x")
    sid = prov.make_source_id("news", date(2026, 8, 11), ticker_dir=tmp_ticker_dir)
    assert sid == "2026-08-11_news_3"
