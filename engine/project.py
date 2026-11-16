"""Project discovery and search history. Research files remain in place."""

import datetime as dt
import fcntl
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
if not (ENGINE / 'templates' / 'CONTEXT.md').exists():
    ENGINE = Path(sys.prefix) / 'share' / 'lumi'
MARKER = 'lumi.json'


def today():
    return dt.date.today().isoformat()


def find_root(start=None):
    if start or os.environ.get('LUMI_PROJECT'):
        return Path(start or os.environ['LUMI_PROJECT']).resolve()
    here = Path.cwd().resolve()
    for folder in (here, *here.parents):
        library = folder / 'literature'
        if (folder / MARKER).is_file() or (library.is_dir() and (
            (folder / 'research').is_dir() or (library / 'KEYS.tsv').is_file() or (library / 'INDEX.md').is_file()
        )):
            return folder
    raise ValueError('не найден проект: запустите из папки с literature/ и research/ или задайте --project / LUMI_PROJECT')


def read_jsonl(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []


@contextmanager
def file_lock(path):
    """Use a stable sidecar: the data file may be atomically replaced."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name('.' + path.name + '.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write_jsonl(path, rows):
    text = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)
    if path.exists() and path.read_text(encoding='utf-8') == text:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '.', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def append_jsonl(path, row):
    path = Path(path)
    with file_lock(path):
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.papers = self.root / 'literature'
        if any((self.root / 'sources' / 'papers').glob('*/metadata.json')):
            raise ValueError('sources/papers/ больше не поддерживается; старые файлы сохранены. Lumi использует literature/ + research/, автоматической миграции нет')
        self.research = self.root / 'research'
        self.field = self.research / 'field'
        config = self.root / MARKER
        self.config = {'name': self.root.name, **(json.loads(config.read_text(encoding='utf-8')) if config.exists() else {})}

    def search_log(self):
        return read_jsonl(self.field / 'search_log.jsonl')

    def candidates(self):
        return read_jsonl(self.field / 'candidates.jsonl')

    def update_candidates(self, callback):
        """Run callback(rows) under a lock; mutate rows in place, return any value.

        No network work belongs in callback. Unchanged rows preserve the data file.
        Search provenance belongs in search_log events, not candidate arrays.
        """
        path = self.field / 'candidates.jsonl'
        with file_lock(path):
            rows = read_jsonl(path)
            before = json.dumps(rows, ensure_ascii=False, sort_keys=True)
            result = callback(rows)
            if json.dumps(rows, ensure_ascii=False, sort_keys=True) != before:
                _write_jsonl(path, rows)
            return result

    def paper_ids(self):
        return sorted(p.parent.name for p in self.papers.glob('*/metadata.json'))

    def questions(self):
        path = self.research / 'questions.md'
        out = []
        for line in path.read_text(encoding='utf-8').splitlines() if path.exists() else []:
            # Legacy tags remain readable without becoming part of the question.
            match = re.match(r'\s*[-*]\s+(\w+):\s*(.+?)(?:\s*\|\s*keywords:\s*.*)?$', line)
            if match:
                out.append({'id': match.group(1), 'question': match.group(2).strip()})
        return out
