"""Copy the pinned skill corpus verbatim into the canonical repository registry."""
import json
import shutil

from security_auditor.config import ROOT
from security_auditor.native_skills import STAGE_SKILLS, SUPPLEMENTAL_FILES
from security_auditor.skills import digest


def main():
    source = ROOT / 'src/security_auditor/vendor/mantis'
    manifest = json.loads((source / 'SOURCE.json').read_text())
    target = ROOT / 'skills/code'
    target.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, entry in manifest['files'].items():
        if not name.startswith('skills/'):
            continue
        raw = (source / name).read_bytes()
        if digest(raw) != entry['sha256'] or entry['modified']:
            raise ValueError('Only pinned, unchanged source skills can be imported')
        relative = name.removeprefix('skills/')
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, output)
        files[relative] = {'sha256': entry['sha256'], 'upstream': entry['upstream']}
    for name, entry in SUPPLEMENTAL_FILES.items():
        # Already checked-in setup skills are retained on ordinary resync.
        # Their pinned digests prevent substituting an unrelated installation guide.
        raw = (target / name).read_bytes()
        if digest(raw) != entry['sha256']:
            raise ValueError('Supplemental upstream skill does not match its pinned digest')
        files[name] = entry
    (target / 'SOURCE.json').write_text(json.dumps({
        'repository': manifest['repository'], 'commit': manifest['commit'],
        'license': manifest['license'], 'runtime_roles': STAGE_SKILLS, 'files': files,
        'policy': 'Verbatim upstream methods; static execution and transport are isolated in static-adapter.md.'
    }, ensure_ascii=False, indent=2) + '\n')
    print(f'Copied {len(files)} original skill files into skills/code')


if __name__ == '__main__':
    main()
