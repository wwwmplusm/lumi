"""Folder names fitted to the real literature/ tree, plus check / normalize safety."""

import hashlib
import json
from pathlib import Path

import pytest

from engine.index import lookup as engine_lookup
from engine.papers import resolve
from engine.project import Project
from engine.sources import record_search
from lumi.cli import main
from lumi.library import Library, check_status, index_text, keys_text
from lumi.naming import (
    FALLBACK_SUFFIXES,
    cite_key_for_work,
    first_author_name,
    last_name,
    make_cite_key,
    title_word,
)

FIXTURE = Path(__file__).parent / "fixtures" / "library"


def _snapshot(root):
    found = {}
    for path in Path(root).rglob("*"):
        if path.is_file():
            found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _rows():
    rows = []
    for line in (FIXTURE / "KEYS.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        key, openalex, folder, title = line.split("\t")
        prefix, rest = "", key
        for index, char in enumerate(key):
            if char.isdigit():
                prefix, rest = key[:index], key[index:]
                break
        year, word = rest[:4], rest[4:]
        rows.append({"key": key, "openalex": openalex, "folder": folder, "title": title, "surname": prefix, "year": year, "word": word})
    return rows


def _work(openalex, author, year, title, **extra):
    work = {
        "id": f"https://openalex.org/{openalex}" if openalex else None,
        "display_name": title,
        "publication_year": year,
        "authorships": [{"author": {"display_name": author}}],
    }
    work.update(extra)
    if not openalex:
        work.pop("id", None)
    return work


def build_real_library(tmp_path):
    project = tmp_path / "proj"
    library = project / "literature"
    library.mkdir(parents=True)
    keys = (FIXTURE / "KEYS.tsv").read_text(encoding="utf-8")
    index = (FIXTURE / "INDEX.md").read_text(encoding="utf-8")
    (library / "KEYS.tsv").write_text(keys, encoding="utf-8")
    (library / "INDEX.md").write_text(index, encoding="utf-8")
    by_key = {}
    for line in keys.splitlines()[1:]:
        key, openalex, folder, title = line.split("\t")
        by_key[folder] = (key, openalex, title)
    from lumi.library import parse_index
    index_rows, errors = parse_index(index)
    assert errors == []
    by_index = {row["folder"]: row for row in index_rows}
    for line in (FIXTURE / "literature_tree.txt").read_text(encoding="utf-8").splitlines():
        folder, listed = line.split("/:", 1)
        folder = folder.strip()
        key, openalex, title = by_key[folder]
        indexed = by_index[folder]
        year = int(indexed["year"])
        destination = library / folder
        destination.mkdir()
        for name in listed.split():
            path = destination / name
            if name == "metadata.json":
                meta = {
                    "key": key,
                    "openalex_id": openalex,
                    "folder": folder,
                    "doi": indexed["doi"],
                    "work": {
                        "id": f"https://openalex.org/{openalex}",
                        "display_name": title,
                        "publication_year": year,
                        "doi": indexed["doi"],
                        "authorships": [{"author": {"display_name": "Ann " + key[: len(key) - len(str(year)) - len(title_word(title))]}}],
                    },
                }
                path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            elif name == "notes_mark.md":
                path.write_text("OWNER NOTES SENTINEL\n", encoding="utf-8")
            elif name.startswith("notes-"):
                path.write_text("OWNER SIDE NOTE\n", encoding="utf-8")
            elif name == "README.md":
                path.write_text(f"# {title}\n", encoding="utf-8")
            else:
                path.write_bytes(b"x\n")
    return Library(library)


def test_real_keys_match_the_handoff_except_pu():
    rows = _rows()
    # The handoff was described as 53 works. KEYS.tsv has a header plus 52 data rows,
    # and literature_tree.txt has the same 52 folders.
    assert len(rows) == 52
    assert len((FIXTURE / "literature_tree.txt").read_text(encoding="utf-8").splitlines()) == 52
    mismatches = []
    for row in rows:
        got = make_cite_key(row["surname"].title(), row["year"], row["title"])
        assert got.startswith(row["surname"] + row["year"])
        if got != row["key"]:
            mismatches.append((row["key"], got, row["title"]))
    assert mismatches == [(
        "pu2026sanctions",
        "pu2026science",
        "Science under sanctions: The impact of the entity list on Chinese academic research",
    )]
    # The stored key is not a special case inside the generator.
    assert title_word(mismatches[0][2]) == "science"
    assert make_cite_key("Xiaodie Pu", 2026, mismatches[0][2]) == "pu2026science"


def test_key_rules_author_year_and_hyphens():
    assert last_name("Xiaodie Pu") == "pu"
    assert last_name("Griliches, Zvi") == "griliches"
    assert last_name("Charles I. Jones") == "jones"
    assert last_name("José Müller") == "muller"
    assert last_name("Li Fei-Fei") == "fei"  # unattested; hyphen cuts the surname
    assert last_name("van der Maaten, Laurens") == "vandermaaten"
    assert last_name("Laurens van der Maaten") == "maaten"  # last token only; not in the 52
    assert last_name("Ada Example Jr.") == "example"
    assert title_word("Co-authorship network analysis") == "coauthorship"
    assert title_word("The De-democratization of AI: Deep Learning") == "dedemocratization"
    assert title_word("R & D-Based Models of Economic Growth") == "based"
    assert title_word("Capital-Labor Substitution and Economic Efficiency") == "capital"
    assert title_word("What's Trending in Difference-in-Differences?") == "trending"
    assert title_word("Does U.S. Entity List impel Chinese AI enterprises") == "entity"
    assert title_word("China's Diffusion‐Forward AI Strategy") == "china"
    assert make_cite_key("Ann Adams", 1996, "Research Productivity in a System of Universities") == "adams1996research"


def test_kaplan_display_name_does_not_use_the_trailing_initial():
    # OpenAlex W3001279689 (Kaplan et al. 2020). display_name has a stray
    # trailing initial; raw_author_name is the inverted form.
    work = {
        "id": "https://openalex.org/W3001279689",
        "display_name": "Scaling Laws for Neural Language Models",
        "publication_year": 2020,
        "authorships": [{
            "raw_author_name": "Kaplan, Jared",
            "author": {"display_name": "Jared Kaplan A"},
        }],
    }
    assert last_name("Jared Kaplan A") == "kaplan"
    assert last_name("Kaplan, Jared") == "kaplan"
    assert first_author_name(work) == "Kaplan, Jared"
    assert cite_key_for_work(work) == "kaplan2020scaling"
    display_only = {
        "id": "https://openalex.org/W3001279689",
        "display_name": "Scaling Laws for Neural Language Models",
        "publication_year": 2020,
        "authorships": [{"author": {"display_name": "Jared Kaplan A"}}],
    }
    assert cite_key_for_work(display_only) == "kaplan2020scaling"


def test_collision_styles_are_a_single_switch():
    taken = {"example2020capital"}
    assert make_cite_key("Ada Example", 2020, "Capital markets", taken=taken) == "example2020capitalb"
    assert make_cite_key("Ada Example", 2020, "Capital markets", taken=taken, collision="scholar") == "example2020capitala"
    third = make_cite_key("Ada Example", 2020, "Capital markets", taken={"example2020capital", "example2020capitalb"})
    assert third == "example2020capitalc"


def test_catalog_roundtrip_and_adams_year_quirk(tmp_path):
    library = build_real_library(tmp_path)
    works = library.load_works()
    assert keys_text(works) == (library.root / "KEYS.tsv").read_text(encoding="utf-8")
    assert index_text(works) == (library.root / "INDEX.md").read_text(encoding="utf-8")
    adams = next(work for work in works if work.folder.startswith("adams1996"))
    assert adams.key == "adams1996research"
    assert adams.year == "1998"  # INDEX year, not the 1996 baked into the cite key


def test_check_reports_real_tree_and_writes_nothing(tmp_path):
    library = build_real_library(tmp_path)
    before = _snapshot(library.root.parent)
    findings = library.check()
    assert _snapshot(library.root.parent) == before
    assert check_status(findings) == 1
    assert not any(item.level == "error" for item in findings)
    noncanonical = {item.path.split("/", 1)[1] for item in findings if item.code == "noncanonical"}
    assert noncanonical == {
        "fulltext_read_keenable.md",
        "fulltext_v6_read_keenable.md",
        "paper-v6-read.pdf",
        "paper.refetch.pdf",
        "wp_merit_rm1999-027.pdf",
        "wp_merit_rm1999-027.txt",
        "search_notes.md",
        "notes-technologic-jin.md",
        "paper-highlighted.pdf",
        "verified-excerpts.md",
        "ocr_selected_pages.txt",
    }
    assert "paper.ocr.pdf" not in noncanonical
    assert any(item.code == "metadata-only" and item.path.startswith("mahoney2012") for item in findings)
    assert any(item.code == "missing" and item.path == "collier2011understanding-W2159961463/pages.json" for item in findings)
    notes = [item for item in findings if item.path.endswith("/notes_mark.md")]
    assert notes and all(item.level == "info" for item in notes)
    assert not any(item.path.startswith("pu2026sanctions") and item.path.endswith("notes_mark.md") for item in notes)
    assert (library.root / "pu2026sanctions-W7203724392" / "notes_mark.md").read_text(encoding="utf-8") == "OWNER NOTES SENTINEL\n"
    assert library.plan() == []
    library.apply()
    assert _snapshot(library.root.parent) == before


def test_check_is_read_only_even_if_the_tree_is_not_writable(tmp_path):
    library = build_real_library(tmp_path)
    paths = [library.root, *library.root.rglob("*")]
    for path in paths:
        path.chmod(0o555 if path.is_dir() else 0o444)
    try:
        findings = library.check()
        assert findings
    finally:
        for path in paths:
            path.chmod(0o755 if path.is_dir() else 0o644)


def test_save_is_idempotent_and_never_writes_notes(tmp_path):
    library = Library(tmp_path / "literature")
    first = _work("W1", "Ada Example", 2020, "Capital markets after the shock", doi="https://doi.org/10.1000/example")
    folder = library.save_work(first, pdf_bytes=b"%PDF-1.4\nfirst\n")
    assert folder == "example2020capital-W1"
    work_dir = library.root / folder
    assert (work_dir / "metadata.json").is_file()
    assert (work_dir / "README.md").is_file()
    assert not (work_dir / "notes_mark.md").exists()
    assert not (work_dir / "ai_review.md").exists()
    readme = "custom reading notes, do not replace\n"
    (work_dir / "README.md").write_text(readme, encoding="utf-8")
    (work_dir / "paper.pdf").write_bytes(b"%PDF-ORIGINAL")
    (work_dir / "notes_mark.md").write_text("OWNER\n", encoding="utf-8")
    before = _snapshot(library.root)
    again = library.save_work(first, pdf_bytes=b"%PDF-OTHER")
    assert again == folder
    assert _snapshot(library.root) == before
    assert (work_dir / "paper.pdf").read_bytes() == b"%PDF-ORIGINAL"
    assert (work_dir / "README.md").read_text(encoding="utf-8") == readme
    assert (work_dir / "notes_mark.md").read_text(encoding="utf-8") == "OWNER\n"
    keys = (library.root / "KEYS.tsv").read_text(encoding="utf-8")
    assert keys.count("example2020capital-W1") == 1
    second = _work("W2", "Ada Example", 2020, "Capital controls in a crisis")
    other = library.save_work(second)
    assert other == "example2020capitalb-W2"
    assert library.save_work(first) == folder
    assert not (library.root / "example2020capitalc-W1").exists()
    with pytest.raises(PermissionError):
        library.write_text(work_dir / "notes_mark.md", "nope\n")
    with pytest.raises(PermissionError):
        library.write_text(work_dir / "notes-extra.md", "nope\n")
    with pytest.raises(PermissionError):
        library.write_text(work_dir / "ai_review.md", "nope\n")


def test_fallback_order_is_easy_to_change(tmp_path, monkeypatch):
    library = Library(tmp_path / "literature")
    doi_work = _work(None, "Bea Sample", 2021, "Widgets after the rain", doi="https://doi.org/10.1016/j.example.2021.1", ids={"arxiv": "2401.02452"})
    doi_folder = library.save_work(doi_work)
    assert doi_folder == "sample2021widgets-doi_10.1016_j.example.2021.1"
    assert library.save_work(doi_work) == doi_folder
    arxiv_work = _work(None, "Bea Sample", 2022, "Widgets after the rain", ids={"arxiv": "2401.09999v2"})
    assert library.save_work(arxiv_work) == "sample2022widgets-arxiv_2401.09999"
    pdf = b"%PDF-1.4\nlocal-only\n"
    digest = hashlib.sha256(pdf).hexdigest()
    local = library.save_work(_work(None, "Bea Sample", 2023, "Widgets after the rain"), pdf_bytes=pdf)
    assert local == f"sample2023widgets-local_{digest[:8]}"
    assert library.save_work(_work(None, "Bea Sample", 2023, "Widgets after the rain"), pdf_bytes=pdf) == local
    assert FALLBACK_SUFFIXES == ("doi", "arxiv", "local")
    monkeypatch.setattr("lumi.naming.FALLBACK_SUFFIXES", ("arxiv", "doi", "local"))
    both = _work(None, "Cara Other", 2024, "Widgets after the rain", doi="https://doi.org/10.1000/zzz", ids={"arxiv": "1991.00001"})
    assert library.save_work(both) == "other2024widgets-arxiv_1991.00001"
    # Same DOI is the same work. save does not rename a fallback folder, even after an OpenAlex id shows up.
    again = _work("W9", "Cara Other", 2024, "Widgets after the rain", doi="https://doi.org/10.1000/zzz")
    assert library.save_work(again) == "other2024widgets-arxiv_1991.00001"
    # OpenAlex still wins over the fallback list for a work that is not saved yet.
    fresh = _work("W11", "Dora Fresh", 2024, "Widgets after the rain", doi="https://doi.org/10.1000/fresh")
    assert library.save_work(fresh) == "fresh2024widgets-W11"
    stored = library.root / "other2024widgets-arxiv_1991.00001" / "metadata.json"
    meta = json.loads(stored.read_text(encoding="utf-8"))
    meta["openalex_id"] = "W9"
    meta["work"]["id"] = "https://openalex.org/W9"
    stored.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    planned = library.plan()
    assert any(action.kind == "rename" and action.dst == "other2024widgets-W9" for action in planned)


def test_existing_hand_named_folder_is_not_renamed_to_the_generated_key(tmp_path):
    library = build_real_library(tmp_path)
    before = _snapshot(library.root)
    work = _work(
        "W7203724392",
        "Xiaodie Pu",
        2026,
        "Science under sanctions: The impact of the entity list on Chinese academic research",
        doi="https://doi.org/10.1016/j.respol.2026.105584",
    )
    assert cite_key_for_work(work) == "pu2026science"
    assert library.save_work(work) == "pu2026sanctions-W7203724392"
    assert not (library.root / "pu2026science-W7203724392").exists()
    assert _snapshot(library.root) == before


def test_parse_index_lookup_and_legacy_normalize_leave_notes_alone(tmp_path, monkeypatch):
    library = Library(tmp_path / "proj" / "literature")
    library.root.mkdir(parents=True)
    legacy = library.root / "W100"
    legacy.mkdir()
    (legacy / "metadata.json").write_text(json.dumps({
        "work": _work("W100", "Ada Legacy", 2019, "Widgets after the rain"),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (legacy / "README.md").write_text("# Widgets\n", encoding="utf-8")
    (legacy / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    (legacy / "notes_mark.md").write_text("OWNER NOTES SENTINEL\n", encoding="utf-8")
    (legacy / "notes-draft.md").write_text("OWNER SIDE NOTE\n", encoding="utf-8")
    (legacy / "paper.refetch.pdf").write_bytes(b"%PDF-REFETCH")
    draft = tmp_path / "proj" / "draft.md"
    draft.write_text("old path literature/W100/paper.pdf\n", encoding="utf-8")

    findings = library.check(tmp_path / "proj")
    assert any(item.code == "legacy-folder" and "legacy2019widgets-W100" in item.message for item in findings)
    assert any(item.code == "stale-ref" and "draft.md:1" in item.path for item in findings)
    assert any(item.code == "noncanonical" and item.path.endswith("paper.refetch.pdf") for item in findings)
    before = _snapshot(tmp_path / "proj")
    assert library.plan()
    assert _snapshot(tmp_path / "proj") == before

    monkeypatch.setattr("lumi.library.extract_pdf_text", lambda pdf: (["zyzzyva on page one"], "layout zyzzyva\n"))
    messages = library.parse(["W100"])
    assert any("W100" in message for message in messages)
    assert (legacy / "notes_mark.md").read_text(encoding="utf-8") == "OWNER NOTES SENTINEL\n"
    assert (legacy / "paper.refetch.pdf").read_bytes() == b"%PDF-REFETCH"
    assert "zyzzyva" in (legacy / "paper.txt").read_text(encoding="utf-8")

    actions = library.apply()
    assert any(action.kind == "rename" and action.src == "W100" and action.dst == "legacy2019widgets-W100" for action in actions)
    moved = library.root / "legacy2019widgets-W100"
    assert moved.is_dir() and not legacy.exists()
    assert (moved / "notes_mark.md").read_text(encoding="utf-8") == "OWNER NOTES SENTINEL\n"
    assert (moved / "notes-draft.md").read_text(encoding="utf-8") == "OWNER SIDE NOTE\n"
    assert (moved / "paper.refetch.pdf").read_bytes() == b"%PDF-REFETCH"
    keys = (library.root / "KEYS.tsv").read_text(encoding="utf-8")
    assert keys.count("legacy2019widgets-W100") == 1
    assert all(line.split("\t")[2] != "W100" for line in keys.splitlines()[1:])
    count = library.rebuild_index()
    assert count == 1
    rows = library.lookup("zyzzyva", papers=["W100"])
    assert rows and rows[0][0] == "legacy2019widgets-W100"
    rows = library.lookup("zyzzyva", papers=["legacy2019widgets"])
    assert rows[0][0] == "legacy2019widgets-W100"
    assert (moved / "notes_mark.md").read_text(encoding="utf-8") == "OWNER NOTES SENTINEL\n"


def test_mismatch_is_reported_and_not_repaired_by_check(tmp_path):
    library = Library(tmp_path / "literature")
    folder = library.save_work(_work("W5", "Ada Example", 2020, "Capital markets after the shock"))
    meta_path = library.root / folder / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["key"] = "example2020wrong"
    meta_path.write_text(json.dumps(meta) + "\n", encoding="utf-8")
    before = _snapshot(library.root)
    findings = library.check()
    assert any(item.level == "error" and item.code == "mismatch" for item in findings)
    assert _snapshot(library.root) == before
    assert main(["library", "check", "--library", str(library.root)]) == 1


def test_engine_lookup_accepts_new_folder_names(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    folder = root / "sources" / "papers" / "example2020capital-W50"
    folder.mkdir(parents=True)
    (root / "eurema.json").write_text(json.dumps({"name": "T", "context": "c"}))
    meta = {"key": "example2020capital", "work": _work("W50", "Ada Example", 2020, "Capital markets after the shock", doi="https://doi.org/10.1000/z")}
    (folder / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (folder / "pages.json").write_text(json.dumps({"pages": ["zyzzyva export control"]}), encoding="utf-8")
    legacy = root / "sources" / "papers" / "W2"
    legacy.mkdir()
    (legacy / "metadata.json").write_text(json.dumps({"key": "Gao2024b", "work": _work("W2", "Ann Gao", 2024, "Collaboration under sanctions")}), encoding="utf-8")
    project = Project(root)
    assert resolve(project, "W50") == "example2020capital-W50"
    assert resolve(project, "example2020capital") == "example2020capital-W50"
    assert resolve(project, "Gao2024b") == "W2"
    assert resolve(project, "gao 2024b") == "W2"
    rows = engine_lookup(project, "zyzzyva", 5, refs=["W50"])
    assert rows and rows[0][0] == "example2020capital-W50"
    monkeypatch.setattr("engine.sources.DATABASES", {"openalex": lambda q, n: [{"id": "W50", "doi": "https://doi.org/10.1000/z", "title": "in library"}, {"id": "W77", "doi": "https://doi.org/10.5/y", "title": "New"}]})
    found = record_search(project, "openalex", "q", [{"id": "W50", "doi": "https://doi.org/10.1000/z", "title": "in library"}, {"id": "W77", "doi": "https://doi.org/10.5/y", "title": "New"}])
    assert [row["status"] for row in found] == ["library", "new"]


def test_cli_save_and_normalize_dry_run(tmp_path):
    library = tmp_path / "literature"
    payload = tmp_path / "work.json"
    payload.write_text(json.dumps(_work("W8", "Ada Example", 2020, "Capital markets after the shock")), encoding="utf-8")
    assert main(["library", "save", str(payload), "--library", str(library)]) == 0
    assert (library / "example2020capital-W8" / "README.md").is_file()
    assert not (library / "example2020capital-W8" / "notes_mark.md").exists()
    assert main(["library", "check", "--library", str(library)]) == 0
    legacy = library / "W8"
    legacy.mkdir()
    (legacy / "metadata.json").write_text(json.dumps({"work": _work("W8", "Ada Example", 2020, "Capital markets after the shock")}), encoding="utf-8")
    (legacy / "README.md").write_text("# x\n", encoding="utf-8")
    (legacy / "notes_mark.md").write_text("keep\n", encoding="utf-8")
    before = _snapshot(library)
    assert main(["library", "normalize", "--library", str(library)]) == 0
    assert _snapshot(library) == before
    assert main(["library", "normalize", "--library", str(library), "--apply"]) == 0
    assert not legacy.exists()
    assert (library / "example2020capitalb-W8" / "notes_mark.md").read_text(encoding="utf-8") == "keep\n"
