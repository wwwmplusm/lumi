"""Public CLI: one reading surface for projects and standalone libraries."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from lumi.library import Library

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def cli(tmp_path):
    def invoke(*args, cwd=None):
        env = {**os.environ, 'XDG_CONFIG_HOME': str(tmp_path / 'config')}
        env.pop('LUMI_PROJECT', None)
        return subprocess.run([sys.executable, str(REPO / 'lumi.py'), *map(str, args)],
                              cwd=cwd or tmp_path, env=env, capture_output=True, text=True)
    return invoke


def save(root, identifier='W123'):
    folder = Library(root).save_work({
        'id': f'https://openalex.org/{identifier}', 'display_name': 'Useful evidence',
        'publication_year': 2020, 'authorships': [{'author': {'display_name': 'Ada Example' if identifier == 'W123' else 'Ben Other'}}],
    })
    return root / folder


def pdf(path, blank=False):
    stream = b'BT /F1 12 Tf 72 720 Td (Useful evidence.) Tj ET\n'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R' + (b' 6 0 R' if blank else b'') + b'] /Count ' + (b'2' if blank else b'1') + b' >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'endstream']
    if blank:
        objects.append(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>')
    data, offsets = bytearray(b'%PDF-1.4\n'), [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n'
    start = len(data)
    data += f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
    for offset in offsets[1:]:
        data += f'{offset:010d} 00000 n \n'.encode()
    data += f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()
    path.write_bytes(data)


@pytest.mark.parametrize('blank', [False, True])
def test_direct_library_parse_and_lookup_without_project(cli, tmp_path, blank):
    if not shutil.which('pdftotext'):
        pytest.skip('pdftotext system dependency unavailable')
    root = tmp_path / 'standalone'
    folder = save(root)
    pdf(folder / 'paper.pdf', blank)
    parsed = cli('parse', 'Example 2020', '--library', root)
    assert parsed.returncode == int(blank), parsed.stderr
    assert 'partial text' in parsed.stdout if blank else 'partial text' not in parsed.stdout
    pages = json.loads((folder / 'pages.json').read_text())['pages']
    assert pages[0].strip() == 'Useful evidence.'
    found = cli('lookup', '"Useful evidence"', '--raw', '--paper', 'Example 2020', '--limit', '1', '--library', root)
    assert found.returncode == 0, found.stderr
    assert 'Example 2020' in found.stdout and folder.name in found.stdout
    assert not (tmp_path / 'lumi.json').exists()
    assert not (tmp_path / 'literature').exists()


@pytest.mark.parametrize("ref", ["W123", "Example 2020"])
def test_project_and_direct_library_lookup_share_output_and_filters(cli, tmp_path, ref):
    project = tmp_path / 'existing'
    root = project / 'literature'
    (project / 'research').mkdir(parents=True)
    folder = save(root)
    second = save(root, 'W456')
    for entry in (folder, second):
        (entry / 'pages.json').write_text(json.dumps({'pages': ['Useful evidence.', 'More evidence.']}))
    args = ['lookup', 'evidence', '--paper', ref, '--limit', '1']
    direct = cli(*args, '--library', root)
    nested = project / 'research' / 'nested'
    nested.mkdir()
    detected = cli(*args, cwd=nested)
    explicit = cli('--project', project, *args)
    assert direct.returncode == detected.returncode == explicit.returncode == 0
    assert direct.stdout == detected.stdout == explicit.stdout
    assert len(direct.stdout.splitlines()) == 1
    assert folder.name in direct.stdout and second.name not in direct.stdout


@pytest.mark.parametrize('args', [('field', 'build'), ('library', 'parse'), ('library', 'lookup', 'evidence')])
def test_removed_command_duplicates_are_rejected(cli, args):
    result = cli(*args)
    assert result.returncode == 2 and 'invalid choice' in result.stderr


def test_field_defaults_to_candidates_without_writing_report(cli, tmp_path):
    project = tmp_path / 'project'
    (project / 'literature').mkdir(parents=True)
    (project / 'research' / 'field').mkdir(parents=True)
    (project / 'research' / 'field' / 'candidates.jsonl').write_text(json.dumps({'id': 'W123', 'title': 'Candidate evidence'}) + '\n')
    result = cli('--project', project, 'field')
    assert result.returncode == 0, result.stderr
    assert 'Candidate evidence' in result.stdout
    assert not list(project.rglob('FIELD.md'))


def test_bad_standalone_reference_is_a_cli_error(cli, tmp_path):
    root = tmp_path / 'library'
    save(root)
    result = cli('parse', 'missing', '--library', root)
    assert result.returncode == 2 and 'Traceback' not in result.stderr


def test_project_parse_keeps_author_year_alias(cli, tmp_path):
    if not shutil.which('pdftotext'):
        pytest.skip('pdftotext system dependency unavailable')
    project = tmp_path / 'existing'
    (project / 'research').mkdir(parents=True)
    folder = save(project / 'literature')
    pdf(folder / 'paper.pdf')
    result = cli('--project', project, 'parse', 'Example 2020')
    assert result.returncode == 0, result.stderr
    assert json.loads((folder / 'pages.json').read_text())['pages'][0].strip() == 'Useful evidence.'
