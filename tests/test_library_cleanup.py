"""Library resolution and write safety regressions."""

import json
from unittest.mock import Mock

import pytest

from engine import index, papers
from engine.project import Project
from lumi.library import Library


def work(identifier='W123', title='Research methods'):
    return {'id': f'https://openalex.org/{identifier}', 'display_name': title,
            'publication_year': 2026, 'doi': f'https://doi.org/10.1234/{identifier}',
            'authorships': [{'author': {'display_name': 'Jane Smith'}}]}


@pytest.mark.parametrize('invalid', ['{broken', '[]'])
def test_corrupt_metadata_blocks_catalog_and_add(tmp_path, monkeypatch, invalid):
    project = Project(tmp_path)
    library = Library(project.papers)
    folder = library.save_work(work())
    before = {name: (library.root / name).read_bytes() for name in ('KEYS.tsv', 'INDEX.md')}
    (library.root / folder / 'metadata.json').write_text(invalid)
    fetch = Mock(side_effect=AssertionError('must not fetch after corrupt metadata'))
    monkeypatch.setattr(papers.sources, 'fetch_work', fetch)
    for action in (library.load_works, library.write_catalog,
                   lambda: library.match_existing(work()), lambda: papers.add(project, 'W123')):
        with pytest.raises(ValueError, match='metadata.json'):
            action()
    assert all((library.root / name).read_bytes() == data for name, data in before.items())
    assert any(f.code == 'metadata' and folder in f.path for f in library.check())
    fetch.assert_not_called()


def test_common_aliases_and_standalone_lookup(tmp_path, capsys):
    library = Library(tmp_path / 'literature')
    folder = library.save_work(work())
    meta = library.read_meta(library.root / folder)
    library.write_text(library.root / folder / 'pages.json', json.dumps({'pages': ['research methods text']}))
    for ref in (folder, 'W123', 'https://openalex.org/W123', meta['key'],
                '10.1234/W123', 'https://doi.org/10.1234/W123', 'Smith 2026', 'smith  2026'):
        assert library.find(ref).name == folder
        assert papers.resolve(library, ref) == folder
        assert index.lookup(library, 'research', refs=[ref])[0][0] == folder
    assert 'Smith 2026' in capsys.readouterr().out
    library.save_work(work('W456', 'Other research'))
    with pytest.raises(ValueError, match='неоднозначная'):
        library.find('Smith 2026')


def test_repeat_add_keeps_index_and_catalog_written_once(tmp_path, monkeypatch):
    project = Project(tmp_path)
    library = Library(project.papers)
    folder = library.save_work(work())
    library.write_text(library.root / folder / 'pages.json', json.dumps({'pages': ['research methods']}))
    library.lookup('research')
    rebuild = Mock(side_effect=AssertionError('unchanged pages must keep index'))
    monkeypatch.setattr(Library, 'rebuild_index', rebuild)
    catalog = Library.write_catalog
    calls = []
    def counted(self):
        calls.append(self.root)
        return catalog(self)
    monkeypatch.setattr(Library, 'write_catalog', counted)
    monkeypatch.setattr(papers, 'pdf_candidates', lambda _: iter(()))
    assert papers.add(project, 'W123') == folder
    assert len(calls) == 1
    assert library.lookup('research')[0][0] == folder
    rebuild.assert_not_called()


def test_unreadable_metadata_has_path_and_check_keeps_other_works(tmp_path, monkeypatch):
    from pathlib import Path
    library = Library(tmp_path / 'literature')
    bad = library.save_work(work())
    good = library.save_work(work('W456', 'Other research'))
    read_text = Path.read_text
    def denied(self, *args, **kwargs):
        if self == library.root / bad / 'metadata.json':
            raise PermissionError('permission denied')
        return read_text(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', denied)
    with pytest.raises(ValueError, match=f'{bad}/metadata.json.*permission denied'):
        library.load_works()
    findings = library.check()
    assert any(f.code == 'metadata' and bad in f.path for f in findings)
    assert not any(f.code == 'metadata' and good in f.path for f in findings)


def test_new_add_refreshes_catalog_once(tmp_path, monkeypatch):
    project = Project(tmp_path)
    monkeypatch.setattr(papers.sources, 'fetch_work', lambda _: work())
    monkeypatch.setattr(papers, 'pdf_candidates', lambda _: iter(()))
    calls = []
    catalog = Library.write_catalog
    def counted(self):
        calls.append(self.root)
        return catalog(self)
    monkeypatch.setattr(Library, 'write_catalog', counted)
    folder = papers.add(project, 'W123')
    assert (project.papers / folder / 'metadata.json').exists()
    assert len(calls) == 1


@pytest.mark.parametrize('invalid', ['{broken', '[]', '{"work": []}', '{"work": null}'])
def test_metadata_readers_share_validation(tmp_path, invalid):
    from engine import sources
    project = Project(tmp_path)
    library = Library(project.papers)
    folder = library.root / library.save_work(work())
    (folder / 'metadata.json').write_text(invalid)
    readers = [lambda: library.read_meta(folder),
               lambda: papers.load_meta(project, folder.name),
               lambda: sources.library_snapshot(project),
               lambda: papers.selected_pdf(folder)]
    errors = []
    for read in readers:
        with pytest.raises(ValueError, match='metadata.json') as error:
            read()
        errors.append(str(error.value))
    assert len(set(errors)) == 1


def test_metadata_reading_is_independent_of_text_status(tmp_path, capsys):
    from engine import reports, sources
    project = Project(tmp_path)
    library = Library(project.papers)
    folder = library.root / library.save_work(work())
    metadata = folder / 'metadata.json'
    stored = json.loads(metadata.read_text())
    stored.pop('key')
    stored['fulltext'] = 'yes'  # Old cached status must not override the page files.
    metadata.write_text(json.dumps(stored))
    before = metadata.read_bytes()
    (folder / 'pages.json').write_text('{broken pages')
    meta = library.read_meta(folder)
    assert meta == papers.load_meta(project, folder.name)
    assert sources.library_snapshot(project) == {folder.name: meta}
    assert meta['key'] and meta['page_source'] == 'none'
    (folder / 'pages.json').write_text(json.dumps({'pages': ['Some text', '']}))
    assert papers.fulltext_status(project, folder.name, meta) == 'partial'
    reports.status(project)
    output = capsys.readouterr().out
    assert 'полный текст 0' in output and 'Неполный текст: 1' in output
    assert metadata.read_bytes() == before


@pytest.mark.parametrize('suffix', ['doi_10.1234_example', 'arxiv_2401.09999', 'local_12345678'])
@pytest.mark.parametrize('id_location', ['work', 'metadata'])
def test_normalize_preserves_existing_ids_when_openalex_appears(tmp_path, suffix, id_location):
    from engine import sources
    library = Library(tmp_path / 'literature')
    original = library.root / library.save_work(work())
    folder = original.with_name(f'smith2026research-{suffix}')
    original.rename(folder)
    meta = json.loads((folder / 'metadata.json').read_text())
    meta.update(folder=folder.name, key='smith2026research')
    if id_location == 'work':
        meta.pop('openalex_id')
    else:
        meta['work'].pop('id')
    (folder / 'metadata.json').write_text(json.dumps(meta))
    (folder / 'notes-owner.md').write_text('Keep this note and its path.')
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    assert not any(a.kind == 'rename' for a in library.plan())
    library.apply()
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    assert library.find('W123') == folder
    assert library.find('https://openalex.org/W123') == folder
    assert 'W123' in sources.library_ids(Project(tmp_path))
    assert not any(f.code == 'openalex-not-in-folder' for f in library.check())
    assert library.plan() == []
