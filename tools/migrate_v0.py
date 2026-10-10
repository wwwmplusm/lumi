#!/usr/bin/env python3
"""One-off migration of a project from the old layout (research/library.py, literature/) to Eurema.

    python3 tools/migrate_v0.py /path/to/project [--name N] [--context "…"] [--dry-run]

Moves use `git mv` (history kept). Only known engine-managed paths are moved; other files stay where they are
and are listed at the end for the user to decide. Old tooling goes to archive/v0/.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

from engine import cards, claims, papers  # noqa: E402
from engine.cli import install_files  # noqa: E402
from engine.project import MARKER, Project, read_jsonl  # noqa: E402

PLACEHOLDER = "Not read yet. Record claims with page or section, methods, sample, and limitations."


class Mover:
    def __init__(self, root, dry):
        self.root, self.dry = root, dry
        self.tracked = set(self.git("ls-files", "-z").split("\0")) if (root / ".git").exists() else set()

    def git(self, *args, check=True):
        result = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)
        if check and result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
        return result.stdout

    def is_tracked(self, rel):
        rel = str(rel)
        return rel in self.tracked or any(t.startswith(rel.rstrip("/") + "/") for t in self.tracked)

    def move(self, src, dst):
        s, d = self.root / src, self.root / dst
        if not s.exists():
            return
        print(f"  {src} → {dst}")
        if self.dry:
            return
        d.parent.mkdir(parents=True, exist_ok=True)
        if self.is_tracked(src):
            self.git("mv", "-k", str(src), str(dst))
            if s.exists():  # untracked leftovers (PDFs, caches) inside a moved directory
                for leftover in sorted(p for p in s.rglob("*") if p.is_file()):
                    target = d / leftover.relative_to(s)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(leftover), str(target))
                shutil.rmtree(s, ignore_errors=True)
        else:
            shutil.move(str(s), str(d))

    def remove(self, rel):
        path = self.root / rel
        if not path.exists():
            return
        print(f"  удалить {rel}")
        if self.dry:
            return
        if (self.root / ".git").exists():  # the tracked set predates the moves, so ask git directly
            self.git("rm", "-r", "-q", "-f", "--ignore-unmatch", str(rel))
        if path.exists():
            shutil.rmtree(path) if path.is_dir() else path.unlink()


def old_notes(text):
    """Reading notes from an old README (everything after '## Reading notes' that is not the placeholder)."""
    if "## Reading notes" not in text:
        return ""
    body = text.split("## Reading notes", 1)[1].strip()
    return "" if not body or body == PLACEHOLDER else body


def migrate(root, name, context, dry):
    m = Mover(root, dry)
    print("1. Статьи: literature/W* → sources/papers/")
    notes = {}
    for folder in sorted((root / "literature").glob("W*")):
        readme = folder / "README.md"
        notes[folder.name] = old_notes(readme.read_text(encoding="utf-8")) if readme.exists() else ""
        m.move(f"literature/{folder.name}", f"sources/papers/{folder.name}")
    m.remove("literature/INDEX.md")
    if not dry and (root / "literature").exists() and not any((root / "literature").iterdir()):
        (root / "literature").rmdir()

    print("2. Знание и поле")
    research = root / "research"
    m.move("research/knowledge/claims.jsonl", "knowledge/claims.jsonl")
    for f in ("edges.jsonl", "candidates.jsonl", "MAP.md", "GAPS.md"):
        m.move(f"research/knowledge/{f}", f"archive/v0/knowledge/{f}")
    m.move("research/field/search_log.jsonl", "field/search_log.jsonl")
    m.move("research/field/candidates.jsonl", "field/candidates.jsonl")
    m.move("research/questions.md", "questions.md")
    m.move("research/runs", "runs")

    print("3. Старые инструменты и отчёты → archive/v0/")
    m.remove("research/index.sqlite")
    for cache in research.rglob("__pycache__") if research.exists() else []:
        if not dry:
            shutil.rmtree(cache, ignore_errors=True)
    for rest in sorted(research.iterdir()) if research.exists() else []:
        m.move(f"research/{rest.name}", f"archive/v0/research/{rest.name}")
    for f in ("AGENTS.md", "ARCHITECTURE.md"):
        m.move(f, f"archive/v0/{f}")
    if (root / "CLAUDE.md").exists():
        m.move("CLAUDE.md", "archive/v0/CLAUDE.md")
    if dry:
        return
    if research.exists() and not any(p.is_file() for p in research.rglob("*")):
        shutil.rmtree(research)

    print("4. Конфиг, шаблоны, обёртка ./eurema")
    if not (root / MARKER).exists():
        (root / MARKER).write_text(json.dumps({"name": name, "context": context, "worker_model": "sonnet", "worker_cap_per_day": 40}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    install_files(root, name, overwrite=("CLAUDE.md",))
    gitignore = (root / ".gitignore").read_text(encoding="utf-8") if (root / ".gitignore").exists() else ""
    if ".cache/" not in gitignore:
        (root / ".gitignore").write_text(gitignore.rstrip("\n") + "\n.cache/\n", encoding="utf-8")
    for sub in ("sources/datasets", "notes", "drafts", "sessions", "field", "knowledge"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    project = Project(root)
    print("5. Метаданные статей: ключ, полнота текста, печатные страницы, вопросы")
    candidate_q = {}
    for c in project.candidates():
        if c.get("question"):
            candidate_q.setdefault(c["id"], set()).add(c["question"])
    taken = set()
    for identifier in project.paper_ids():
        meta = papers.load_meta(project, identifier)
        if meta.get("key"):
            taken.add(meta["key"])
    for identifier in project.paper_ids():
        meta = papers.load_meta(project, identifier)
        if not meta.get("key"):
            meta["key"] = papers.make_key(meta["work"], taken)
            taken.add(meta["key"])
        meta["questions"] = sorted(set(meta.get("questions") or []) | candidate_q.get(identifier, set()))
        meta["fulltext"] = papers.fulltext_status(project, identifier, meta)
        layout = papers.load_pages(project, identifier, layout=True) or papers.load_pages(project, identifier)
        if layout:
            papers.detect_pages(meta, papers.pdf_file(project, identifier), layout)
        else:
            meta.setdefault("page_source", "none")
        papers.save_meta(project, identifier, meta)
        if (project.papers / identifier / "paper.txt").exists():
            m.remove(f"sources/papers/{identifier}/paper.txt")  # superseded by pages.json
        print(f"  {papers.label(meta)} · {meta['key']} · текст {meta['fulltext']} · страницы {meta.get('page_source')}")

    print("6. Карточки: шапка + аннотация; старые заметки сохраняются в «## Заметки»")
    for identifier in project.paper_ids():
        path = cards.card_path(project, identifier)
        if notes.get(identifier):
            path.write_text("## Заметки\n\n" + notes[identifier] + "\n", encoding="utf-8")
        elif path.exists():
            path.unlink()
        cards.write_stub(project, identifier)

    print("7. Факты: lint по новым правилам")
    rows = project.claims()
    for c in rows:
        c.pop("unquoted_numbers", None)
    project.save_claims(rows)
    claims.lint(project)
    from engine import index, reports
    index.build(project)
    reports.build_map(project)
    reports.build_field(project)

    leftovers = [p.name for p in sorted(root.iterdir()) if p.name not in {".git", ".gitignore", ".cache", MARKER, "eurema", "CLAUDE.md", "CONTEXT.md", "questions.md", "MAP.md", "FIELD.md",
                                                                       "sources", "field", "knowledge", "notes", "drafts", "sessions", "runs", "archive"}]
    print("\nНе тронуто (решить вручную — notes/, sources/datasets/ или оставить):")
    for name_ in leftovers:
        print(f"  {name_}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--name")
    parser.add_argument("--context", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not (root / "literature").exists() and not (root / "research").exists():
        raise SystemExit("не похоже на старый проект (нет literature/ и research/)")
    migrate(root, args.name or root.name, args.context, args.dry_run)


if __name__ == "__main__":
    main()
