"""Read-only literal grep bridge for the upstream exhaustive call-site method."""
from pathlib import Path, PurePosixPath

from .config import contained_file
from .skills import digest


class SnapshotSourceSearch:
    def __init__(self, root: Path, inventory: list[dict]):
        self.root, self.inventory = root, inventory

    def search(self, query: str, directory: str = '', offset: int = 0, limit: int = 50) -> dict:
        if (not query.strip() or len(query) > 512 or offset < 0 or not 1 <= limit <= 100
                or directory.startswith('/') or '\\' in directory or '..' in PurePosixPath(directory).parts):
            raise ValueError('源码搜索参数或目录无效')
        directory = str(PurePosixPath(directory))
        if directory == '.':
            directory = ''
        items, gaps, scanned = [], [], 0
        for entry in self.inventory:
            name = entry['path']
            if directory and name != directory and not name.startswith(directory + '/'):
                continue
            if entry['status'] != 'READABLE':
                gaps.append({'path': name, 'status': entry['status']})
                continue
            raw = contained_file(self.root, name).read_bytes()
            if digest(raw) != entry['sha256']:
                raise ValueError('源码搜索的快照文件摘要已变化')
            try:
                lines = raw.decode().splitlines()
            except UnicodeError:
                gaps.append({'path': name, 'status': 'NOT_UTF8'})
                continue
            scanned += 1
            for number, line in enumerate(lines, 1):
                if query.casefold() in line.casefold():
                    items.append({'path': name, 'line': number, 'excerpt': line[:400]})
        return {'items': items[offset:offset + limit], 'total_matches': len(items),
                'has_more': offset + limit < len(items), 'next_offset': offset + len(items[offset:offset + limit]),
                'scanned_files': scanned, 'gaps': gaps, 'read_required': True,
                'coverage': 'Complete literal search of readable registered files in requested scope; not semantic reachability.'}
