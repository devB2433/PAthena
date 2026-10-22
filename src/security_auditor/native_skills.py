"""Canonical, byte-verified upstream skills shared by production and tests."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import ROOT, contained_file
from .skills import SkillBundle, digest

SUPPLEMENTAL_FILES = {'mantis-configure/SKILL.md': {'sha256': '8547c0f1485c3a01f2741bd377b82444e2892d0dcfb935917bd1c4507a5ee948', 'upstream': 'reference/skills/mantis-configure/SKILL.md'}, 'mantis-launch/SKILL.md': {'sha256': '7bbb0d5afc95f118c96b3db9dcfcddc8b63311406bfd4e0438284bc9881a2e1d', 'upstream': 'reference/skills/mantis-launch/SKILL.md'}}

STAGE_SKILLS = {
    'history': 'mantis-history', 'structural_index': 'mantis-structural-index',
    'architect': 'mantis-architecture', 'threat_modeler': 'mantis-threat-model',
    'planner': 'mantis-plan', 'researcher': 'mantis-researcher',
    'deduplicator': 'mantis-dedupe', 'reviewer': 'mantis-review',
    'critic': 'mantis-critic', 'chainer': 'mantis-chain',
    'calibrator': 'mantis-calibrate', 'reflector': 'mantis-reflect',
    'reporter': 'mantis-report',
}


class NativeSkillLoader:
    def __init__(self, root: Path | None = None):
        self.root = root or Path(os.getenv('AUDITOR_SKILLS_DIR', str(ROOT / 'skills')))
        self.code_root = self.root / 'code'
        self.manifest = json.loads(contained_file(self.code_root, 'SOURCE.json').read_text())
        if self.manifest['runtime_roles'] != STAGE_SKILLS:
            raise ValueError('原版技能角色登记不一致')
        upstream = json.loads(contained_file(Path(__file__).parent / 'vendor/mantis', 'SOURCE.json').read_text())
        expected = {name.removeprefix('skills/'): {'sha256': row['sha256'], 'upstream': row['upstream']}
                    for name, row in upstream['files'].items() if name.startswith('skills/') and not row['modified']}
        expected.update(SUPPLEMENTAL_FILES)
        if (self.manifest['files'] != expected or self.manifest['commit'] != upstream['commit']
                or self.manifest['repository'] != upstream['repository']):
            raise ValueError('代码技能目录必须对应固定原版来源，不能用改写摘要替代')

    def read(self, name: str) -> str:
        declared = self.manifest['files'].get(name)
        if not declared:
            raise ValueError('原版技能资源未登记')
        content = contained_file(self.code_root, name).read_bytes()
        if digest(content) != declared['sha256']:
            raise ValueError('原版技能资源摘要不一致')
        return content.decode()

    def localized_manifest(self) -> dict:
        manifest = json.loads(contained_file(self.code_root, 'LOCALIZATION.zh-CN.json').read_text())
        expected = {'static-adapter.md'}
        for skill in STAGE_SKILLS.values():
            expected.add(skill + '/SKILL.md')
            expected.update(name for name in self.manifest['files'] if name.startswith(skill + '/references/'))
        if (manifest.get('language') != 'zh-CN' or manifest.get('source_commit') != self.manifest['commit']
                or set(manifest.get('files', {})) != expected):
            raise ValueError('中文代码技能必须完整对应固定原版资源库存')
        return manifest

    def read_localized(self, name: str) -> str:
        entry = self.localized_manifest()['files'].get(name)
        if not entry or entry['path'] != 'zh-CN/' + name:
            raise ValueError('中文技能资源路径未登记')
        original = contained_file(self.code_root, name).read_bytes()
        content = contained_file(self.code_root, entry['path']).read_bytes()
        if (digest(original) != entry['source_sha256'] or digest(content) != entry['sha256']
                or entry['source_lines'] != len(original.splitlines())
                or entry['lines'] != len(content.splitlines())):
            raise ValueError('中文技能或对应原版的摘要、行库存不一致')
        return content.decode()

    def resolve(self, stage: str, language: str = 'en') -> SkillBundle:
        from .localization import language as checked_language
        checked_language(language)
        if stage not in STAGE_SKILLS:
            raise ValueError('代码审计角色未登记，禁止使用简版后备技能')
        skill = STAGE_SKILLS[stage]
        read = self.read_localized if language == 'zh-CN' else self.read
        body = read(skill + '/SKILL.md')
        resources = sorted(name for name in self.manifest['files']
                           if name.startswith(skill + '/references/'))
        # References remain exact, separate source documents, never summarized.
        heading = '原版方法的完整中文参考资料：' if language == 'zh-CN' else 'UPSTREAM REFERENCE: '
        packet = body + ''.join('\n\n' + heading + name + '\n' + read(name) for name in resources)
        return SkillBundle(skill, self.manifest['commit'], digest(packet.encode()), packet, ())

    def catalog(self, language: str = 'en') -> list[dict]:
        return [{'stage': stage, 'id': bundle.skill_id, 'version': bundle.version,
                 'sha256': bundle.content_hash, 'source': self.manifest['repository'],
                 'execution': 'deterministic' if stage == 'structural_index' else 'agent'}
                for stage in STAGE_SKILLS for bundle in [self.resolve(stage, language)]]

    def fingerprint(self) -> str:
        # Verify every copied skill, including retained but disabled dynamic stages.
        for name in self.manifest['files']:
            self.read(name)
        return digest(contained_file(self.code_root, 'SOURCE.json').read_bytes()
                      + contained_file(self.code_root, 'static-adapter.md').read_bytes())

    def localization_fingerprint(self) -> str:
        manifest = self.localized_manifest()
        for name in manifest['files']:
            self.read_localized(name)
        return digest(contained_file(self.code_root, 'LOCALIZATION.zh-CN.json').read_bytes())

    def compile(self, stage: str, language: str = 'en') -> tuple[SkillBundle, str, str]:
        from .localization import output_policy
        bundle = self.resolve(stage, language)
        adapter = self.read_localized('static-adapter.md') if language == 'zh-CN' else contained_file(self.code_root, 'static-adapter.md').read_text()
        prompt = (f'MANTIS_STAGE:{stage}\n' + ('完整中文技能：\n' if language == 'zh-CN' else 'UPSTREAM FULL SKILL:\n') + bundle.instruction
                  + ('\n\n部署适配与提交契约：\n' if language == 'zh-CN' else '\n\nDEPLOYMENT ADAPTER CONTRACT:\n') + adapter
                  + '\n\n' + output_policy(language))
        return bundle, prompt, digest(prompt.encode())
