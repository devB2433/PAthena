"""Exact task-local source handles; no fuzzy matching or result interpretation."""
from __future__ import annotations

import re

from .provider import OutputContractError


class SourceReferences:
    def __init__(self):
        self.by_id: dict[str, str] = {}
        self.by_handle: dict[str, str] = {}
        self.by_quote: dict[str, dict[str, str]] = {}
        self.quote_keys: dict[tuple[str, str], str] = {}

    def register_quotes(self, source):
        """Address all original paragraphs; never summarize or infer a passage."""
        passages = []
        for paragraph in re.split(r'\n\s*\n', source['content']):
            for start in range(0, len(paragraph), 5000):
                text = paragraph[start:start + 5000]
                if not text.strip():
                    continue
                key = (source['id'], text)
                if key not in self.quote_keys:
                    handle = f'Q{len(self.by_quote) + 1:05d}'
                    self.quote_keys[key] = handle
                    self.by_quote[handle] = {'evidence_id': source['id'], 'text': text}
                passages.append({'quote_id': self.quote_keys[key], 'text': text})
        return passages

    def register(self, identifiers):
        for identifier in identifiers:
            if identifier not in self.by_id:
                handle = f'S{len(self.by_id) + 1:04d}'
                self.by_id[identifier] = handle
                self.by_handle[handle] = identifier

    def resolve(self, value: str) -> str:
        if value in self.by_handle:
            return self.by_handle[value]
        if value in self.by_id:  # Exact original IDs remain compatible.
            return value
        raise OutputContractError('来源编号不在本任务登记表中，阶段未完成')

    def encode(self, value):
        if isinstance(value, str):
            return self.by_id.get(value, value)
        if isinstance(value, list):
            return [self.encode(item) for item in value]
        if isinstance(value, dict):
            # Upstream quote IDs belong to another invocation. Preserve the
            # stored original text and source, not the prior transport handle.
            return {self.by_id.get(key, key): self.encode(item) for key, item in value.items()
                    if not (key == 'quote_id' and 'evidence_id' in value)}
        return value

    def decode_submission(self, value: dict, *, quote_handles_required: bool = False) -> dict:
        # Only declared source-reference fields are projected. Narrative,
        # record IDs, scope IDs, verdicts and acceptance criteria stay exact.
        def decode(item):
            if isinstance(item, list):
                return [decode(entry) for entry in item]
            if isinstance(item, dict):
                if item.get('quote_id') is not None:
                    original = self.by_quote.get(item['quote_id'])
                    if not original:
                        raise OutputContractError('原文段落编号未在本次读取中登记')
                    if item.get('text') and item['text'] != original['text']:
                        raise OutputContractError('段落编号与引文内容冲突')
                    if item.get('evidence_id') and self.resolve(item['evidence_id']) != original['evidence_id']:
                        raise OutputContractError('段落编号与来源冲突')
                    return {**item, **original}
                result = {}
                for key, entry in item.items():
                    if key == 'quotes' and quote_handles_required and any(not q.get('quote_id') for q in entry):
                        raise OutputContractError('引文必须使用本次已读原文的段落编号')
                    if key == 'evidence_id':
                        result[key] = self.resolve(entry)
                    elif key in {'evidence_ids', 'counter_evidence_ids'}:
                        result[key] = [self.resolve(ref) for ref in entry]
                    else:
                        result[key] = decode(entry)
                if quote_handles_required:
                    claims = result.get('source_obligations', result.get('claim_reviews'))
                    if isinstance(claims, list):
                        # The selected paragraph already declares its source.
                        # Complete the provenance closure by exact lookup, not
                        # by asking the model to echo another redundant ID.
                        selected = [q['evidence_id'] for claim in claims for q in claim.get('quotes', [])]
                        result['evidence_ids'] = list(dict.fromkeys([*result.get('evidence_ids', []), *selected]))
                return result
            return item
        return decode(value)
