# Peer comparability rubric (spec §13.3)

You are judging how closely each candidate company compares to the **subject**
company — the row with `"is_subject": true`.

**This is a judgment, not an arithmetic.** Do not score the signals below, do not
weight them, and do not compute a composite. There is deliberately no formula in
this pipeline: an earlier version assigned integers on four axes and combined
them, which added precision without adding accuracy and could not absorb the
noise the mechanical sources produce (a broad Consumer-Discretionary ETF once put
ABNB and EXPE above F and GM as peers for TSLA). Read the evidence, decide, and
say why in one concrete sentence.

## What to weigh

Roughly in order of how much they should move your ranking:

1. **Business-model similarity** — how the company actually makes money.
   Subscription vs. transactional vs. hardware vs. services. Two companies in the
   same industry label with different models are weaker comparables than the
   label suggests.
2. **Product and customer overlap** — do they sell competing products to the same
   buyers? This is the strongest single signal.
3. **Competitive substitutability** — would a customer evaluating the subject
   plausibly evaluate this company in the same purchase decision?
4. **End-market similarity** — the industries and buyer types they ultimately
   sell into.
5. **Scale** — `market_cap` and `revenue_ttm` against the subject's. An order of
   magnitude apart is a weak comparable even with identical products.
6. **Growth profile** — companies growing at very different rates trade on
   different logic and are poor valuation comparables.
7. **Revenue profile** — mix, recurring share, concentration, as far as the
   description reveals it.
8. **Company description** — `description` and `fmp_industry` are the ground
   truth about what a company does. Weigh these **above all the source labels**.

## The mechanical signals, and what they are worth

These found the candidates. They are **not** evidence of comparability, and
ranking by them is the failure mode this rubric exists to prevent.

- `sources: ["fmp_peers"]` — Financial Modeling Prep's own peer list. It can
  return sector or market-cap neighbours that are poor operating comparables
  (§13.1). Useful, but **weak**.
- `sources: [..., "funds"]` with `fund_count` — how many of the (up to five)
  ETFs holding the subject also hold this company. Thematic-fund construction is
  not a comparability judgment. Useful, but **weak**.
- `sources: [..., "user"]` — the user named this company. Note it, but a pinned
  peer takes its slot in `peers-select` regardless of your ranking, so you do not
  need to reserve room for it.
- **Agreement between mechanical sources** is mildly corroborating: a company
  named by FMP *and* held by several of the same funds is likelier to be a real
  comparable than one named by either alone. Mildly.

## The proxy excerpt

`peers_proxy.json`'s `data.excerpt` is prose from the subject's latest DEF 14A.
Read it, but know what it is: a peer group chosen for the **executive talent
market**, on criteria that are usually revenue and market-cap bands plus growth
rate — not business similarity. That is why such groups routinely contain
companies from unrelated industries.

Treat it as evidence of **size and growth comparability**, not of business
comparability.

You may rank a company that appears **only** in the excerpt and in no mechanical
source — say so in its rationale. This is deliberate: the excerpt is shown to you
precisely so a real comparable that no mechanical source surfaced can still be
named. Use the correct US listing for any company you name from prose, prefer
listed US companies to unlisted or foreign ones, and never invent a ticker.

## Output

Exactly five entries, ranked 1–5, best first. Each `rationale` is one concrete
sentence naming the products, buyers or end markets that justify the placement —
not a restatement of the signals ("high fund overlap" is not a rationale).
