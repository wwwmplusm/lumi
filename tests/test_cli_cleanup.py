"""Init preserves owner files; local CLI operations do not depend on API settings."""
import json
from pathlib import Path
import stat

import pytest

from engine import onboarding
from lumi.cli import main
from lumi.library import Library


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.delenv('LUMI_PROJECT', raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_init_context_has_one_source_and_no_wrapper(isolated):
    root = isolated / 'project'
    context = '研究人工智能。\nСравнить лаборатории и университеты.'
    assert main(['init', str(root), '--name', 'Research', '--context', context]) == 0
    assert json.loads((root / 'lumi.json').read_text()) == {'name': 'Research'}
    assert f'## Тема\n{context}\n' in (root / 'CONTEXT.md').read_text()
    assert '{{CONTEXT}}' not in (root / 'CONTEXT.md').read_text()
    assert not (root / 'lumi').exists()
    assert './lumi' not in (root / 'AGENTS.md').read_text()


def test_init_preserves_existing_owner_files_and_permissions(isolated):
    root = isolated / 'project'
    root.mkdir()
    for name in ('lumi', 'CONTEXT.md', 'AGENTS.md', 'CLAUDE.md', '.gitignore'):
        (root / name).write_text(f'Owner content: {name}\n')
    (root / 'lumi').chmod(0o640)
    before = {p.name: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in root.iterdir()}
    assert main(['init', str(root), '--context', 'New context']) == 0
    assert {name: ((root / name).read_bytes(), stat.S_IMODE((root / name).stat().st_mode))
            for name in before} == before


@pytest.mark.parametrize('command,expected', [
    (['status'], 0), (['field', 'candidates'], 0), (['parse'], 0),
    (['lookup', 'evidence'], 0), (['show', 'W123', '--pdf-page', '1'], 0),
    (['pages', 'W123'], 0), (['check', 'W123', 'Useful evidence from laboratory research.', '--pdf-page', '1'], 0),
    (['library', 'find', 'W123'], 0), (['library', 'index'], 0),
    (['library', 'normalize'], 0),
])
def test_local_commands_ignore_broken_credentials(isolated, command, expected):
    root = isolated / 'project'
    assert main(['init', str(root)]) == 0
    folder = Library(root / 'literature').save_work({
        'id': 'https://openalex.org/W123', 'display_name': 'Useful evidence',
        'publication_year': 2020, 'authorships': [{'author': {'display_name': 'Ada Example'}}],
    })
    (root / 'literature' / folder / 'pages.json').write_text(json.dumps({'pages': ['Useful evidence from laboratory research.']}))
    path = onboarding.config_path()
    path.parent.mkdir(parents=True)
    path.write_text('{broken credentials')
    assert main(['--project', str(root), *command]) == expected
    assert path.read_text() == '{broken credentials'


def test_init_and_readonly_check_ignore_broken_credentials(isolated):
    path = onboarding.config_path()
    path.parent.mkdir(parents=True)
    path.write_text('{broken credentials')
    root = isolated / 'project'
    assert main(['init', str(root)]) == 0
    assert main(['library', 'check', '--library', str(root / 'literature')]) == 0


@pytest.mark.parametrize('command', [
    ['search', 'evidence'], ['citations', 'W123'], ['add', 'W123'],
    ['field', 'refs'], ['doctor'], ['setup', '--non-interactive'],
])
def test_api_and_configuration_commands_still_validate_credentials(isolated, command, capsys):
    path = onboarding.config_path()
    path.parent.mkdir(parents=True)
    path.write_text('{broken credentials')
    assert main(command) == 2
    assert 'Повреждён файл настроек' in capsys.readouterr().err


def test_decide_updates_candidate_in_transaction_and_preserves_discovery(isolated):
    from engine.project import Project
    root = isolated / 'project'
    assert main(['init', str(root)]) == 0
    project = Project(root)
    rows = [{'id': 'W123', 'doi': '10.123/example', 'discoveries': [{'db': 'crossref'}]},
            {'id': 'W456', 'title': 'Another source'}]
    project.update_candidates(lambda current: current.extend(rows))
    assert main(['--project', str(root), 'field', 'decide', '10.123/example', 'include', '--why', 'Relevant']) == 0
    saved = project.candidates()
    assert saved[0]['decision'] == 'include' and saved[0]['why'] == 'Relevant'
    assert saved[0]['discoveries'] == rows[0]['discoveries']
    assert saved[1] == rows[1]
    before = (project.field / 'candidates.jsonl').read_bytes()
    assert main(['--project', str(root), 'field', 'decide', 'missing', 'exclude']) == 2
    assert (project.field / 'candidates.jsonl').read_bytes() == before


@pytest.mark.parametrize('problem', ['corrupt', 'ambiguous'])
def test_invalid_local_citation_never_falls_back_to_network(isolated, monkeypatch, problem):
    from engine import sources
    root = isolated / 'project'
    assert main(['init', str(root)]) == 0
    library = Library(root / 'literature')
    work = {'id': 'https://openalex.org/W123', 'display_name': 'First study',
            'publication_year': 2020, 'authorships': [{'author': {'display_name': 'Ada Example'}}]}
    folder = library.save_work(work)
    if problem == 'corrupt':
        (library.root / folder / 'metadata.json').write_text('{broken metadata')
        ref = 'W123'
    else:
        second = library.save_work({**work, 'id': 'https://openalex.org/W456', 'display_name': 'Second study'})
        metadata = library.read_meta(library.root / second)
        metadata['key'] = library.read_meta(library.root / folder)['key']
        (library.root / second / 'metadata.json').write_text(json.dumps(metadata))
        ref = 'Example 2020'
    def unexpected_network(*args, **kwargs):
        pytest.fail('invalid local reference fell back to network')
    monkeypatch.setattr(sources, 'citations', unexpected_network)
    assert main(['--project', str(root), 'citations', ref]) == 2


@pytest.mark.parametrize('tty', [True, False])
def test_empty_invocation_always_shows_help(isolated, monkeypatch, capsys, tty):
    monkeypatch.setattr(onboarding.sys.stdin, 'isatty', lambda: tty)
    def unexpected(*args, **kwargs):
        pytest.fail('empty invocation must not configure credentials')
    monkeypatch.setattr(onboarding, 'setup', unexpected)
    monkeypatch.setattr(onboarding, 'load_credentials', unexpected)
    assert main([]) == 0
    assert 'usage: lumi' in capsys.readouterr().out
    assert not onboarding.config_path().exists()


def test_pages_only_displays_or_sets_numbering(isolated, monkeypatch, capsys):
    from engine import papers
    root = isolated / 'project'
    library = Library(root / 'literature')
    folder = library.root / library.save_work({
        'id': 'https://openalex.org/W123', 'display_name': 'Useful evidence',
        'publication_year': 2020, 'authorships': [{'author': {'display_name': 'Ada Example'}}],
    })
    pages = folder / 'pages.json'
    pages.write_text(json.dumps({'pages': ['First page', 'Second page']}))
    before = pages.read_bytes()
    def unexpected(*args, **kwargs):
        pytest.fail('pages must not extract text')
    monkeypatch.setattr(papers, 'parse', unexpected)
    monkeypatch.setattr(Library, 'parse', unexpected)
    args = ['--project', str(root), 'pages', 'W123']
    assert main(args) == 0
    assert main([*args, '--first', '91']) == 0
    assert library.read_meta(folder)['page_labels'] == ['91', '92']
    assert pages.read_bytes() == before
    with pytest.raises(SystemExit) as error:
        main([*args, '--detect'])
    assert error.value.code == 2
    assert 'unrecognized arguments: --detect' in capsys.readouterr().err
