"""Validate the checked-in production corpus and emit a reproducible receipt."""
import json

from security_auditor.config import ROOT
from security_auditor.native_skills import NativeSkillLoader
from security_auditor.skills import SkillLoader, digest


def main():
    native = NativeSkillLoader(ROOT / 'skills')
    app = SkillLoader(ROOT / 'skills')
    methods = []
    for manifest_path in sorted((ROOT / 'skills').glob('*/manifest.json')):
        manifest = json.loads(manifest_path.read_text())
        bundle = app.resolve(manifest['id'], manifest['id'], manifest['version'])
        methods.append({'id': bundle.skill_id, 'version': bundle.version,
                        'sha256': bundle.content_hash})
    print(json.dumps({'corpus_root': 'skills/', 'code_source': native.manifest['commit'],
                      'code_corpus_hash': native.fingerprint(),
                      'upstream_documents': len(native.manifest['files']),
                      'application_methods': methods, 'active_application_roles': app.catalog(),
                      'code_stages': native.catalog(),
                      'adapter_sha256': digest((native.code_root / 'static-adapter.md').read_bytes())},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
