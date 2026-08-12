export const meta = {
  name: 'sra-write-wave',
  description: 'write -> critic -> rewrite for each report section (spec §15.1, §15.2)',
  phases: [
    { title: 'Write', detail: 'one writer per section, from its wiki page' },
    { title: 'Critique', detail: 'adversarial review with citation verification' },
    { title: 'Rewrite', detail: 'apply the critique, re-run hard checks' },
  ],
}

// The cold build's write phase: seven sections x three agents (§15.1).
//
// A pipeline, not three barriers. Sections are independent — they read
// different wiki pages and write different files — so valuation can be in
// rewrite while risk_news is still drafting. Three barriers would make every
// stage wait for the slowest section, which on a 21-agent wave is most of the
// wall clock for no benefit.
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

const { ticker, company, workdir, report_date, sections, char_caps } = args

const REPO = 'the repo root (where sra.py lives)'

function draftPath(section) {
  return `${workdir}/reports/${report_date}/sections/${section.id}.md`
}

function critiquePath(section) {
  return `${workdir}/reports/${report_date}/sections/${section.id}.critique.md`
}

function hardChecks(section) {
  const rules = (section.hard_checks || []).slice()
  const cap = char_caps ? char_caps[section.id] : null
  // The character cap is a per-run budget (length preset x word target), not a
  // property of the section, so it is appended here rather than living in
  // sections.yaml next to the structural checks.
  if (cap) rules.push(`max_length: ${cap}`)
  return JSON.stringify(rules)
}

// Every prompt opens the same way: who you are, what to read, and the fact that
// the pieces live on disk rather than in this script.
function preamble(section, role) {
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
    `  {hard_checks_json} = ${hardChecks(section)}`,
  ].join('\n')
}

function writePrompt(section) {
  return [
    preamble(section, 'WRITER'),
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
    preamble(section, 'CRITIC'),
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
    '   "contradicted": <n>, "unsupported": <n>}',
  ].join('\n')
}

function rewritePrompt(section) {
  return [
    preamble(section, 'REWRITE AGENT'),
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

log(`write wave: ${sections.length} sections into reports/${report_date}/sections/`)

const results = await pipeline(
  sections,
  (section) =>
    agent(writePrompt(section), {
      label: `write:${section.id}`,
      phase: 'Write',
      agentType: 'sra-writer',
      schema: STAGE_SCHEMA,
    }),
  // Later stages take the ORIGINAL section, not the previous result: a stage
  // that died returns null, and rebuilding the prompt from it would take the
  // whole item down rather than just that stage.
  (_draft, section) =>
    agent(criticPrompt(section), {
      label: `critic:${section.id}`,
      phase: 'Critique',
      agentType: 'sra-writer',
    }),
  (_critique, section) =>
    agent(rewritePrompt(section), {
      label: `rewrite:${section.id}`,
      phase: 'Rewrite',
      agentType: 'sra-writer',
      schema: STAGE_SCHEMA,
    }),
)

const written = results.filter(Boolean)
const failed = written.filter((r) => !r.hard_checks_passed)
if (failed.length) {
  // Surfaced, never silently dropped: a section whose checks fail still has a
  // file on disk, and the assembler would embed it without knowing.
  log(`hard checks FAILED for: ${failed.map((r) => r.section).join(', ')}`)
}
if (written.length < sections.length) {
  const done = written.map((r) => r.section)
  const missing = sections.filter((s) => done.indexOf(s.id) === -1)
  log(`no draft returned for: ${missing.map((s) => s.id).join(', ')}`)
}

return {
  report_date,
  sections: written,
  hard_checks_failed: failed.map((r) => r.section),
}
