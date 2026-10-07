"""Exact task-local source handles; no fuzzy matching or result interpretation."""
from __future__ import annotations

from .provider import OutputContractError


class SourceReferences:
    def __init__(self):
        self.by_id: dict[str, str] = {}
        self.by_handle: dict[str, str] = {}

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
            return {self.by_id.get(key, key): self.encode(item) for key, item in value.items()}
        return value

    def decode_submission(self, value: dict) -> dict:
        # Only declared source-reference fields are projected. Narrative,
        # record IDs, scope IDs, verdicts and acceptance criteria stay exact.
        def decode(item):
            if isinstance(item, list):
                return [decode(entry) for entry in item]
            if isinstance(item, dict):
                return {key: [self.resolve(ref) for ref in entry]
                        if key in {'evidence_ids', 'counter_evidence_ids'} else decode(entry)
                        for key, entry in item.items()}
            return item
        return decode(value)
