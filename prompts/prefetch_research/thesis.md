# Topic: investment thesis — {company} ({symbol})

Read `prompts/prefetch_research/_shared.md` first; its search budget and its
"numbers are not your job" rule apply here.

**Do not build a model.** Consensus estimates, price targets and the current
multiple are already gathered — `estimates`, `price_targets`, `key_ratios_computed`
in `structured/`. Read them and take them as given. Constructing your own DCF or
your own EPS bridge from a web search is out of scope for this topic and was a
large part of the previous build's cost; the valuation section is written later,
by a writer who has the same artifacts and more context than you.

What you are gathering is the **argument**: what informed people believe about
this company, in both directions, and what would settle it.

## Seed queries

1. `{company} bull case OR why buy`
2. `{company} bear case OR short thesis OR overvalued`
3. `{company} analyst price target raised lowered` — and the reasoning
4. `{company} valuation multiple compared to peers`
5. `{company} guidance outlook next year`
6. `{company} growth runway OR deceleration`

## What to extract

- `## Bull` — three to five arguments. Each: the claim, who makes it, and what
  observable fact supports it. Attach a rough magnitude only where a source
  gives you one, tagged `[CONSENSUS]` or `[ESTIMATE]` with its origin.
- `## Bear` — the same, three to five. Steelman these; a bear section made of
  strawmen is how a report ends up bullish by construction. If the bear case is
  genuinely thin, say so and say why.
- `## Where the debate actually is` — the two or three questions on which bulls
  and bears disagree about *facts* rather than preferences. This is the most
  valuable part of the file for later phases, so make it specific.
- `## Watchpoints` — five to seven observable indicators with a threshold and a
  direction: "net retention below X in any quarter would break the bull case".
  Metrics that can actually be checked in a filing or a release.

Consensus and targets: cite the `structured/` artifacts, do not re-source them
from the web.
