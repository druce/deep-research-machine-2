"""Citation markers become bidirectional anchor links."""

from __future__ import annotations

from lib.render.postprocess import link_citations, postprocess

REFS = ("## References\n\n"
        "[1] First source — example.com\n"
        "[2] Second source — example.org\n")


def test_single_citation_gets_one_backlink() -> None:
    out = link_citations(f"Claim.[^1]\n\n{REFS}")

    assert '<sup class="cite"><a id="cite-1-1" href="#ref-1">1</a></sup>' in out
    assert '<span class="ref-n" id="ref-1">[1]</span>' in out
    assert out.count('href="#cite-1-1"') == 1
    assert "↩¹" not in out


def test_repeated_citation_gets_numbered_backlinks() -> None:
    out = link_citations(f"A.[^2] B.[^2] C.[^2]\n\n{REFS}")

    for k in (1, 2, 3):
        assert f'id="cite-2-{k}"' in out
        assert f'href="#cite-2-{k}"' in out
    assert "↩¹" in out and "↩²" in out and "↩³" in out


def test_no_marker_survives_for_a_resolvable_citation() -> None:
    out = link_citations(f"A.[^1] B.[^2]\n\n{REFS}")

    assert "[^" not in out.split("## References")[0]


def test_dangling_marker_is_left_literal() -> None:
    assert "[^99]" in link_citations(f"A.[^99]\n\n{REFS}")


def test_reference_text_is_preserved() -> None:
    assert "First source — example.com" in link_citations(f"A.[^1]\n\n{REFS}")


def test_uncited_reference_entry_gets_no_backlink() -> None:
    out = link_citations(f"A.[^1]\n\n{REFS}")

    assert '<span class="ref-n" id="ref-2">[2]</span> Second source' in out
    assert 'href="#cite-2-' not in out


def test_document_without_references_is_unchanged() -> None:
    text = "A claim.[^1]\n"
    assert link_citations(text) == text


def test_postprocess_chain_applies_it() -> None:
    assert 'href="#ref-1"' in postprocess(f"Claim.[^1]\n\n{REFS}")


def test_anchored_citations_are_still_resolvable_by_the_gate() -> None:
    """`link_citations` must not hide citations from `_check_assembled_reports`.

    That check reads `[^N]` markers. Once every marker becomes an anchor, a
    validator that only knew the marker form would find nothing to verify and
    would pass any report at all.
    """
    from lib.validate import _report_citations

    assert _report_citations(link_citations(f"A.[^1] B.[^2]\n\n{REFS}")) == ["1", "2"]
