"""In-place support for literature/ + research/, without migrating user records."""
import json
from pathlib import Path

import pytest

from engine import index, papers, quotes, reports, sources
from engine.project import Project, find_root
from lumi.cli import main
from lumi.library import Library


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def existing(tmp_path, monkeypatch):
    root = tmp_path / 'research-project'
    library = root / 'literature'
    folder = library / 'example2020widgets-W123'
    folder.mkdir(parents=True)
    work = {'id': 'https://openalex.org/W123', 'doi': 'https://doi.org/10.1000/widgets',
            'display_name': 'Widgets in research', 'publication_year': 2020,
            'authorships': [{'author': {'display_name': 'Ada Example'}}],
            'referenced_works': ['https://openalex.org/W123', 'https://openalex.org/W999']}
    (folder / 'metadata.json').write_text(json.dumps({'work': work, 'text_source_pdf': 'paper.ocr.pdf', 'pdf_source': 'user original', 'version_note': 'exact edition'}))
    (folder / 'pages.json').write_text(json.dumps({'source': 'OCR of paper.ocr.pdf', 'pages': ['A verified statement about widgets and research.']}))
    (folder / 'paper.pdf').write_bytes(b'original sentinel')
    (folder / 'paper.ocr.pdf').write_bytes(b'OCR sentinel')
    (folder / 'paper.txt').write_text('verified text sentinel')
    (folder / 'README.md').write_text('# My card\n\n## Reading notes\nExact edition, p. 1.\n')
    (folder / 'notes_mark.md').write_text('owner notes')
    (root / 'research' / 'knowledge').mkdir(parents=True)
    (root / 'research' / 'knowledge' / 'claims.jsonl').write_text('{"id":"old-schema","unchanged":true}\n')
    (root / 'research' / 'knowledge' / 'edges.jsonl').write_text('old graph\n')
    (root / 'research' / 'index.sqlite').write_bytes(b'old index must remain untouched')
    (root / 'research' / 'questions.md').write_text('- RQ: What do widgets explain? | keywords: widgets\n')
    (root / 'research' / 'field').mkdir()
    (root / 'research' / 'field' / 'candidates.jsonl').write_text(json.dumps({'id':'W123','title':'Widgets','decision':None,'round':3})+'\n')
    (root / 'notes' / 'nested').mkdir(parents=True)
    monkeypatch.delenv('LUMI_PROJECT', raising=False)
    monkeypatch.delenv('EUREMA_PROJECT', raising=False)
    monkeypatch.chdir(root / 'notes' / 'nested')
    return Project(root), folder, work


def test_discovery_status_show_check_are_read_only(existing, capsys):
    project, folder, _ = existing
    before = snapshot(project.root)
    assert find_root() == project.root
    assert project.papers == project.root / 'literature'
    assert project.field == project.root / 'research' / 'field'
    assert project.questions()[0]['id'] == 'RQ'
    for ref in ('W123', folder.name, 'example2020widgets', '10.1000/widgets', 'Example 2020'):
        assert papers.resolve(project, ref) == folder.name
    assert papers.load_meta(project, folder.name)['key'] == 'example2020widgets'
    assert main(['status']) == 0
    assert 'статей 1 (полный текст 1)' in capsys.readouterr().out
    assert main(['show', 'W123', '--pdf-page', '1']) == 0
    assert main(['check', 'example2020widgets', 'A verified statement about widgets and research.', '--pdf-page', '1']) == 0
    assert main(['check', 'W123', 'A verified statement about widgets and research.', '--page', '99']) == 1
    assert main(['library', 'find', 'W123']) == 0
    with pytest.raises(SystemExit) as exc:
        main(['finish'])
    assert exc.value.code == 2
    assert snapshot(project.root) == before


def test_search_and_citations_use_existing_library(existing, monkeypatch):
    project, folder, work = existing
    monkeypatch.setitem(sources.DATABASES, 'openalex', lambda q, n: [{'id':'W123','title':'Widgets'}, {'id':'W456','title':'New'}])
    assert main(['search', 'widgets']) == 0
    assert [row['id'] for row in project.candidates()] == ['W123','W456']
    assert project.search_log()[-1]['new'] == 1
    calls = []
    monkeypatch.setattr(sources, 'citations', lambda p, ref, limit, question: calls.append(ref))
    assert main(['citations', folder.name]) == 0
    assert calls == [work['id']]
    assert not (project.root / 'field').exists()


def test_add_preserves_existing_versions_notes_and_deduplicates(existing, monkeypatch):
    project, folder, work = existing
    protected = {name: (folder/name).read_bytes() for name in ['README.md','notes_mark.md','paper.pdf','paper.ocr.pdf','paper.txt','pages.json']}
    monkeypatch.setattr(sources, 'fetch_work', lambda raw: (_ for _ in ()).throw(AssertionError('existing work must resolve locally')))
    assert papers.add(project, '10.1000/widgets', 'RQ') == folder.name
    assert papers.add(project, 'W123') == folder.name
    assert project.paper_ids() == [folder.name]
    assert {name:(folder/name).read_bytes() for name in protected} == protected
    meta = json.loads((folder/'metadata.json').read_text())
    assert meta['version_note'] == 'exact edition' and meta['pdf_source'] == 'user original'
    assert meta['text_source_pdf'] == 'paper.ocr.pdf'
    assert project.candidates()[0]['decision'] == 'include'
    assert project.candidates()[0]['round'] == 3
    assert (project.papers/'KEYS.tsv').exists() and (project.papers/'INDEX.md').exists()
    assert not (project.root/'sources').exists()


def test_new_add_and_library_save_share_location(existing, monkeypatch, tmp_path):
    project, folder, work = existing
    new = {**work, 'id':'https://openalex.org/W456', 'doi':'https://doi.org/10.1000/new', 'display_name':'Additional evidence'}
    monkeypatch.setattr(sources, 'fetch_work', lambda raw:new)
    monkeypatch.setattr(papers, 'pdf_candidates', lambda work:iter(()))
    name = papers.add(project, 'W456')
    assert name.endswith('-W456') and name != 'W456'
    assert Library(project.papers).find('W456').name == name
    payload = tmp_path/'work.json'
    payload.write_text(json.dumps({**new,'id':'https://openalex.org/W789','doi':'https://doi.org/10.1000/third'}))
    assert main(['library','save',str(payload)]) == 0
    assert len(project.paper_ids()) == 3
    assert not (Path.cwd()/'literature').exists()


def test_lookup_refreshes_after_external_library_change(existing):
    project, folder, _ = existing
    original_index = (project.research/'index.sqlite').read_bytes()
    assert index.lookup(project, 'widgets', refs=['10.1000/widgets'])
    assert Library(project.papers).lookup('widgets')
    (folder/'pages.json').write_text(json.dumps({'pages':['Replacement text about zyzzyva.']}))
    assert index.lookup(project, 'zyzzyva')
    assert not index.lookup(project, 'widgets')
    assert Library(project.papers).lookup('zyzzyva')
    assert not Library(project.papers).lookup('widgets')
    assert (project.research/'index.sqlite').read_bytes() == original_index
    assert (project.research/'knowledge'/'claims.jsonl').read_text() == '{"id":"old-schema","unchanged":true}\n'


def test_parse_library_path_uses_recorded_ocr_and_preserves_manual_pages(existing, monkeypatch):
    project, folder, _ = existing
    meta = json.loads((folder/'metadata.json').read_text())
    meta.update(page_labels=['91'], page_source='manual')
    (folder/'metadata.json').write_text(json.dumps(meta))
    calls=[]
    def extract(pdf):
        calls.append(pdf.name)
        return ['Verified OCR text. '*100], 'Verified OCR text. '*100
    monkeypatch.setattr('lumi.library.extract_pdf_text',extract)
    papers.parse(project,[folder.name])
    assert calls == ['paper.ocr.pdf']
    assert papers.load_meta(project,folder.name)['page_labels'] == ['91']
    monkeypatch.setattr('lumi.library.extract_pdf_text', lambda pdf: (calls.append(pdf.name) or ['Library OCR text'], 'Library OCR text'))
    assert main(['parse', 'W123', '--library', str(project.papers)]) == 0
    assert calls[-1] == 'paper.ocr.pdf'
    before=(folder/'pages.json').read_bytes()
    (folder/'paper.ocr.pdf').unlink()
    assert main(['parse', 'W123', '--library', str(project.papers)]) == 1
    assert (folder/'pages.json').read_bytes() == before
    assert main(['parse','W123']) == 1


def test_reference_filter_uses_folder_ids(existing, monkeypatch):
    project, folder, work = existing
    seen=[]
    monkeypatch.setattr(sources,'fetch_works',lambda ids: seen.extend(ids) or [])
    reports.fetch_refs(project,min_count=1)
    assert seen == ['W999']
    assert not (project.field/'FIELD.md').exists()
    assert not (project.root/'FIELD.md').exists()


def test_explicit_project_applies_to_library_commands(existing, monkeypatch, tmp_path):
    project, folder, _ = existing
    monkeypatch.chdir(tmp_path)
    assert main(['--project',str(project.root),'library','find','W123']) == 0
    assert not (tmp_path/'literature').exists()


def test_new_project_uses_current_layout_and_skill(tmp_path):
    root = tmp_path / 'new'
    assert main(['init', str(root), '--name', 'Example']) == 0
    assert (root / 'literature').is_dir()
    assert (root / 'research' / 'questions.md').is_file()
    assert (root / '.agents' / 'skills' / 'lumi-research' / 'SKILL.md').is_file()
    assert json.loads((root / 'lumi.json').read_text()) == {'name': 'Example'}
    assert not (root / 'sources').exists()
    assert not (root / 'knowledge').exists()
    before = snapshot(root)
    assert main(['init', str(root)]) == 2
    assert snapshot(root) == before


def test_shared_index_and_preserved_legacy_records(existing):
    import sqlite3
    project, folder, _ = existing
    before = snapshot(project.research)
    assert main(['lookup', 'widgets']) == 0
    assert main(['lookup', 'widgets', '--library', str(project.papers)]) == 0
    assert list(project.papers.rglob('*.sqlite')) == [project.papers / '.lumi' / 'index.sqlite']
    with sqlite3.connect(project.papers / '.lumi' / 'index.sqlite') as db:
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='claims'").fetchone()[0] == 0
        assert db.execute('SELECT paper FROM pages').fetchone()[0] == folder.name
    assert snapshot(project.research) == before


def test_parse_failure_preserves_text_and_manual_labels(existing, monkeypatch):
    project, folder, _ = existing
    meta = json.loads((folder / 'metadata.json').read_text())
    meta.update(page_source='manual', page_labels=['91'])
    (folder / 'metadata.json').write_text(json.dumps(meta))
    before = snapshot(folder)
    monkeypatch.setattr('lumi.library.extract_pdf_text', lambda pdf: (['new first', 'new second'], 'new first\fnew second'))
    assert main(['parse', 'W123']) == 1
    assert snapshot(folder) == before
    monkeypatch.setattr('lumi.library.extract_pdf_text', lambda pdf: ([''], ''))
    assert main(['parse', 'W123', '--library', str(project.papers)]) == 1
    assert snapshot(folder) == before


def test_retired_commands_reject_without_touching_project(existing):
    project, _, _ = existing
    before = snapshot(project.root)
    for command in ['claim', 'claims', 'belief', 'extract', 'reverify', 'attribute', 'annotate', 'audit', 'card', 'finish', 'map', 'lint', 'worldbank', 'orcid', 'patents']:
        with pytest.raises(SystemExit) as exc:
            main([command])
        assert exc.value.code == 2
    assert snapshot(project.root) == before


def test_old_storage_is_not_automatically_migrated(tmp_path):
    root = tmp_path / 'old'
    folder = root / 'sources' / 'papers' / 'W1'
    folder.mkdir(parents=True)
    (folder / 'metadata.json').write_text('{"work":{}}')
    (root / 'eurema.json').write_text('{"name":"Old"}')
    before = snapshot(root)
    assert main(['init', str(root)]) == 2
    assert main(['--project', str(root), 'status']) == 2
    assert snapshot(root) == before
