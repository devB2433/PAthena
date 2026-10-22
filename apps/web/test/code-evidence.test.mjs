import { test } from 'node:test';
import assert from 'node:assert/strict';
import { codeFirstLine, codeSources, implementationCodeSources, implementationReferences } from '../src/code-evidence.ts';

const sources = [
  { id: 'design', source_type: 'document', locator: 'design.md:1' },
  { id: 'standard', source_type: 'standard', locator: '8.3.1' },
  { id: 'auth', source_type: 'code', locator: 'auth.go:41-47', metadata: { line_start: 41, line_end: 47 } },
  { id: 'validation', source_type: 'code', locator: 'validate.go:80-92', metadata: '{"line_start":80}' },
  { id: 'counter', source_type: 'code', locator: 'fallback.go:10-14' },
  { id: 'unrelated', source_type: 'code', locator: 'other.go:5-8' },
];
const first = { evidence_ids: ['design', 'auth', 'standard', 'auth'], counter_evidence_ids: ['counter'] };
const second = { evidence_ids: ['validation', 'auth'] };

test('each criterion shows only its own code, with counter-evidence separated', () => {
  assert.deepEqual(codeSources(first, sources).map(s => s.id), ['auth']);
  assert.deepEqual(codeSources(first, sources, true).map(s => s.id), ['counter']);
  assert.deepEqual(codeSources(second, sources).map(s => s.id), ['auth', 'validation']);
});
test('list references come from checks, never requirement sources or unrelated code', () => {
  assert.deepEqual(implementationCodeSources({ evidence_ids: ['unrelated'], criterion_checks: [first, second] }, sources)
    .map(s => s.id), ['auth', 'validation']);
  assert.deepEqual(implementationCodeSources({}, sources), []);
});
test('one detail fetch per exact source ID, including separate counter-evidence', () => {
  assert.deepEqual(implementationReferences({ criterion_checks: [first, second] }),
    ['design', 'auth', 'standard', 'counter', 'validation']);
});
test('document-only and unavailable source IDs cannot appear as code support', () => {
  assert.deepEqual(codeSources({ evidence_ids: ['design', 'standard', 'missing'] }, sources), []);
});
test('line numbering uses recorded metadata in object and JSON formats', () => {
  assert.equal(codeFirstLine(sources[2]), 41);
  assert.equal(codeFirstLine(sources[3]), 80);
});
test('legacy code locations supply the original first line', () => {
  assert.equal(codeFirstLine(sources[4]), 10);
  assert.equal(codeFirstLine({ ...sources[2], metadata: 'malformed' }), 41);
});
test('missing or invalid positions never invent line 1', () => {
  for (const metadata of [undefined, 'null', { line_start: -1 }, { line_start: 1.5 }, { line_start: 1e30 }]) {
    assert.equal(codeFirstLine({ id: 'legacy', source_type: 'code', locator: 'legacy.go', metadata }), undefined);
  }
});
