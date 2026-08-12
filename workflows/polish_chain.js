export const meta = {
  name: 'sra-polish-chain',
  description: 'cross-section check, conclusion and verdict, critique, shrink-gated polish, evaluation (spec §15.2)',
  phases: [
    { title: 'Cross-check', detail: 'number and narrative consistency across seven sections' },
    { title: 'Conclusion', detail: 'the verdict, key tests, monitoring dashboard, verdict.json' },
    { title: 'Critique', detail: 'whole-report review no section critic could do' },
    { title: 'Polish', detail: 'apply the worklists under a word-count shrink gate' },
    { title: 'Evaluate', detail: 'six-dimension score into evaluation.json' },
  ],
}

// Five SEQUENTIAL stages (§15.2). Unlike the write wave, nothing here is
// parallelizable: each stage reads what the previous one wrote, and the polish
// stage in particular consumes both worklists. A pipeline would be wrong.
//
// args: {ticker, company, workdir, report_date, sections, char_caps, stages}
//   workdir   absolute path to data/<TICKER>
//   sections  [{id, title, ...}] — same shape the write wave took
//   stages    optional subset, in §15.2 order. §23.2: a run with fewer than
//             three dirty sections earns only the cross-section check and the
//             conclusion/verdict, because a two-section edit does not justify
//             rewriting the whole document. /sra-assemble makes that call — the
//             script only honors it, and always runs the full five when the
//             caller says nothing.

// Some harness builds deliver `args` as a JSON string rather than a parsed
// object; destructuring that directly yields undefined and fails opaquely.
// Accept either shape.
const input = typeof args === 'string' ? JSON.parse(args) : args

const { ticker, company, workdir, report_date, sections, char_caps } = input

const ALL_STAGES = ['cross_section', 'conclusion', 'critique', 'polish', 'evaluate']
const requested = Array.isArray(input.stages) && input.stages.length
  ? ALL_STAGES.filter((s) => input.stages.includes(s))
  : ALL_STAGES
const runs = (stage) => requested.includes(stage)

const runDir = `${workdir}/reports/${report_date}`
const sectionsDir = `${runDir}/sections`
// The pre-polish copy the shrink gate measures against. The orchestrating skill
// makes this snapshot before launching the chain; without it `not_longer_than`
// has no baseline and the gate silently disappears.
const baselineDir = `${runDir}/sections_prepolish`

const crossCheckPath = `${runDir}/cross_check.json`
const conclusionPath = `${runDir}/conclusion.md`
const verdictPath = `${runDir}/verdict.json`
const critiquePath = `${runDir}/critique.md`
const evaluationPath = `${runDir}/evaluation.json`

const sectionIds = sections.map((s) => s.id).join(' ')

// Conclusion length: §15.2 gives the chain the same `char_caps` the write wave
// took, so the conclusion's budget comes from the same place as a section's
// rather than being hard-coded here.
const conclusionWords = char_caps && char_caps.conclusion
  ? Math.round(char_caps.conclusion / 8)
  : 900

// One log per agent under the run's log/ directory (§23.4). `purpose` must come
// from §23.4's closed vocabulary — which is why these are `cross-section` and
// not the `cross-check` the progress label uses.
const LOG_SEQUENCE = {
  cross_section: ['section', 20], conclusion: ['conclusion', 21],
  critique: ['critique', 22], polish: ['polish', 23], evaluate: ['evaluate', 24],
}
const LOG_PURPOSE = {
  cross_section: 'cross-section', conclusion: 'conclusion',
  critique: 'critique', polish: 'polish', evaluate: 'evaluate',
}

function logPath(stage) {
  const [slug, sequence] = LOG_SEQUENCE[stage]
  return `${runDir}/log/${sequence}_${LOG_PURPOSE[stage]}_${slug}.md`
}

function taskLogContract(stage) {
  return [
    'BEFORE you start, record the time:',
    '  date -u +%Y-%m-%dT%H:%M:%SZ',
    '',
    'AFTER you finish, take the time again and write your task log to',
    `${logPath(stage)} — exactly this shape, and nothing else in that file:`,
    '',
    '  ---',
    `  purpose: ${LOG_PURPOSE[stage]}`,
    '  section: null',
    '  round: 1',
    `  label: "${stage}"`,
    '  started_at: <the first timestamp>',
    '  finished_at: <the second timestamp>',
    '  status: ok | degraded | failed',
    '  outputs: [<paths you wrote, relative to the run directory>]',
    '  ---',
    '',
    '  ## Inputs',
    '  - the sections and artifacts you actually read',
    '',
    '  ## Outputs',
    '  - what you wrote, with word counts',
    '',
    '  ## Notes',
    '  What you changed and why, what you deliberately left alone, and anything',
    '  you could not resolve. Be specific and brief — this is the only account',
    '  of your work that survives.',
    '',
    'Write the log even when the work failed. A failed stage with no log is',
    'indistinguishable from a stage that never ran.',
  ].join('\n')
}

function preamble(role, stage) {
  return [
    `You are the ${role} for an equity research report on ${company} (${ticker}).`,
    '',
    'Read STYLE.md at the repo root before you start; it governs how the prose',
    'must read, and it is the same guide every section writer worked from.',
    '',
    'Do no independent retrieval — no web, no MCP, no new fetches. A fact',
    'discovered now has no bronze id, cannot be cited, and cannot survive',
    'assembly. Everything you need is in the sections and in the artifacts',
    `under ${workdir}.`,
    '',
    taskLogContract(stage),
  ].join('\n')
}

function fill(promptFile, role, stage, placeholders) {
  const lines = [
    preamble(role, stage),
    '',
    `Follow prompts/polish/${promptFile} exactly. Read it first.`,
    '',
    'Placeholder values:',
    `  {ticker} = ${ticker}`,
    `  {company} = ${company}`,
    `  {workdir} = ${workdir}`,
    `  {sections_dir} = ${sectionsDir}`,
    `  {baseline_dir} = ${baselineDir}`,
    `  {section_ids} = ${sectionIds}`,
    `  {cross_check_path} = ${crossCheckPath}`,
    `  {conclusion_path} = ${conclusionPath}`,
    `  {verdict_path} = ${verdictPath}`,
    `  {critique_path} = ${critiquePath}`,
    `  {evaluation_path} = ${evaluationPath}`,
    `  {log_path} = ${logPath(stage)}`,
  ]
  for (const key of Object.keys(placeholders)) {
    lines.push(`  {${key}} = ${placeholders[key]}`)
  }
  return lines.join('\n')
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    conclusion_path: { type: 'string' },
    verdict_path: { type: 'string' },
    rating: { type: 'string' },
    fair_value: { type: ['number', 'null'] },
    current_price: { type: ['number', 'null'] },
    words: { type: 'number' },
  },
  required: ['conclusion_path', 'verdict_path', 'rating'],
  additionalProperties: true,
}

const POLISH_SCHEMA = {
  type: 'object',
  properties: {
    applied: { type: 'array' },
    skipped: { type: 'array' },
    words_before: { type: 'number' },
    words_after: { type: 'number' },
    shrink_gate_passed: { type: 'boolean' },
  },
  required: ['shrink_gate_passed'],
  additionalProperties: true,
}

const EVALUATION_SCHEMA = {
  type: 'object',
  properties: {
    overall_score: { type: 'number' },
    weakest_dimension: { type: 'string' },
    failed_spot_checks: { type: 'array' },
  },
  required: ['overall_score'],
  additionalProperties: true,
}

log(`polish chain: ${sections.length} sections in reports/${report_date}`
    + ` — stages: ${requested.join(', ')}`)

phase('Cross-check')
const crossCheck = runs('cross_section') ? await agent(
  fill('cross_section.md', 'CONSISTENCY EDITOR', 'cross_section', {}),
  { label: 'cross-check', agentType: 'sra-writer' },
) : null

phase('Conclusion')
const verdict = runs('conclusion') ? await agent(
  fill('conclusion.md', 'CONCLUSION WRITER', 'conclusion',
       { word_target: conclusionWords }),
  { label: 'conclusion', agentType: 'sra-writer', schema: VERDICT_SCHEMA },
) : null

phase('Critique')
const critique = runs('critique') ? await agent(
  fill('critique.md', 'WHOLE-REPORT CRITIC', 'critique', {}),
  { label: 'critique', agentType: 'sra-writer' },
) : null

phase('Polish')
const polish = runs('polish') ? await agent(
  fill('polish.md', 'POLISH EDITOR', 'polish', {}),
  { label: 'polish', agentType: 'sra-writer', schema: POLISH_SCHEMA },
) : null

if (polish && polish.shrink_gate_passed === false) {
  // Surfaced, never swallowed: a polish pass that grew the report has failed
  // its one measurable obligation, and the assembler would embed it anyway.
  log('shrink gate FAILED — the polish pass grew the report')
}

phase('Evaluate')
const evaluation = runs('evaluate') ? await agent(
  fill('evaluate.md', 'INDEPENDENT ASSESSOR', 'evaluate', {}),
  { label: 'evaluate', agentType: 'sra-writer', schema: EVALUATION_SCHEMA },
) : null

return {
  report_date,
  cross_check_path: crossCheckPath,
  conclusion_path: conclusionPath,
  // The driver recomputes implied_return_pct from fair_value and current_price
  // before assembly (§15.3) — it does not trust model arithmetic, so what this
  // returns is the model's claim, not the number the report will show.
  verdict_path: verdictPath,
  verdict: verdict,
  critique_path: critiquePath,
  polish: polish,
  evaluation: evaluation,
  shrink_gate_passed: polish ? polish.shrink_gate_passed : null,
  stages_requested: requested,
  stages_completed: [crossCheck, verdict, critique, polish, evaluation]
    .filter(Boolean).length,
}
