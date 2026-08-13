export const meta = {
  name: 'sra-write-wave',
  description: 'write -> critic -> rewrite for each report section (spec §15.1, §15.2)',
  phases: [
    { title: 'Risks' },
    { title: 'Valuation' },
    { title: 'Financial Strength' },
    { title: 'Competitive Landscape' },
    { title: 'Supply Chain Positioning' },
    { title: 'Business Model' },
    { title: 'Company Profile' },
  ],
}

// The cold build's write phase: seven sections x three agents (§15.1).
//
// Seven independent chains, launched together — not three stages over seven
// sections. Each section runs its own write -> critic -> rewrite to completion
// and shares no state with the others: they read different wiki pages and
// write different files, so nothing one section does can hold up another.
// Progress groups are per SECTION rather than per stage, because "valuation is
// in rewrite while risk_news is still drafting" is the true picture and a
// stage-shaped display hides it.
//
// The harness caps concurrent agent() calls at min(16, max(2, cores - 2)) — 6
// on an 8-core machine — and there is no setting for it; it is read from the
// core count when the module loads. With seven sections one always waits. That
// costs about one stage of slack rather than a doubled makespan, because a
// slot is held per agent() call rather than per chain, so a chain releases its
// slot between stages and the FIFO queue self-balances. Sections are ordered
// LONGEST FIRST so the one that queues is the cheapest (profile, 1000 words)
// instead of the dearest (risk_news, 2700).
//
// Splitting the wave across two Workflow invocations would give 12 slots (the
// semaphore is per run) and is deliberately not done: it buys one section out
// of seven and costs a split summary object.
//
// The script itself does no file I/O: workflow scripts have no filesystem, and
// they do not need one here. Every agent reads its own prompt parts off disk
// (`prompts/write/_shared.md`, `prompts/write/<section>.md`, `STYLE.md`) and
// the section config out of `sections.yaml`, which keeps ONE copy of each.
//
// args: {ticker, company, workdir, report_date, sections, char_caps}
//   workdir     absolute path to data/<TICKER> — every path below is absolute,
//               because a subagent's working directory is not guaranteed
//   sections    [{id, title, wiki_page, word_target, hard_checks}]
//   char_caps   {<section id>: <max_length in characters>} — optional

// Some harness builds deliver `args` as a JSON string rather than a parsed
// object; destructuring that directly yields undefined and fails opaquely at
// the first `sections.length`. Accept either shape.
const input = typeof args === 'string' ? JSON.parse(args) : args

const { ticker, company, workdir, report_date, sections, char_caps } = input

function draftPath(section) {
  return `${workdir}/reports/${report_date}/sections/${section.id}.md`
}

function critiquePath(section) {
  return `${workdir}/reports/${report_date}/sections/${section.id}.critique.md`
}

// One log per agent, under the run's log/ directory (§23.4). Agents cannot see
// their own token usage, so these carry what only the agent knows — what it
// read, what it ran, what it decided — and `sra.py run-log` joins the token
// counts in from run_stats.json afterwards.
function logPath(section, stage, sequence) {
  const n = String(sequence).padStart(2, '0')
  return `${workdir}/reports/${report_date}/log/${n}_${stage}_${section.id}.md`
}

function hardChecks(section) {
  const rules = (section.hard_checks || []).slice()
  const cap = char_caps ? char_caps[section.id] : null
  // The character cap is a per-run budget (length preset x word target), not a
  // property of the section, so it is appended here rather than living in
  // sections.yaml next to the structural checks.
  //
  // `max_length_prose`, not `max_length`: the cap must not count draft citation
  // ids, which assembly renumbers from 21 characters to five. It used to, and
  // the effect was measurable — every SPCX section landed between 98.7% and
  // 100.0% of its cap (business_model three characters under), with up to 27%
  // of the budget spent on ids the reader never sees. Writers optimise against
  // the binding constraint, so the constraint has to measure the right thing.
  if (cap) rules.push(`max_length_prose: ${cap}`)
  return JSON.stringify(rules)
}

// Every prompt opens the same way: who you are, what to read, and the fact that
// the pieces live on disk rather than in this script.
function preamble(section, role, stage, sequence) {
  return [
    `You are the ${role} for section "${section.id}" (${section.title}) of an`,
    `equity research report on ${company} (${ticker}).`,
    '',
    'Read these, in this order, from the repo root:',
    '  1. prompts/write/_shared.md — the reading, citing, saving and checking contract',
    `  2. prompts/write/${section.id}.md — this section's writer, critic and rewrite blocks`,
    '  3. STYLE.md — how the prose must read',
    '',
    'And load this section\'s configuration:',
    '  uv run python -c "import json;from lib.sections import load_sections;' +
      `c=load_sections();s=c['sections']['${section.id}'];` +
      `print(json.dumps({'write_guidance':s['write_guidance'],` +
      `'section_ownership':c['section_ownership'],` +
      `'tension_analysis':c['tension_analysis']}))"`,
    '',
    'Placeholder values for those prompts:',
    `  {ticker} = ${ticker}`,
    `  {company} = ${company}`,
    `  {section} = ${section.id}`,
    `  {wiki_page} = ${section.wiki_page}`,
    `  {word_target} = ${section.word_target}`,
    `  {workdir} = ${workdir}`,
    `  {report_date} = ${report_date}`,
    `  {draft_path} = ${draftPath(section)}`,
    `  {critique_path} = ${critiquePath(section)}`,
    `  {log_path} = ${logPath(section, stage, sequence)}`,
    `  {hard_checks_json} = ${hardChecks(section)}`,
    '',
    taskLogContract(section, stage, sequence),
  ].join('\n')
}

// Spelled out in every prompt rather than left to the agent definition: an
// agent that skips its log leaves a hole in the run log nobody can fill later,
// because the information was never anywhere but in that agent's context.
function taskLogContract(section, stage, sequence) {
  return [
    'BEFORE you start, record the time:',
    '  date -u +%Y-%m-%dT%H:%M:%SZ',
    '',
    'AFTER you finish, take the time again and write your task log to',
    `${logPath(section, stage, sequence)} — exactly this shape, and nothing else`,
    'in that file:',
    '',
    '  ---',
    `  purpose: ${stage}`,
    `  section: ${section.id}`,
    '  round: 1',
    `  label: "${stage}:${section.id}"`,
    '  started_at: <the first timestamp>',
    '  finished_at: <the second timestamp>',
    '  status: ok | degraded | failed',
    '  outputs: [<paths you wrote, relative to the run directory>]',
    '  ---',
    '',
    '  ## Inputs',
    '  - the wiki page, artifacts and prompts you actually read, as a list',
    '',
    '  ## Commands',
    '  - each sra.py or lib command you ran, with its outcome in a few words',
    '',
    '  ## Outputs',
    '  - what you wrote, with word counts',
    '',
    '  ## Notes',
    '  What you decided and why, anything you could not verify, and any gap you',
    '  recorded as a question. Be specific and brief — this is the only account',
    '  of your work that survives.',
    '',
    'Write the log even when the work failed. A failed stage with no log is',
    'indistinguishable from a stage that never ran.',
  ].join('\n')
}

function writePrompt(section) {
  return [
    preamble(section, 'WRITER', 'section-write', 1),
    '',
    `Follow the "## Writer" block of prompts/write/${section.id}.md.`,
    `Write the section to ${draftPath(section)}.`,
    '',
    'Then run its hard checks over your own draft and fix anything that fails:',
    `  uv run python -m lib.hard_checks ${draftPath(section)} \\`,
    `      --rules-json '${hardChecks(section)}'`,
    '',
    'Return, as your final message, a JSON object and nothing else:',
    '  {"section": "<id>", "path": "<draft path>", "words": <n>,',
    '   "hard_checks_passed": true|false, "failures": [...], "gaps": [...]}',
    'where "gaps" lists any question you recorded with sra.py add-questions.',
  ].join('\n')
}

function criticPrompt(section) {
  return [
    preamble(section, 'CRITIC', 'section-critic', 2),
    '',
    'Follow the "## CRITIQUE PROCEDURE" block of prompts/write/_shared.md and',
    `the "## Critic" block of prompts/write/${section.id}.md.`,
    '',
    'Verify every factual claim by resolving its citation with',
    `  uv run python sra.py show ${ticker} <id>`,
    'from the repo root. A citation that resolves to a wiki page, an answer file',
    'or nothing at all is the most important thing you can find.',
    '',
    `Save the critique to ${critiquePath(section)}.`,
    '',
    'Return, as your final message, a JSON object and nothing else:',
    '  {"section": "<id>", "critique_path": "<path>", "items": <n>,',
    '   "contradicted": <n>, "unsupported": <n>, "blocking": [...]}',
    'where "blocking" names the findings a rewrite must not leave unaddressed.',
  ].join('\n')
}

function rewritePrompt(section) {
  return [
    preamble(section, 'REWRITE AGENT', 'section-rewrite', 3),
    '',
    'Follow the "## REWRITE PROCEDURE" block of prompts/write/_shared.md and',
    `the "## Rewrite" block of prompts/write/${section.id}.md.`,
    '',
    `Read the draft at ${draftPath(section)} and the critique at`,
    `${critiquePath(section)}. Apply the critique and overwrite the draft.`,
    '',
    'Then re-run the hard checks:',
    `  uv run python -m lib.hard_checks ${draftPath(section)} \\`,
    `      --rules-json '${hardChecks(section)}'`,
    '',
    'Return, as your final message, a JSON object and nothing else:',
    '  {"section": "<id>", "path": "<draft path>", "words": <n>,',
    '   "hard_checks_passed": true|false, "failures": [...],',
    '   "declined": [...]}  // critique items you did not apply, with reasons',
  ].join('\n')
}

const STAGE_SCHEMA = {
  type: 'object',
  properties: {
    section: { type: 'string' },
    path: { type: 'string' },
    words: { type: 'number' },
    hard_checks_passed: { type: 'boolean' },
    failures: { type: 'array', items: { type: 'string' } },
    gaps: { type: 'array', items: { type: 'string' } },
    declined: { type: 'array', items: { type: 'string' } },
  },
  required: ['section', 'path', 'hard_checks_passed'],
  additionalProperties: true,
}

// The critic had no schema at all, so its findings came back as an unparsed
// string and nothing downstream could count them. The flow is unchanged — the
// rewrite always runs — but what the critic found is now recorded.
const CRITIQUE_SCHEMA = {
  type: 'object',
  properties: {
    section: { type: 'string' },
    critique_path: { type: 'string' },
    items: { type: 'number' },
    contradicted: { type: 'number' },
    unsupported: { type: 'number' },
    blocking: { type: 'array', items: { type: 'string' } },
  },
  required: ['section', 'critique_path'],
  additionalProperties: true,
}

// Effort per STAGE, not per agent type (spec §21.1). All three stages are the
// same `sra-writer`, dispatched here through `agent()`, which takes an explicit
// `opts.effort` — so the agent definition's `effort: high` is a floor these
// override rather than a setting they inherit. Every stage names its level, so
// changing the session default never silently re-prices the write wave.
//
// This is the pipeline's single largest block: 7 x 3 agents, 2.56M input tokens
// on the PANW build, 32% of the run (§23.3).
//
//   write    high    generative prose from the wiki page. The product.
//   critic   high    resolves every citation with `sra.py show` and hunts the
//                    claim the draft cannot support. This stage is the reason
//                    the wave costs three agents instead of one; running it
//                    cheap would buy back tokens by removing the check they
//                    were spent on.
//   rewrite  medium  applies a worklist that already names what to fix and
//                    why. The judgment happened in the critic; this stage is
//                    execution against it, plus the hard checks. Kept on the
//                    same model rather than dropped to Sonnet because it is
//                    still authoring the prose that ships, against STYLE.md.
const STAGE_TUNING = {
  write: { effort: 'high' },
  critic: { effort: 'high' },
  rewrite: { effort: 'medium' },
}

// `{...undefined}` is legal JavaScript and spreads to nothing, so a key that
// does not match its `agent()` call site loses its effort setting with no error
// and no log line — the stage just quietly costs what the session default
// costs. Name the three stages once and check them.
const untuned = ['write', 'critic', 'rewrite'].filter((s) => !STAGE_TUNING[s])
if (untuned.length) log(`STAGE_TUNING missing: ${untuned.join(', ')} — those stages run at the session default`)

// Longest first: under the concurrency cap the last section to be dispatched is
// the one that waits, and it should be the cheapest one.
const ordered = sections.slice().sort(
  (a, b) => (b.word_target || 0) - (a.word_target || 0))

log(`write wave: ${ordered.length} sections into reports/${report_date}/sections/`
    + ` — longest first: ${ordered.map((s) => s.id).join(', ')}`)

// One self-contained chain per section. Every agent() inside carries the same
// `phase`, so the progress display shows seven per-section groups.
async function runSection(section) {
  const phase = section.title

  const draft = await agent(writePrompt(section), {
    label: `write:${section.id}`,
    phase,
    agentType: 'sra-writer',
    schema: STAGE_SCHEMA,
    ...STAGE_TUNING.write,
  })

  // Later stages are built from the ORIGINAL section, never from the previous
  // result: a stage that died returns null, and rebuilding the prompt from it
  // would take the whole section down rather than just that stage.
  const critique = await agent(criticPrompt(section), {
    label: `critic:${section.id}`,
    phase,
    agentType: 'sra-writer',
    schema: CRITIQUE_SCHEMA,
    ...STAGE_TUNING.critic,
  })

  const rewrite = await agent(rewritePrompt(section), {
    label: `rewrite:${section.id}`,
    phase,
    agentType: 'sra-writer',
    schema: STAGE_SCHEMA,
    ...STAGE_TUNING.rewrite,
  })

  // The rewrite is the section's final word; the draft stands only if the
  // rewrite died, in which case there is still a file on disk to account for.
  const final = rewrite || draft
  if (!final) return null
  return { ...final, section: section.id, critique: critique || null }
}

const results = await parallel(ordered.map((section) => () => runSection(section)))

const written = results.filter(Boolean)
const failed = written.filter((r) => !r.hard_checks_passed)
if (failed.length) {
  // Surfaced, never silently dropped: a section whose checks fail still has a
  // file on disk, and the assembler would embed it without knowing.
  log(`hard checks FAILED for: ${failed.map((r) => r.section).join(', ')}`)
}
if (written.length < ordered.length) {
  const done = written.map((r) => r.section)
  const missing = ordered.filter((s) => done.indexOf(s.id) === -1)
  log(`no draft returned for: ${missing.map((s) => s.id).join(', ')}`)
}

return {
  report_date,
  sections: written,
  hard_checks_failed: failed.map((r) => r.section),
  log_dir: `${workdir}/reports/${report_date}/log`,
  order: ordered.map((s) => s.id),
}
