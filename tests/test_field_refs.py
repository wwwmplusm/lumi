"""Missing-reference discovery keeps scope explicit and failures observable."""
import json

from engine import reports, sources


def configure(monkeypatch, references):
    monkeypatch.setattr(reports.papers, "all_meta", lambda project: {
        str(i): {"work": {"referenced_works": refs}} for i, refs in enumerate(references)
    })
    monkeypatch.setattr(sources, "library_ids", lambda project, metas=None: {"W1"})


def record(identifier):
    return {"id": f"https://openalex.org/{identifier}", "display_name": f"Title {identifier}"}


def test_default_lists_every_reference_and_preserves_history(project, monkeypatch, capsys):
    ids = [f"W{i}" for i in range(100, 131)]
    configure(monkeypatch, [ids + ["W1"]])
    monkeypatch.setattr(sources, "fetch_works", lambda wanted: [record(w) for w in wanted])
    project.field.mkdir(parents=True, exist_ok=True)
    field = project.field / "FIELD.md"
    field.write_text("User's old report\n")
    cache = reports.refs_cache_path(project)
    cache.write_text(json.dumps({"W999": {"title": "Old cached record"}}))

    assert reports.fetch_refs(project) == 0
    output = capsys.readouterr().out
    assert sum(line.startswith("- ") for line in output.splitlines()) == 31
    assert all(f"· {w}\n" in output for w in ids)
    assert "≥1" in output
    assert field.read_text() == "User's old report\n"
    assert json.loads(cache.read_text())["W999"]["title"] == "Old cached record"


def test_explicit_threshold_counts_distinct_library_papers(project, monkeypatch, capsys):
    configure(monkeypatch, [["W100", "W100", "https://openalex.org/W100", "W101"], ["W100"]])
    monkeypatch.setattr(sources, "fetch_works", lambda wanted: [record(w) for w in wanted])
    assert reports.fetch_refs(project) == 0
    assert "· W101" in capsys.readouterr().out
    assert reports.fetch_refs(project, min_count=2) == 0
    output = capsys.readouterr().out
    assert "· W100" in output and "цитируют 2 наших" in output
    assert "W101" not in output


def test_incomplete_api_response_is_failure_with_visible_missing_ids(project, monkeypatch, capsys):
    configure(monkeypatch, [["W100", "W101"]])
    monkeypatch.setattr(sources, "fetch_works", lambda wanted: [record("W100")])
    assert reports.fetch_refs(project) == 1
    output = capsys.readouterr().out
    assert "1 загружено" in output
    assert "W101 — цитируют 1 наших; метаданные не получены" in output
    assert "Неполный результат" in output
    assert "W100" in json.loads(reports.refs_cache_path(project).read_text())


def test_later_request_failure_keeps_completed_batch_and_can_resume(project, monkeypatch, capsys):
    ids = [f"W{i}" for i in range(100, 151)]
    configure(monkeypatch, [ids])
    calls = []

    def fetch(wanted):
        calls.append(wanted)
        if len(calls) == 2:
            raise OSError("API unavailable")
        return [record(w) for w in wanted]

    monkeypatch.setattr(sources, "fetch_works", fetch)
    assert reports.fetch_refs(project) == 1
    output = capsys.readouterr().out
    assert "50 загружено" in output and "API unavailable" in output
    assert len(json.loads(reports.refs_cache_path(project).read_text())) == 50
    assert "W150 — цитируют 1 наших; метаданные не получены" in output
    assert reports.fetch_refs(project) == 0
    assert calls[-1] == ["W150"]
