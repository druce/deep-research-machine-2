# `e2e_tree` — a recorded PANW corpus for the offline end-to-end test

A small, hand-checked knowledge base: two bronze documents, four structured
artifacts, one wiki page with bronze citations, one answered ledger question,
and the generated manifest. It is **recorded, not regenerated** — the point of
the offline e2e (§24) is that a corpus captured at one moment still drives
`manifest → validate → charts → assemble → snapshot → validate` after the code
around it changes.

`tests/test_e2e_offline.py` copies it to a temp directory before touching it;
nothing in the test suite writes here.

To change it: edit the files directly and re-run
`uv run python sra.py validate PANW --data-root tests/fixtures/e2e_tree`, which
must stay clean. Keep it small — this fixture exists to exercise phase ordering
and the assembly path, not to be a realistic research corpus.
