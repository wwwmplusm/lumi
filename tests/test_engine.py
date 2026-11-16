import json
import subprocess

import pytest

from engine import index, papers, quotes, reports, sources
from engine.text import normalize, normalize_map


def test_normalize_map_points_back_to_original():
    original = "la-\n  boratories “quoted” ﬁne"
    text, pos = normalize_map(original)
    assert text == 'laboratories "quoted" fine'
    assert original[pos[text.index("quoted")]] == "q"


def test_ladder_found_and_page_break(project):
    pages = papers.load_pages(project, "W1")
    r = quotes.locate_in(pages, "reduced GPU access for Chinese laboratories", hint=1)
    assert r["verdict"] == "FOUND" and r["page"] == 2 and "la-\nboratories" in r["found"]
    r = quotes.locate_in(["the effect was large and", "persistent across all provinces"], "the effect was large and persistent across")
    assert r["verdict"] == "FOUND" and r["page"] == 1


def test_ladder_halves_segments_drift_absent_short(project):
    pages = ["Results show that the policy reduced output 12 in compute-intensive fields by a third."]
    assert quotes.locate_in(pages, "the policy reduced output in compute-intensive fields by a third")["verdict"] == "FOUND*"
    pages = ["Results show that the policy reduced output in rural areas in compute-intensive fields by a third."]
    assert quotes.locate_in(pages, "the policy reduced output in compute-intensive fields by a third")["verdict"] != "FOUND*"
    pages = ["Results show that the policy reduced output", "Journal of Tests 12 (2024) 113\nin compute-intensive fields by a third."]
    r = quotes.locate_in(pages, "the policy reduced output in compute-intensive fields by a third")
    assert r["verdict"] == "FOUND*" and r["page"] == 1
    pages = papers.load_pages(project, "W1")
    assert quotes.locate_in(pages, "Chinese labs shifted toward … quantization")["verdict"] == "SEGMENTS"
    assert quotes.locate_in(pages, "Chinese labs shifted toward … nonexistent words here")["verdict"] != "SEGMENTS"
    r = quotes.locate_in(pages, "Chinese labs shifted towards efficient training method such as quantisation")
    assert r["verdict"] == "DRIFT" and "quantization" in r["found"]
    assert quotes.locate_in(pages, "This sentence is not in the paper at all, not even close")["verdict"] == "ABSENT"
    assert quotes.locate_in(pages, "too short")["verdict"] == "SHORT"


def test_check_text_parses_citations_and_pages(project):
    text = ("Как пишут авторы, «reduced GPU access for Chinese laboratories» [Gao 2024, с. 102]. "
            "Не та страница: «Chinese labs shifted toward efficient training methods» [Gao и др. 2024, с. 101]. "
            "Чужая статья: «Chinese AI laboratories had less access to GPUs» [Gao 2024, с. 102]. "
            "Вторая статья без печатных номеров: «Chinese AI laboratories had less access to GPUs» [Gao 2024b, PDF-с. 2]. "
            "Неизвестная: «some long enough quote for the check» [Smith 2020, с. 4].")
    results = quotes.check_text(project, text)
    verdicts = [(r["verdict"], quotes.ok(r)) for r in results]
    assert verdicts == [("FOUND", True), ("FOUND", False), ("ABSENT", False), ("FOUND", True), ("BAD_CITE", False)]
    assert results[1]["page_wrong"]
    assert "Gao 2024b" in results[2]["note"]
    assert "страница не та" in quotes.report(project, results[1])


def test_keys_labels_and_resolve(project):
    meta = papers.load_meta(project, "W1")
    assert papers.label(meta) == "Gao и др. 2024 «Export controls and Chinese AI labs…»"
    assert papers.cite_name(papers.load_meta(project, "W2")) == "Gao 2024b"
    assert papers.resolve(project, "gao2024b") == "W2" and papers.resolve(project, "W1") == "W1"
    assert papers.page_ref(meta, 2) == "с. 102" and papers.page_ref(papers.load_meta(project, "W2"), 2) == "PDF-с. 2"
    with pytest.raises(ValueError):
        papers.resolve(project, "Smith2020")


def test_page_detection():
    layout = ["Header\ntext\n15", "16\ntext", "text\n17", "Vol 3 2024\ntext\n18"]
    assert papers.header_offset(layout) == 14
    assert papers.header_offset(["a\nb", "c\nd", "e\nf"]) is None
    meta = {"work": {"biblio": {"first_page": "884", "last_page": "886"}, "doi": "10.1/x"}}
    papers.detect_pages(meta, None, ["a", "b", "c"])
    assert meta["page_source"] == "metadata" and meta["page_labels"] == ["884", "885", "886"]
    meta = {"work": {"biblio": {"first_page": "1456978", "last_page": "1456978"}, "doi": "https://doi.org/10.48550/arxiv.1"}}
    papers.detect_pages(meta, None, ["a", "b", "c"])
    assert meta["page_source"] == "preprint"
    assert papers.roman(14) == "xiv"


def test_qpdf_labels(monkeypatch):
    class R:
        stdout = json.dumps({"pagelabels": [{"index": 0, "label": {"/S": "/r"}}, {"index": 2, "label": {"/S": "/D", "/St": 5}}]})
    monkeypatch.setattr(papers.shutil, "which", lambda x: "/usr/bin/qpdf")
    monkeypatch.setattr(papers.subprocess, "run", lambda *a, **k: R)
    assert papers.qpdf_labels("x.pdf", 4) == ["i", "ii", "5", "6"]
    R.stdout = json.dumps({"pagelabels": [{"index": 0, "label": {"/S": "/D"}}]})
    assert papers.qpdf_labels("x.pdf", 3) is None


def test_index_lookup_and_show(project, capsys):
    index.build(project)
    rows = index.lookup(project, "export control GPUs", 5)
    assert rows and rows[0][0] in ("W1", "W2")
    out = capsys.readouterr().out
    assert "[Gao 2024, с. 102]" in out or "[Gao 2024b, PDF-с. 2]" in out
    index.show(project, "Gao2024", page="103")
    assert "quantization" in capsys.readouterr().out
    index.lookup(project, 'AND OR "NEAR( * -', 5)


def test_search_log_and_candidates(project, monkeypatch):
    monkeypatch.setitem(sources.DATABASES, "openalex", lambda q, n: [{"id": "W1", "title": "in library"}, {"id": "W77", "doi": "https://doi.org/10.5/y", "title": "New"}])
    rows = sources.search(project, "openalex", "export controls", 5, "H1")
    assert [r["status"] for r in rows] == ["library", "new"]
    rows = sources.search(project, "openalex", "export controls again", 5, "H1")
    assert rows[1]["status"] == "candidate"
    assert len(project.candidates()) == 1 and project.candidates()[0]["doi"] == "10.5/y"
    assert [r["new"] for r in project.search_log()] == [1, 0]
    reports.build_field(project)
    field = (project.field / "FIELD.md").read_text()
    assert "| H1 | 2 | 1 | 1 |" in field and "Gao и др. 2024 «Export controls" in field


def test_cli_init_and_wrapper(tmp_path):
    from lumi.cli import main
    assert main(["init", str(tmp_path / "p"), "--name", "P"]) == 0
    wrapper = tmp_path / "p" / "lumi"
    out = subprocess.run([str(wrapper), "status"], capture_output=True, text=True)
    assert out.returncode == 0 and "# P" in out.stdout


def test_installed_entrypoint_does_not_shadow_library_package(tmp_path, monkeypatch):
    import shutil
    import sys
    from pathlib import Path

    source = Path(__file__).resolve().parents[1]
    deployed = tmp_path / "share" / "lumi" / "lumi.py"
    deployed.parent.mkdir(parents=True)
    shutil.copy2(source / "lumi.py", deployed)
    monkeypatch.setenv("PYTHONPATH", str(source))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    out = subprocess.run(
        [sys.executable, str(deployed), "init", str(project), "--name", "Deployed"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    out = subprocess.run(
        [sys.executable, str(deployed), "library", "--help"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert out.returncode == 0 and "normalize" in out.stdout, out.stderr
