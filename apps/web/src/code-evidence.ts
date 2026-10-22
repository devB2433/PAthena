export type SourceMetadata = {
  path?: string; line_start?: number; line_end?: number;
  provenance?: { page_no?: number }[]; source?: { pdf_pages?: number[] };
};
export type Source = { id: string; source_type: string; locator: string; content?: string; metadata?: string | SourceMetadata };
type Check = { evidence_ids: string[]; counter_evidence_ids?: string[] };
type Requirement = { criterion_checks?: Check[] };

export function codeSources(check: Check, sources: Source[], counter = false): Source[] {
  const ids = new Set(counter ? check.counter_evidence_ids || [] : check.evidence_ids);
  return sources.filter(source => source.source_type === 'code' && ids.has(source.id));
}

export function implementationCodeSources(requirement: Requirement, sources: Source[]): Source[] {
  const ids = new Set((requirement.criterion_checks || []).flatMap(check => check.evidence_ids));
  return sources.filter(source => source.source_type === 'code' && ids.has(source.id));
}

export function implementationReferences(requirement: Requirement): string[] {
  return [...new Set((requirement.criterion_checks || []).flatMap(check =>
    [...check.evidence_ids, ...(check.counter_evidence_ids || [])]))];
}

export function codeFirstLine(source: Source): number | undefined {
  let metadata: SourceMetadata = {};
  try { metadata = typeof source.metadata === 'string' ? JSON.parse(source.metadata) : source.metadata || {}; } catch { /* Use the recorded locator for older sources. */ }
  const value = metadata?.line_start;
  if (typeof value === 'number' && Number.isSafeInteger(value) && value > 0) return value;
  const match = source.locator.match(/:(\d+)(?:-\d+)?$/);
  const line = match ? Number(match[1]) : 0;
  return Number.isSafeInteger(line) && line > 0 ? line : undefined;
}
