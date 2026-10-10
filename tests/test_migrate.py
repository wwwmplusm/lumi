import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import migrate_v0  # noqa: E402

from engine import papers  # noqa: E402
from engine.project import Project  # noqa: E402


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_migrate_old_layout(tmp_path):
    root = tmp_path / "old"
    paper = root / "literature" / "W1"
    paper.mkdir(parents=True)
    work = {"id": "https://openalex.org/W1", "display_name": "A study", "publication_year": 2020, "doi": "https://doi.org/10.48550/arxiv.1",
            "authorships": [{"author": {"display_name": "Ann Gao"}}]}
    (paper / "metadata.json").write_text(json.dumps({"work": work}))
    (paper / "pages.json").write_text(json.dumps({"pages": ["Some text that is long enough.\n1", "More text.\n2", "End.\n3"]}))
    (paper / "paper.txt").write_text("old")
    (paper / "paper.pdf").write_bytes(b"%PDF-untracked")
    (paper / "README.md").write_text("# A study\n\n## Reading notes\n\nRead in full: key finding on p. 2.\n")
    (root / "literature" / "INDEX.md").write_text("index")
    (root / "research" / "knowledge").mkdir(parents=True)
    (root / "research" / "knowledge" / "claims.jsonl").write_text(json.dumps({"id": "C0001", "statement": "Text.", "type": "finding", "status": "active",
                                                                              "evidence": [{"paper": "W1", "page": 1, "quote": "Some text that is long enough."}], "unquoted_numbers": ["9"]}) + "\n")
    (root / "research" / "knowledge" / "edges.jsonl").write_text("")
    (root / "research" / "questions.md").write_text("- H1: Q | keywords: a\n")
    (root / "research" / "library.py").write_text("# old")
    (root / "CLAUDE.md").write_text("old rules")
    (root / "notes_by_user.md").write_text("mine")
    (root / ".gitignore").write_text("*.pdf\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "old")

    migrate_v0.migrate(root, "Test", "ctx", dry=False)

    project = Project(root)
    assert project.paper_ids() == ["W1"]
    meta = papers.load_meta(project, "W1")
    assert meta["key"] == "Gao2020" and meta["fulltext"] == "yes" and meta["page_source"] in ("header", "preprint")
    assert (project.papers / "W1" / "paper.pdf").exists() and not (project.papers / "W1" / "paper.txt").exists()
    card = (project.papers / "W1" / "README.md").read_text()
    assert card.startswith("# Gao 2020 — A study") and "## Заметки" in card and "key finding on p. 2" in card
    assert "unquoted_numbers" not in project.claims()[0]
    assert (root / "archive" / "v0" / "research" / "library.py").exists() and (root / "archive" / "v0" / "CLAUDE.md").exists()
    assert "RULES.md" in (root / "CLAUDE.md").read_text() and (root / "eurema").exists() and (root / "MAP.md").exists()
    assert (root / "notes_by_user.md").exists() and not (root / "literature").exists() and not (root / "research").exists()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout
    assert "literature/W1/metadata.json -> sources/papers/W1/metadata.json" in status
    assert "paper.txt" not in status.replace("D  literature/W1/paper.txt", "")
