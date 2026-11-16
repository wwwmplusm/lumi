"""Regression scenarios from the specification audit; all data stays in tmp_path."""
import json
from pathlib import Path

import pytest

from engine import papers, reports, sources
from engine.project import Project
from lumi.cli import main
from lumi.library import Library, folder_status


@pytest.fixture
def clean_project(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    root = tmp_path / 'project'
    (root / 'literature').mkdir(parents=True)
    (root / 'research').mkdir()
    monkeypatch.chdir(root)
    monkeypatch.delenv('LUMI_PROJECT', raising=False)
    return Project(root)


def save(project, identifier='W123'):
    library = Library(project.papers)
    name = library.save_work({'id': f'https://openalex.org/{identifier}', 'display_name': 'Example evidence',
                             'publication_year': 2020, 'authorships': [{'author': {'display_name': 'Ada Example'}}]})
    return project.papers / name


@pytest.mark.parametrize('command', [['parse', 'W123'], ['library', 'parse', 'W123'], ['add', 'W123']])
def test_partial_extraction_is_not_success(clean_project, monkeypatch, command, capsys):
    folder = save(clean_project)
    (folder / 'paper.pdf').write_bytes(b'fixture')
    monkeypatch.setattr('lumi.library.extract_pdf_text', lambda pdf: (['Title', '', ''], 'Title\f\f\f'))
    monkeypatch.setattr(papers, 'qpdf_labels', lambda *a: None)
    assert main(command) == 1
    meta = json.loads((folder / 'metadata.json').read_text())
    assert meta['fulltext'] == 'partial'
    assert meta['text_extraction'] == 'partial'
    assert meta['empty_pdf_pages'] == [2, 3]
    assert papers.load_meta(clean_project, folder.name)['fulltext'] == 'partial'
    assert folder_status(folder) == 'partial text'
    assert json.loads((folder / 'pages.json').read_text())['pages'] == ['Title', '', '']
    assert 'partial text' in capsys.readouterr().out
    reports.build_field(clean_project)
    field = (clean_project.field / 'FIELD.md').read_text()
    assert 'полный текст 0 · неполный текст 1' in field
    assert 'нет текста 0' in field


def test_partial_reparse_preserves_verified_pages(clean_project, monkeypatch):
    folder = save(clean_project)
    (folder / 'paper.pdf').write_bytes(b'fixture')
    (folder / 'pages.json').write_text(json.dumps({'pages': ['Verified first', 'Verified second']}))
    (folder / 'paper.txt').write_text('Verified original')
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    monkeypatch.setattr('lumi.library.extract_pdf_text', lambda pdf: (['New first', ''], 'New first\f\f'))
    assert main(['parse', 'W123']) == 1
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


@pytest.mark.parametrize('reason', ['no text layer; OCR required', 'pdftotext failed'])
def test_add_propagates_extraction_failure(clean_project, monkeypatch, reason, capsys):
    folder = save(clean_project)
    (folder / 'paper.pdf').write_bytes(b'fixture')
    def fail(pdf):
        raise RuntimeError(reason)
    monkeypatch.setattr('lumi.library.extract_pdf_text', fail)
    assert main(['add', 'W123']) == 1
    assert reason in capsys.readouterr().out
    assert not (folder / 'pages.json').exists()
    assert (folder / 'paper.pdf').read_bytes() == b'fixture'


def test_add_no_copy_and_download_error_are_distinct(clean_project, monkeypatch, capsys):
    folder = save(clean_project)
    monkeypatch.setattr(papers, 'pdf_candidates', lambda work: iter(()))
    assert main(['add', 'W123']) == 1
    assert json.loads((folder / 'metadata.json').read_text())['download_status'] == 'no_open_copy'
    assert 'открытая копия не найдена' in capsys.readouterr().out
    monkeypatch.setattr(papers, 'pdf_candidates', lambda work: iter(['https://example.org/paper.pdf']))
    monkeypatch.setattr(papers, 'download_pdf', lambda *a: (_ for _ in ()).throw(ValueError('invalid PDF')))
    assert main(['add', 'W123']) == 1
    assert json.loads((folder / 'metadata.json').read_text())['download_status'] == 'failed'
    assert 'invalid PDF' in capsys.readouterr().out


def test_add_complete_text_succeeds_and_card_has_no_stale_status(clean_project, monkeypatch):
    folder = save(clean_project)
    (folder / 'paper.pdf').write_bytes(b'fixture')
    monkeypatch.setattr('lumi.library.extract_pdf_text', lambda pdf: (['Complete text'], 'Complete text\f'))
    monkeypatch.setattr(papers, 'qpdf_labels', lambda *a: None)
    assert main(['add', 'W123']) == 0
    readme = (folder / 'README.md').read_text()
    assert 'no open PDF found' not in readme and 'Text extraction: not available' not in readme
    assert 'metadata.json' in readme


@pytest.mark.parametrize('query,page', [('人工智能', 1), ('研究', 1), ('人工智能 研究', 1), ('科学', 1), ('научные', 2), ('laboratories', 3)])
def test_multilingual_lookup(clean_project, query, page):
    folder = save(clean_project)
    (folder / 'pages.json').write_text(json.dumps({'pages': ['人工智能技术正在改变科学研究。', 'Современные научные исследования.', 'New laboratory evidence.']}))
    library = Library(clean_project.papers)
    rows = library.lookup(query)
    assert [row[:2] for row in rows] == [(folder.name, page)]
    assert main(['lookup', query]) == 0
    assert main(['library', 'lookup', query]) == 0


def test_mixed_script_lookup_deduplicates_filters_and_refreshes(clean_project):
    folder = save(clean_project)
    other = save(clean_project, 'W456')
    (folder / 'pages.json').write_text(json.dumps({'pages': ['AI 人工智能技术。']}))
    (other / 'pages.json').write_text(json.dumps({'pages': ['Other 人工智能技术。']}))
    library = Library(clean_project.papers)
    rows = library.lookup('Other 人工智能', papers=['W456'])
    assert len(rows) == 1 and rows[0][0] == other.name
    assert len(library.lookup('Other 人工智能')) == 2
    assert not library.lookup('不存在')
    assert not library.lookup('人工智能', raw=True)  # raw is deliberately SQLite FTS syntax
    (other / 'pages.json').write_text(json.dumps({'pages': ['替换文本。']}))
    assert not library.lookup('人工智能', papers=['W456'])
    assert library.lookup('替换', papers=['W456'])


def test_lookup_limit_is_consistent_between_commands(clean_project, capsys):
    folder = save(clean_project)
    (folder / 'pages.json').write_text(json.dumps({'pages': ['widgets'] * 125}))
    for command in [['lookup', 'widgets', '--limit', '125'], ['library', 'lookup', 'widgets', '--limit', '125']]:
        assert main(command) == 0
        assert len(capsys.readouterr().out.splitlines()) == 125


def test_check_never_opens_or_walks_knowledge(clean_project, monkeypatch):
    save(clean_project)
    protected = clean_project.research / 'knowledge'
    protected.mkdir()
    (protected / 'claims.md').write_text('literature/W123/README.md')
    outside = clean_project.root / 'draft.md'
    outside.write_text('literature/W123/README.md')
    original = Path.read_text
    def guarded(path, *args, **kwargs):
        assert not path.resolve().is_relative_to(protected), 'knowledge was opened'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', guarded)
    import lumi.library as module
    walk = module.os.walk
    def guarded_walk(*args, **kwargs):
        for directory, dirs, files in walk(*args, **kwargs):
            assert not Path(directory).resolve().is_relative_to(protected), 'knowledge was traversed'
            yield directory, dirs, files
    monkeypatch.setattr(module.os, 'walk', guarded_walk)
    library = Library(clean_project.papers)
    findings = library.check()
    assert [f.path for f in findings if f.code == 'stale-ref'] == ['draft.md:1']
    assert not any(f.code == 'missing-optional' for f in findings)
    assert not library._stale_refs(protected)


def test_new_context_has_no_session_end_mandate(tmp_path):
    assert main(['init', str(tmp_path / 'new')]) == 0
    context = (tmp_path / 'new' / 'CONTEXT.md').read_text()
    assert 'в конце сессии' not in context
    assert 'при изменении вопроса' in context


def test_lookup_chinese_excerpt_contains_late_match(clean_project):
    folder = save(clean_project)
    (folder / 'pages.json').write_text(json.dumps({'pages': ['开头。' * 200 + '人工智能技术。']}))
    rows = Library(clean_project.papers).lookup('人工智能')
    assert len(rows) == 1 and '[人工智能]' in rows[0][2] and len(rows[0][2]) < 250


def test_unpaywall_failure_is_not_no_open_copy(clean_project, monkeypatch, capsys):
    folder = save(clean_project)
    meta = json.loads((folder / 'metadata.json').read_text())
    meta['work']['doi'] = 'https://doi.org/10.1000/example'
    (folder / 'metadata.json').write_text(json.dumps(meta))
    monkeypatch.setenv('UNPAYWALL_EMAIL', 'reader@example.org')
    monkeypatch.setattr(sources, 'get_json', lambda *a: (_ for _ in ()).throw(OSError('API unavailable')))
    assert main(['add', 'W123']) == 1
    meta = json.loads((folder / 'metadata.json').read_text())
    assert meta['download_status'] == 'failed'
    assert 'API unavailable' in capsys.readouterr().out
    assert any('API unavailable' in error for error in meta['pdf_errors'])


@pytest.mark.parametrize('args', [['search', 'x'], ['citations', 'W123'], ['lookup', 'x'], ['library', 'lookup', 'x']])
def test_invalid_limit_is_rejected_before_io(clean_project, monkeypatch, args):
    monkeypatch.setattr(sources, 'get_json', lambda *a: (_ for _ in ()).throw(AssertionError('unexpected request')))
    assert main([*args, '--limit', '0']) == 2
    assert not list(clean_project.root.rglob('*.jsonl'))


def test_real_pdftotext_keeps_blank_page_positions(clean_project):
    """Exercise the actual Poppler boundary, not just a mocked page list."""
    import shutil
    if not shutil.which('pdftotext'):
        pytest.skip('pdftotext is a documented system dependency')
    folder = save(clean_project)
    stream = b'BT /F1 12 Tf 72 720 Td (Readable first page.) Tj ET\n'
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 6 0 R >> >> /Contents 7 0 R >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'endstream',
    ]
    pdf = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n'
    start = len(pdf)
    pdf += f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
    for offset in offsets[1:]:
        pdf += f'{offset:010d} 00000 n \n'.encode()
    pdf += f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()
    (folder / 'paper.pdf').write_bytes(pdf)
    assert main(['parse', 'W123']) == 1
    pages = json.loads((folder / 'pages.json').read_text())['pages']
    assert len(pages) == 3 and pages[0].strip() == 'Readable first page.'
    assert not pages[1].strip() and not pages[2].strip()
    assert json.loads((folder / 'metadata.json').read_text())['empty_pdf_pages'] == [2, 3]
