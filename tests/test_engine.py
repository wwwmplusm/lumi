import json
import subprocess

import pytest
from conftest import fake_worker

from engine import beliefs, cards, claims, index, papers, quotes, reports, sources, workers
from engine.text import normalize, normalize_map


# --- text and quotes -----------------------------------------------------------------

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


# --- labels, keys, pages -------------------------------------------------------------------

def test_keys_labels_and_resolve(project):
    meta = papers.load_meta(project, "W1")
    assert papers.make_key(meta["work"], {"Gao2024"}) == "Gao2024b"
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
    assert papers.qpdf_labels("x.pdf", 3) is None  # identity 1..N is no information


def test_index_lookup_and_show(project, capsys):
    index.build(project)
    rows = index.lookup(project, "export control GPUs", 5)
    assert rows and rows[0][0] in ("W1", "W2")
    out = capsys.readouterr().out
    assert "[Gao 2024, с. 102]" in out or "[Gao 2024b, PDF-с. 2]" in out
    index.show(project, "Gao2024", page="103")
    assert "quantization" in capsys.readouterr().out
    index.lookup(project, 'AND OR "NEAR( * -', 5)  # syntax-safe


# --- claims ----------------------------------------------------------------------------------

def test_verify_attribution_and_lint(project):
    raw = [{"statement": "Controls cut GPU access for Chinese laboratories.", "type": "finding", "page": 3, "quote": "Export controls reduced GPU access for Chinese laboratories", "attribution": "own"},
           {"statement": "Invented.", "type": "finding", "page": 1, "quote": "this sentence does not exist anywhere in it"},
           {"statement": "Quantization cut compute needs by 40% in all labs.", "type": "finding", "page": 3, "quote": "efficient training methods such as quantization, which may reduce compute needs"}]
    accepted, rejected = claims.verify(project, "W1", raw)
    assert len(accepted) == 2 and len(rejected) == 1
    first, second = accepted
    assert first["evidence"][0]["page"] == 2 and first["page_corrected"]
    assert first["attribution"] == "own" and first["attribution_by"].startswith("worker (code saw")
    flags = " ".join(second["lint"])
    assert "40" in flags and "may" in flags and "all" in flags


def test_extract_with_fake_worker_appends_and_skips(project, monkeypatch, capsys):
    run, calls = fake_worker([{"claims": [{"statement": "Labs shifted to quantization.", "type": "finding", "page": 3, "quote": "Chinese labs shifted toward efficient training methods"}]}])
    monkeypatch.setattr(workers, "run", run)
    claims.extract(project, ["Gao2024"])
    claims.extract(project, ["Gao2024"])
    assert len(project.claims()) == 1 and len(calls) == 1
    assert "Project context: export controls" in calls[0][2] and "(printed 101)" in calls[0][2]
    assert "уже извлечены" in capsys.readouterr().out


def test_manual_claim_checks_quote(project):
    claim = claims.add_manual(project, "Access fell.", "Gao2024", "reduced GPU access for Chinese laboratories", page="102", questions=["H1"])
    assert claim["id"] == "C0001" and claim["evidence"][0]["page"] == 2 and claim["questions"] == ["H1"]
    with pytest.raises(ValueError):
        claims.add_manual(project, "Made up.", "Gao2024", "a fabricated sentence that is not in the paper")
    assert claims.add_manual(project, "Access fell.", "Gao2024", "reduced GPU access for Chinese laboratories", page="102") is None
    (project.datasets / "cnki").mkdir(parents=True)
    with pytest.raises(ValueError):
        claims.add_manual(project, "Output rose.", dataset="cnki", how="count")
    (project.datasets / "cnki" / "README.md").write_text("# cnki")
    assert claims.add_manual(project, "Output rose 5%.", dataset="cnki", how="groupby year count")["evidence"][0]["dataset"] == "cnki"


def test_worker_isolation_and_daily_cap(project, monkeypatch):
    seen = {}

    class R:
        returncode, stderr = 0, ""
        stdout = json.dumps({"result": '{"claims": []}', "total_cost_usd": 0.01, "usage": {"input_tokens": 10, "output_tokens": 5}, "num_turns": 1})

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, cwd=kw["cwd"], env=kw["env"])
        return R
    monkeypatch.setattr(workers.subprocess, "run", fake_run)
    monkeypatch.setenv("WORKER_CAP", "1")
    parsed, record = workers.run(project, "extract", "task", "stdin", "W1")
    assert parsed == {"claims": []} and record["ok"]
    assert "--strict-mcp-config" in seen["cmd"] and seen["cmd"][seen["cmd"].index("--tools") + 1] == "Read"
    assert seen["cwd"] == project.runs and seen["env"]["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    with pytest.raises(ValueError):
        workers.run(project, "extract", "task", "stdin", "W1")


# --- beliefs ---------------------------------------------------------------------------------

def make_claims(project):
    c1 = claims.add_manual(project, "Access fell.", "Gao2024", "reduced GPU access for Chinese laboratories", page="102", questions=["H1"], attribution="own")
    c2 = claims.add_manual(project, "Labs had less access.", "Gao2024b", "Chinese AI laboratories had less access to GPUs", pdf_page=2, questions=["H1"])
    c3 = claims.add_manual(project, "Co-authorship declined.", "Gao2024b", "International co-authorship between US and China declined", pdf_page=3)
    return c1, c2, c3


def test_belief_versions_confidence_and_propagation(project):
    c1, c2, c3 = make_claims(project)
    b = beliefs.add(project, "H1", "Controls cut access", [c1["id"]])
    assert b["confidence"] == "low" and not b["seen_by_user"]
    b2 = beliefs.revise(project, b["id"], "second paper agrees", add_premises=[c2["id"]])
    assert b2["v"] == 2 and b2["confidence"] == "high"
    rows = project.belief_rows()
    assert [r["status"] for r in rows] == ["superseded", "active"]
    project.notes.mkdir()
    (project.notes / "x.md").write_text("# Talk\n")
    b3 = beliefs.add(project, "H1", "Built on B001", ["B001", "N:notes/x.md"])
    assert b3["confidence"] == "high"  # inherits the claims of its premise belief
    beliefs.revise(project, b["id"], "a contrary result", add_against=[c3["id"]])
    assert beliefs.current(project.belief_rows())[b["id"]]["confidence"] == "low"
    claims.set_status(project, c1["id"], "retracted", "misread")
    changed = beliefs.refresh(project)
    now = beliefs.current(project.belief_rows())
    assert set(changed) == {"B001", "B002"} and now["B002"]["status"] == "review"
    with pytest.raises(ValueError):
        beliefs.add(project, "H1", "No premises", [])
    with pytest.raises(ValueError):
        beliefs.add(project, "H1", "Bad ref", ["C9999"])
    with pytest.raises(ValueError):
        beliefs.add(project, "H7", "Unknown question", [c2["id"]])
    assert beliefs.add(project, "H2", "Just a hunch", [], kind="idea")["confidence"] == "idea"


def test_map_shows_names_not_bare_ids(project):
    c1, c2, _ = make_claims(project)
    beliefs.add(project, "H1", "Controls cut access", [c1["id"], c2["id"]], reasoning="two papers")
    reports.build_map(project)
    text = (project.root / "MAP.md").read_text()
    assert "### Controls cut access ●" in text
    assert "«reduced GPU access for Chinese laboratories» — [Gao 2024, с. 102] «Export controls and Chinese AI labs…» · C0001" in text
    assert "[Gao 2024b, PDF-с. 2]" in text
    for line in text.splitlines():  # an id never leads a human line
        assert not line.lstrip("-✓✗ ").startswith(("C0", "B0", "W1", "W2"))
    beliefs.mark_seen(project)
    reports.build_map(project)
    assert "●" not in (project.root / "MAP.md").read_text().split("## H1")[1]


# --- cards, field, search log --------------------------------------------------------------------

def test_card_stub_render_keeps_notes(project, monkeypatch):
    path = cards.card_path(project, "W1")
    path.write_text("old header\n\n## Заметки\n\nmy reading notes\n")
    cards.write_stub(project, "W1")
    text = path.read_text()
    assert "Основа: полный текст, с. 101–103" in text and "my reading notes" in text and "Short abstract." in text
    run, calls = fake_worker([{"aim": "Цель.", "data_method": "", "results": [{"text": "Доступ упал.", "page": "102"}, {"text": "Нет такой.", "page": "999"}], "limitations": "Одна страна.", "for_us": "H1"}])
    monkeypatch.setattr(workers, "run", run)
    cards.generate(project, ["Gao2024"])
    text = path.read_text()
    assert "- Доступ упал. (с. 102)" in text and "(с. ?)" in text and "**Данные и метод.** нет в источнике" in text
    assert text.index("Основа") < text.index("**Вопрос и цель.**") < text.index("## Заметки") and "my reading notes" in text
    assert cards.check_cards(project) == ["Gao и др. 2024b «Collaboration under sanctions»: нет карточки"]


def test_search_log_and_candidates(project, monkeypatch):
    monkeypatch.setitem(sources.DATABASES, "openalex", lambda q, n: [{"id": "W1", "title": "in library"}, {"id": "W77", "doi": "https://doi.org/10.5/y", "title": "New"}])
    rows = sources.search(project, "openalex", "export controls", 5, "H1")
    assert [r["status"] for r in rows] == ["library", "new"]
    rows = sources.search(project, "openalex", "export controls again", 5, "H1")
    assert rows[1]["status"] == "candidate"
    assert len(project.candidates()) == 1 and project.candidates()[0]["doi"] == "10.5/y"
    assert [r["new"] for r in project.search_log()] == [1, 0]
    reports.build_field(project)
    field = (project.root / "FIELD.md").read_text()
    assert "| H1 | 2 | 1 | 1 | мало запросов |" in field and "Gao и др. 2024 «Export controls" in field


def test_status_and_finish_run(project, capsys):
    make_claims(project)
    (project.sessions).mkdir()
    (project.sessions / "2026-10-01.md").write_text("# s\n## Дальше\n- read Gao\n")
    reports.status(project)
    out = capsys.readouterr().out
    assert "read Gao" in out and "фактов 3" in out
    reports.finish(project)
    assert (project.root / "MAP.md").exists() and (project.root / "FIELD.md").exists()


def test_cli_init_and_wrapper(tmp_path):
    from engine.cli import main
    assert main(["init", str(tmp_path / "p"), "--name", "P"]) == 0
    wrapper = tmp_path / "p" / "eurema"
    out = subprocess.run([str(wrapper), "status"], capture_output=True, text=True)
    assert out.returncode == 0 and "# P" in out.stdout
