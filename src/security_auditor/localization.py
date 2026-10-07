"""Presentation strings and an explicit generation policy, never result translation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

RESOURCES = Path(__file__).parent / 'resources'
LANGUAGES = ('en', 'zh-CN')


def language(value: str) -> str:
    if value not in LANGUAGES:
        raise ValueError('语言仅支持 en 或 zh-CN')
    return value


def output_policy(selected: str = 'en') -> str:
    return (RESOURCES / 'language' / (language(selected) + '.md')).read_text()


def policy_hashes() -> dict[str, str]:
    return {code: hashlib.sha256(output_policy(code).encode()).hexdigest() for code in LANGUAGES}


def tr(text: str, selected: str = 'en') -> str:
    language(selected)
    if selected == 'zh-CN':
        return text
    return MESSAGES.get(text, text)


MESSAGES = json.loads((RESOURCES / 'messages.json').read_text())
