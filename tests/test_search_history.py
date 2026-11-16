"""Concurrent candidate state and append-only discovery provenance."""
import multiprocessing
import time

from engine import reports, sources
from engine.project import Project, append_jsonl


def add_rows(root, start, barrier):
    project = Project(root)
    barrier.wait()
    for number in range(start, start + 10):
        def update(rows):
            time.sleep(0.002)
            rows.append({"id": f"W{number}", "payload": "x" * 1000})
        project.update_candidates(update)
        append_jsonl(project.field / "search_log.jsonl", {"id": number, "payload": "中" * 10000})


def test_concurrent_updates_and_large_log_appends(project):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(4)
    processes = [context.Process(target=add_rows, args=(project.root, i * 10, barrier)) for i in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    assert len({row["id"] for row in project.candidates()}) == 40
    assert len({row["id"] for row in project.search_log()}) == 40
    assert all(len(row["payload"]) == 10000 for row in project.search_log())


def test_noop_keeps_candidate_file_mtime_and_old_records(project):
    legacy = {"id": "W900", "decision": "exclude", "custom": {"owner": True}}
    project.update_candidates(lambda rows: rows.append(legacy))
    path = project.field / "candidates.jsonl"
    before = path.stat().st_mtime_ns
    assert project.update_candidates(lambda rows: "unchanged") == "unchanged"
    assert path.stat().st_mtime_ns == before
    project.update_candidates(lambda rows: rows[0].update(legacy))
    assert path.stat().st_mtime_ns == before
    assert project.candidates() == [legacy]


def test_journal_retains_cross_provider_known_and_library_hits(project):
    first = [{"id": "W900", "doi": "https://doi.org/10.123/test", "title": "First"}]
    sources.record_search(project, "openalex", "original", first, "Q1")
    path = project.field / "candidates.jsonl"
    before = path.stat().st_mtime_ns
    repeat = [{"id": "s2:other", "doi": "10.123/test", "title": "Same work"},
              {"id": "W1", "doi": None, "title": "Library work"}]
    sources.record_search(project, "s2", "different query", repeat, "Q2")
    assert [row["status"] for row in repeat] == ["candidate", "library"]
    assert len(project.candidates()) == 1
    assert path.stat().st_mtime_ns == before
    event = project.search_log()[-1]
    assert event["query"] == "different query" and event["question"] == "Q2"
    assert event["db"] == "s2" and event["time"].endswith("+00:00")
    assert event["results"] == [{"id": row["id"], "doi": row["doi"], "status": row["status"]} for row in repeat]


def test_malformed_reference_does_not_hide_healthy_records(project, monkeypatch, capsys):
    monkeypatch.setattr(reports.papers, "all_meta", lambda p: {
        "W1": {"work": {"referenced_works": [f"W{i}" for i in range(100, 151)]}}
    })
    def fetch(ids):
        records = [{"id": identifier, "title": "Healthy"} for identifier in ids]
        return [{"display_name": "Broken"}] + records
    monkeypatch.setattr(sources, "fetch_works", fetch)
    assert reports.fetch_refs(project) == 1
    output = capsys.readouterr().out
    assert "повреждённая запись API" in output
    assert "51 загружено" in output
    assert "· W150" in output
    assert "сеть/API" not in output
