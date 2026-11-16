"""Provider contracts and complete search -> catalog flows, without live API calls."""
import io
import json
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

from engine import papers, sources
from engine.project import Project
from lumi.cli import main
from lumi.library import Library
from lumi.naming import openalex_id_of, parse_folder_name


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    (root / 'literature').mkdir(parents=True)
    (root / 'research').mkdir()
    monkeypatch.chdir(root)
    monkeypatch.delenv('LUMI_PROJECT', raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(sources.time, 'sleep', lambda seconds: None)
    return Project(root)


def oa(i):
    return {'id': f'https://openalex.org/W{i}', 'display_name': f'Paper {i}', 'cited_by_count': i}


def test_search_obeys_requested_limit_and_paginates(project, monkeypatch, capsys):
    calls = []
    def get(url, headers=None):
        params = parse_qs(urlsplit(url).query)
        size = int(params['per_page'][0])
        start = int(params['cursor'][0]) if params['cursor'][0] != '*' else 0
        calls.append((start, size))
        return {'results': [oa(i) for i in range(start, start + size)], 'meta': {'next_cursor': str(start + size)}}
    monkeypatch.setattr(sources, 'get_json', get)
    assert main(['search', 'widgets', '--limit', '250']) == 0
    assert calls == [(0, 100), (100, 100), (200, 50)]
    assert len(project.candidates()) == 250
    log = project.search_log()[-1]
    assert log['requested'] == 250 and log['requests'] == 3 and log['stop'] == 'limit'
    assert 'достигнут заданный --limit' in capsys.readouterr().out


def test_search_failure_keeps_first_page_and_reports_partial(project, monkeypatch, capsys):
    monkeypatch.setenv('OPENALEX_API_KEY', 'secret-token')
    def get(url, headers=None):
        cursor = parse_qs(urlsplit(url).query)['cursor'][0]
        if cursor == '*':
            return {'results': [oa(i) for i in range(100)], 'meta': {'next_cursor': 'next'}}
        raise urllib.error.URLError('quota error secret-token')
    monkeypatch.setattr(sources, 'get_json', get)
    assert main(['search', 'widgets', '--limit', '150']) == 1
    assert len(project.candidates()) == 100
    log = project.search_log()[-1]
    assert log['stop'] == 'partial' and log['found'] == 100
    assert 'quota error' in log['error'] and 'secret-token' not in log['error']
    output = capsys.readouterr().out
    assert 'Неполный результат' in output and 'secret-token' not in output


def test_first_page_failure_is_not_empty_success(project, monkeypatch):
    monkeypatch.setattr(sources, 'get_json', lambda *a: (_ for _ in ()).throw(OSError('offline')))
    assert main(['search', 'widgets']) == 1
    assert project.candidates() == []
    assert project.search_log()[-1]['stop'] == 'partial'


def test_empty_api_result_is_successful_exhaustion(project, monkeypatch, capsys):
    monkeypatch.setattr(sources, 'get_json', lambda *a: {'results': [], 'meta': {'next_cursor': None}})
    assert main(['search', 'widgets']) == 0
    assert project.search_log()[-1]['stop'] == 'exhausted'
    assert 'выдача API исчерпана' in capsys.readouterr().out


@pytest.mark.parametrize('same_cursor', [True, False])
def test_repeated_pages_terminate_with_partial_result(same_cursor):
    calls = []
    def fetch(cursor, size):
        calls.append(cursor)
        return [{'id': 'W1'}], 'same' if same_cursor else len(calls)
    rows = sources.collect_pages(fetch, 20)
    assert len(rows) == 1 and rows.stop == 'partial'
    assert len(calls) <= 2


def test_citations_fetches_all_references_including_after_200(project, monkeypatch, capsys):
    work = {**oa(999), 'referenced_works': [f'https://openalex.org/W{i}' for i in range(251)]}
    monkeypatch.setattr(sources, 'fetch_work', lambda raw: work)
    seen = []
    def get(url, headers=None):
        params = parse_qs(urlsplit(url).query)
        filter_ = params['filter'][0]
        if filter_.startswith('cites:'):
            return {'results': [], 'meta': {'next_cursor': None}}
        ids = filter_.removeprefix('openalex:').split('|')
        seen.extend(ids)
        assert len(ids) <= 50
        return {'results': [oa(int(w[1:])) for w in ids]}
    monkeypatch.setattr(sources, 'get_json', get)
    assert main(['citations', 'W999', '--limit', '250']) == 0
    assert len(seen) == 251 and 'W250' in seen
    assert len(project.candidates()) == 250
    assert project.candidates()[0]['id'] == 'W250'
    assert 'получено 251, показаны 250' in capsys.readouterr().out


def test_crossref_cursor_and_remaining_page_size(monkeypatch):
    calls = []
    def get(url, headers=None):
        params = parse_qs(urlsplit(url).query)
        cursor, size = params['cursor'][0], int(params['rows'][0])
        start = 0 if cursor == '*' else 100
        calls.append((cursor, size))
        return {'message': {'items': [{'DOI': f'10.1/{i}', 'title': [f'Title {i}']} for i in range(start, start+size)], 'next-cursor': 'second'}}
    monkeypatch.setattr(sources, 'get_json', get)
    rows = sources.crossref('query', 125)
    assert len(rows) == 125 and not rows.error
    assert calls == [('*', 100), ('second', 25)]


def test_core_offsets_and_native_metadata(monkeypatch):
    monkeypatch.setenv('CORE_API_KEY', 'test')
    calls = []
    def get(url, headers=None):
        params = parse_qs(urlsplit(url).query)
        offset, size = int(params['offset'][0]), int(params['limit'][0])
        calls.append((offset, size))
        assert headers == {'Authorization': 'Bearer test'}
        return {'results': [{'id': i, 'title': f'Title {i}', 'authors': [{'name': 'Ada Example'}], 'downloadUrl': f'https://example.org/{i}.pdf'} for i in range(offset, offset+size)], 'totalHits': 125}
    monkeypatch.setattr(sources, 'get_json', get)
    rows = sources.core('query', 125)
    assert len(rows) == 125 and not rows.error
    assert calls == [(0, 100), (100, 25)]
    assert rows[0]['work']['best_oa_location']['pdf_url'] == 'https://example.org/0.pdf'
    assert rows[0]['work']['authorships'][0]['author']['display_name'] == 'Ada Example'


def test_s2_provider_limit_is_explicit(monkeypatch):
    calls = []
    def get(url, headers=None):
        params = parse_qs(urlsplit(url).query)
        offset, size = int(params['offset'][0]), int(params['limit'][0])
        calls.append(offset)
        return {'data': [{'paperId': f'{i:040x}', 'title': f'Title {i}'} for i in range(offset, offset+size)], 'total': 2000}
    monkeypatch.setattr(sources, 'get_json', get)
    rows = sources.semantic_scholar('query', 1200)
    assert len(rows) == 1000 and rows.stop == 'partial'
    assert '1000' in rows.error and len(calls) == 10


def test_lens_scroll_supports_stable_scroll_id(monkeypatch):
    monkeypatch.setenv('LENS_TOKEN', 'test')
    calls = []
    def request(body):
        calls.append(body)
        if len(calls) == 1:
            return {'data': [{'lens_id': str(i), 'title': f'Title {i}'} for i in range(100)], 'scroll_id': 'stable'}
        if len(calls) == 2:
            return {'data': [{'lens_id': str(i), 'title': f'Title {i}'} for i in range(100, 200)], 'scroll_id': 'stable'}
        return {}  # HTTP 204, exhausted
    monkeypatch.setattr(sources, 'lens_request', request)
    rows = sources.lens('query', 250)
    assert len(rows) == 200 and rows.stop == 'exhausted'
    assert calls[1:] == [{'scroll_id': 'stable', 'scroll': '1m'}] * 2


def test_lens_204_is_not_json_error(monkeypatch):
    monkeypatch.setenv('LENS_TOKEN', 'test')
    response = io.BytesIO(b'')
    response.status = 204
    monkeypatch.setattr(sources.urllib.request, 'urlopen', lambda *a, **k: response)
    assert sources.lens_request({'scroll_id': 'last'}) == {}


def test_arxiv_paginates_using_start(project, monkeypatch):
    starts = []
    def get(request, **kwargs):
        params = parse_qs(urlsplit(request.full_url).query)
        start, size = int(params['start'][0]), int(params['max_results'][0])
        starts.append(start)
        entries = ''.join(f'<entry><id>http://arxiv.org/abs/2401.{i:05d}v1</id><title>Paper {i}</title><published>2024-01-01</published></entry>' for i in range(start, start+size))
        return io.BytesIO(f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode())
    monkeypatch.setattr(sources.urllib.request, 'urlopen', get)
    rows = sources.arxiv('all:widgets', 125)
    assert len(rows) == 125 and not rows.error and starts == [0, 100]


@pytest.mark.parametrize('provider,item', [
    ('core', {'id': 123, 'title': 'Evidence without a DOI', 'yearPublished': 2020, 'authors': [{'name': 'Ada Example'}]}),
    ('s2', {'paperId': 'a'*40, 'title': 'Evidence without a DOI', 'year': 2020, 'authors': [{'name': 'Ada Example'}]}),
    ('lens', {'lens_id': '123-456-789', 'title': 'Evidence without a DOI', 'year_published': 2020, 'authors': [{'first_name': 'Ada', 'last_name': 'Example'}]}),
])
def test_native_search_add_readd_without_openalex(project, monkeypatch, provider, item):
    row = getattr(sources, provider + '_row')(item)
    monkeypatch.setitem(sources.DATABASES, provider, lambda query, limit: [row.copy()])
    assert main(['search', 'evidence', '--db', provider]) == 0
    monkeypatch.setattr(sources, 'fetch_work', lambda raw: (_ for _ in ()).throw(AssertionError('must use stored native metadata')))
    monkeypatch.setattr(papers, 'pdf_candidates', lambda work: iter(()))
    # Metadata saved successfully, but no full text -> explicit partial result.
    assert main(['add', row['id']]) == 1
    library = Library(project.papers)
    folder = library.find(row['id'])
    assert parse_folder_name(folder.name).kind == 'provider'
    meta = json.loads((folder / 'metadata.json').read_text())
    assert meta['work']['provider_record'] == item
    assert meta['work']['authorships'][0]['author']['display_name'] == 'Ada Example'
    assert meta['openalex_id'] == ''
    (folder / 'README.md').write_text('Personal card')
    (folder / 'notes-private.md').write_text('Personal notes')
    assert main(['add', row['id']]) == 1
    assert len(project.paper_ids()) == 1
    assert (folder / 'README.md').read_text() == 'Personal card'
    assert (folder / 'notes-private.md').read_text() == 'Personal notes'
    found = sources.search(project, provider, 'evidence', 10)
    assert found[0]['status'] == 'library'
    assert project.candidates()[0]['decision'] == 'include'


@pytest.mark.parametrize('native,response', [
    ('core:123', {'id': 123, 'title': 'Direct CORE result'}),
    ('a'*40, {'paperId': 'a'*40, 'title': 'Direct S2 result'}),
    ('lens:123-456', {'data': [{'lens_id': '123-456', 'title': 'Direct Lens result'}]}),
])
def test_native_ids_fetch_directly_without_candidate(monkeypatch, native, response):
    monkeypatch.setenv('CORE_API_KEY', 'test')
    monkeypatch.setenv('LENS_TOKEN', 'test')
    urls = []
    def get(url, headers=None):
        urls.append(url)
        return response
    monkeypatch.setattr(sources, 'get_json', get)
    monkeypatch.setattr(sources, 'lens_request', lambda body: response)
    work = sources.fetch_work(native)
    assert work['id'] == sources.provider_id(native)
    assert not any('openalex' in url for url in urls)


def test_doi_duplicates_inside_same_batch_are_one_candidate(project):
    rows = sources.record_search(project, 'test', 'query', [
        {'id': 'core:1', 'doi': 'https://doi.org/10.1/example', 'title': 'Title'},
        {'id': 's2:other', 'doi': '10.1/EXAMPLE', 'title': 'Title'},
    ])
    assert [r['status'] for r in rows] == ['new', 'candidate']
    assert len(project.candidates()) == 1


def test_provider_id_containing_w_digits_is_not_openalex():
    assert openalex_id_of({'id': 'lens:W123'}) is None


def test_legacy_s2_candidate_resolves_and_is_marked(project, monkeypatch):
    raw = 'a' * 40
    item = {'paperId': raw, 'title': 'Old candidate'}
    project.update_candidates(lambda rows: rows.append({'id': raw, 'title': 'Old candidate', 'decision': None}))
    monkeypatch.setattr(sources, 'get_json', lambda *a: item)
    monkeypatch.setattr(papers, 'pdf_candidates', lambda work: iter(()))
    assert main(['add', raw]) == 1
    assert project.candidates()[0]['decision'] == 'include'
    sources.record_search(project, 's2', 'again', [sources.s2_row(item)])
    assert len(project.candidates()) == 1
