"""Create, find, index, and check a ``literature/`` directory.

Files in each work folder are the source of truth. ``KEYS.tsv`` and
``INDEX.md`` are rebuilt from those folders. ``.lumi/index.sqlite`` is a
rebuildable full-text index.

This module does not import the v1 engine. Copy it (and ``naming.py``) into
the thesis repo and call it from ``research/library.py``:

- ``add`` around line 142 (``folder = LIBRARY / identifier``) should call
  ``Library.save_work`` instead of creating ``literature/<OpenAlexID>``.
- ``index`` (line 127), ``parse`` (line 314) and ``build_index`` (line 364)
  glob ``W*/`` and miss ``<key>-W…`` folders. Iterate ``Library.work_dirs``.
- ``load_pages`` (line 337), ``show`` (line 401), ``paper_text`` (line 506)
  and ``paper_label`` (line 789) join ``LIBRARY / identifier``. Resolve the
  identifier with ``lumi.naming.resolve_ref`` first. ``show`` also uppercases
  its argument (line 1129), which breaks a lowercase cite key.

``notes_mark.md`` and ``notes-*.md`` / ``notes_*.md`` are never written,
renamed, or deleted. A directory rename that gives a legacy folder its new
name carries those files along unchanged; that is the only way they move.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lumi.naming import (
    arxiv_id_of,
    bare_doi,
    cite_key_for_work,
    doi_url,
    folder_name_for,
    is_legacy_folder,
    is_scholar_key,
    openalex_id_from_folder,
    openalex_id_of,
    parse_folder_name,
    resolve_ref,
    work_title,
    work_year,
    year_part,
)

# Proposal: when metadata later gains an OpenAlex id, ``normalize --apply`` may
# rename ``<key>-doi_…`` / ``arxiv_`` / ``local_`` to ``<key>-W…``. ``save``
# never renames. Set this to False to keep fallback folders where they are.
UPGRADE_FALLBACK_ON_NORMALIZE = True

CANONICAL_FILES = frozenset({
    "paper.pdf",
    "paper.txt",
    "pages.json",
    "metadata.json",
    "README.md",
    "ai_review.md",
    "notes_mark.md",
    "paper.ocr.pdf",
    "excerpts.jsonl",
    "paper.ai-highlights.pdf",
})
# Present only when that artefact exists. Absence is not a finding.
SILENT_OPTIONAL = frozenset({"paper.ocr.pdf", "excerpts.jsonl", "paper.ai-highlights.pdf"})
WORK_WRITABLE = frozenset({"metadata.json", "README.md", "paper.txt", "pages.json", "paper.pdf"})
ROOT_WRITABLE = frozenset({"KEYS.tsv", "INDEX.md"})

STALE_REF = re.compile(r"literature/W\d+")
INDEX_ROW = re.compile(
    r"^\| \[(?P<title>.*)\]\((?P<folder>[^)/]+)/README\.md\) \| (?P<year>[^|]*) \| (?P<status>[^|]*) \| (?P<doi>[^|]*) \|$"
)
SKIP_SCAN = frozenset({".git", ".lumi", "__pycache__", ".cache", "node_modules", ".pytest_cache"})


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    path: str
    message: str

    def render(self):
        return f"{self.level}\t{self.code}\t{self.path}\t{self.message}"


@dataclass(frozen=True)
class Action:
    kind: str
    src: str
    dst: str
    message: str

    def render(self):
        return f"{self.kind}\t{self.src}\t{self.dst}\t{self.message}"


@dataclass(frozen=True)
class Work:
    folder: str
    path: Path
    key: str
    openalex_id: str
    title: str
    year: str
    doi: str
    status: str


def is_owner_note(name):
    """Personal notes. Callers must not write, rename, or delete these files."""
    lower = name.lower()
    return lower == "notes_mark.md" or lower.startswith("notes-") or lower.startswith("notes_")


def folder_status(path):
    if (path / "paper.txt").is_file():
        return "full text"
    if (path / "paper.pdf").is_file():
        return "PDF saved"
    return "metadata only"


def noncanonical_hint(name):
    if is_owner_note(name) or name.startswith("notes"):
        return "owner note, reported only, not moved"
    if "keenable" in name:
        return "non-canonical text, reported only, not moved"
    if name == "paper.refetch.pdf":
        return "refetch copy, reported only, not moved"
    if name in {"paper-highlighted.pdf", "paper-v6-read.pdf"}:
        return "highlight or extra PDF, reported only, not moved"
    if name == "search_notes.md":
        return "search notes, reported only, not moved"
    if name == "verified-excerpts.md":
        return "reported only, not moved"
    return "reported only, not moved"


def sort_works(works):
    def key(work):
        if work.openalex_id.startswith("W") and work.openalex_id[1:].isdigit():
            return (0, int(work.openalex_id[1:]), work.folder)
        return (1, 0, work.folder)
    return sorted(works, key=key)


def keys_text(works):
    lines = ["key\topenalex_id\tfolder\ttitle"]
    for work in sort_works(works):
        title = work.title.replace("\t", " ").replace("\n", " ")
        lines.append(f"{work.key}\t{work.openalex_id}\t{work.folder}\t{title}")
    return "\n".join(lines) + "\n"


def index_text(works):
    lines = [
        "# Literature index",
        "",
        "| Paper | Year | Status | DOI |",
        "|---|---:|---|---|",
    ]
    for work in sort_works(works):
        title = work.title.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| [{title}]({work.folder}/README.md) | {work.year} | {work.status} | {work.doi} |")
    return "\n".join(lines) + "\n"


def fts_query(text):
    terms = [token for token in re.findall(r"\w+", text.lower()) if len(token) > 2]
    return " OR ".join(f'"{term}"' for term in dict.fromkeys(terms)) or '""'


def extract_pdf_text(pdf):
    """``(reading-order pages, layout text)`` via poppler ``pdftotext``. No PyMuPDF."""
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext is not installed")
    reading = _pdftotext(pdf, layout=False)
    layout = _pdftotext(pdf, layout=True)
    if isinstance(layout, list):
        layout = "\f".join(layout)
    return reading, layout


def _pdftotext(pdf, layout):
    command = ["pdftotext", *(["-layout"] if layout else []), str(pdf), "-"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        err = result.stderr.strip().splitlines()
        raise RuntimeError(err[-1] if err else "pdftotext failed")
    if layout:
        return result.stdout
    pages = result.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


def _display_year(work, meta):
    raw = work_year(work) if work else None
    if raw in (None, ""):
        raw = (meta or {}).get("year")
    if raw in (None, ""):
        return "?"
    text = year_part(raw)
    return text if text != "nd" else "?"


class Library:
    def __init__(self, root):
        self.root = Path(root)

    # --- files -----------------------------------------------------------------

    def _ensure_allowed(self, path):
        if is_owner_note(path.name):
            raise PermissionError(f"refusing to write owner notes: {path}")
        try:
            relative = path.resolve().relative_to(self.root.resolve())
        except ValueError as exc:
            raise PermissionError(f"refusing to write outside the library: {path}") from exc
        parts = relative.parts
        if len(parts) == 1 and parts[0] in ROOT_WRITABLE:
            return
        if parts and parts[0] == ".lumi":
            return
        if len(parts) == 2 and parts[1] in WORK_WRITABLE:
            return
        raise PermissionError(f"refusing to write {path}")

    def write_text(self, path, text):
        """Write ``text`` if it differs. Returns True when the file changed."""
        path = Path(path)
        self._ensure_allowed(path)
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return True

    def write_bytes(self, path, data):
        path = Path(path)
        self._ensure_allowed(path)
        if path.is_file() and path.read_bytes() == data:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return True

    def read_meta(self, folder):
        path = Path(folder) / "metadata.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object")
        return data

    def work_dirs(self):
        """Work folders: the new scheme, bare ``W…`` folders, and any directory with metadata."""
        if not self.root.is_dir():
            return []
        found = []
        for child in self.root.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            if parse_folder_name(child.name) or is_legacy_folder(child.name) or (child / "metadata.json").is_file():
                found.append(child)
        return sorted(found, key=lambda path: path.name)

    def load_works(self):
        works = []
        for path in self.work_dirs():
            meta = {}
            meta_path = path / "metadata.json"
            if meta_path.is_file():
                try:
                    meta = self.read_meta(path) or {}
                except (OSError, json.JSONDecodeError, ValueError):
                    meta = {}
            body = meta.get("work") if isinstance(meta.get("work"), dict) else {}
            parsed = parse_folder_name(path.name)
            key = str(meta.get("key") or (parsed.key if parsed else ""))
            openalex = str(meta.get("openalex_id") or openalex_id_of(body) or openalex_id_from_folder(path.name) or "")
            title = work_title(body) or str(meta.get("title") or "")
            doi = doi_url(meta.get("doi") or body.get("doi") or "")
            works.append(Work(
                folder=path.name,
                path=path,
                key=key,
                openalex_id=openalex,
                title=title,
                year=_display_year(body, meta),
                doi=doi,
                status=folder_status(path),
            ))
        return works

    def load_pages(self, folder):
        path = Path(folder) / "pages.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        pages = data.get("pages")
        return pages if isinstance(pages, list) else None

    # --- catalog ---------------------------------------------------------------

    def write_catalog(self):
        """Rewrite ``KEYS.tsv`` and ``INDEX.md`` from the folders. No-op when bytes match."""
        works = self.load_works()
        changed = self.write_text(self.root / "KEYS.tsv", keys_text(works))
        changed = self.write_text(self.root / "INDEX.md", index_text(works)) or changed
        return changed

    def catalog_dirty(self, works=None):
        works = self.load_works() if works is None else works
        keys_path = self.root / "KEYS.tsv"
        index_path = self.root / "INDEX.md"
        if works and (not keys_path.is_file() or not index_path.is_file()):
            return True
        if keys_path.is_file() and keys_path.read_text(encoding="utf-8") != keys_text(works):
            return True
        if index_path.is_file() and index_path.read_text(encoding="utf-8") != index_text(works):
            return True
        return False

    # --- save ------------------------------------------------------------------

    def save_work(self, work, pdf_path=None, pdf_bytes=None):
        """Create ``<key>-<id>`` for a new work. Re-saving the same work does not rename or duplicate.

        An existing folder is left untouched, including ``README.md``, ``paper.pdf``,
        and every notes file. ``KEYS.tsv`` and ``INDEX.md`` are inserted if missing.
        """
        if pdf_bytes is None and pdf_path:
            pdf_bytes = Path(pdf_path).read_bytes()
        pdf_sha = hashlib.sha256(pdf_bytes).hexdigest() if pdf_bytes is not None else ""
        self.root.mkdir(parents=True, exist_ok=True)
        existing = self.match_existing(work, pdf_sha)
        if existing is not None:
            self.write_catalog()
            return existing.name
        taken = self._taken_keys()
        key = cite_key_for_work(work, taken=taken)
        name = folder_name_for(key, work, pdf_sha or None)
        folder = self.root / name
        if folder.exists():
            raise ValueError(f"folder already exists: {name}")
        folder.mkdir(parents=True)
        if pdf_bytes is not None:
            self.write_bytes(folder / "paper.pdf", pdf_bytes)
        meta = {
            "key": key,
            "openalex_id": openalex_id_of(work) or "",
            "doi": bare_doi(work.get("doi")),
            "arxiv_id": arxiv_id_of(work),
            "folder": name,
            "work": work,
            "pdf_sha256": pdf_sha if pdf_bytes is not None else "",
            "text_extraction": "not available",
            "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        self.write_text(folder / "metadata.json", json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        self._write_readme_if_absent(folder, work)
        self.write_catalog()
        return name

    def _write_readme_if_absent(self, folder, work):
        path = folder / "README.md"
        if path.exists():
            return False
        doi = doi_url(work.get("doi")) or "none"
        openalex = work.get("id") or (f"https://openalex.org/{openalex_id_of(work)}" if openalex_id_of(work) else "none")
        names = _author_line(work)
        lines = [
            f"# {work_title(work) or 'Untitled'}",
            "",
            f"- Authors: {names}",
            f"- Year: {work_year(work) if work_year(work) not in (None, '') else '?'}",
            f"- DOI: {doi}",
            f"- OpenAlex: {openalex}",
            f"- PDF source: {'saved as paper.pdf' if (folder / 'paper.pdf').is_file() else 'no open PDF found'}",
            "- Text extraction: not available",
            f"- References indexed: {len(work.get('referenced_works') or [])}",
            f"- Cited by count at retrieval: {work.get('cited_by_count') or 0}",
            "",
            "## Reading notes",
            "",
            "Not read yet. Record claims with page or section, methods, sample, and limitations.",
            "",
        ]
        return self.write_text(path, "\n".join(lines))

    def match_existing(self, work, pdf_sha=""):
        oa = openalex_id_of(work)
        doi = bare_doi(work.get("doi")).lower()
        arxiv = arxiv_id_of(work)
        for path in self.work_dirs():
            parsed = parse_folder_name(path.name)
            meta = {}
            if (path / "metadata.json").is_file():
                try:
                    meta = self.read_meta(path) or {}
                except (OSError, json.JSONDecodeError, ValueError):
                    meta = {}
            body = meta.get("work") if isinstance(meta.get("work"), dict) else {}
            folder_oa = openalex_id_from_folder(path.name) or meta.get("openalex_id") or openalex_id_of(body) or ""
            if oa and folder_oa and folder_oa == oa:
                return path
            folder_doi = bare_doi(meta.get("doi") or body.get("doi")).lower()
            if doi and folder_doi and folder_doi == doi:
                return path
            folder_arxiv = str(meta.get("arxiv_id") or arxiv_id_of(body) or "")
            if arxiv and folder_arxiv and folder_arxiv == arxiv:
                return path
            if pdf_sha and str(meta.get("pdf_sha256") or "") == pdf_sha:
                return path
            if pdf_sha and parsed and parsed.kind == "local" and parsed.ident == pdf_sha[:8]:
                return path
        return None

    def _taken_keys(self):
        taken = set()
        for work in self.load_works():
            if work.key:
                taken.add(work.key)
            parsed = parse_folder_name(work.folder)
            if parsed:
                taken.add(parsed.key)
        for row in self.read_keys():
            if row.get("key"):
                taken.add(row["key"])
        return taken

    def find(self, ref):
        """Folder path for a folder name, OpenAlex id, cite key, or DOI."""
        works = self.load_works()
        hit = resolve_ref([work.folder for work in works], ref, {work.folder: work.key for work in works})
        if hit:
            return self.root / hit
        wanted = bare_doi(ref).lower()
        if wanted:
            hits = [work for work in works if bare_doi(work.doi).lower() == wanted]
            if len(hits) == 1:
                return hits[0].path
            if len(hits) > 1:
                raise ValueError(f"ambiguous DOI {wanted}: {', '.join(work.folder for work in hits)}")
        raise LookupError(f"no work {ref!r}")

    # --- parse and index -------------------------------------------------------

    def parse(self, refs=None):
        """Extract ``paper.txt`` and ``pages.json`` from ``paper.pdf``.

        Legacy ``W…`` folders are included so an old folder can still be parsed.
        ``paper.refetch.pdf`` is not read and not moved. Owner notes are not opened.
        """
        if refs:
            folders = [self.find(ref) for ref in refs]
        else:
            folders = [path for path in self.work_dirs() if (path / "paper.pdf").is_file()]
        messages = []
        for folder in folders:
            pdf = folder / "paper.pdf"
            if not pdf.is_file():
                messages.append(f"{folder.name}: no paper.pdf")
                continue
            try:
                pages, layout = extract_pdf_text(pdf)
            except (OSError, RuntimeError) as exc:
                messages.append(f"{folder.name}: unreadable PDF ({exc})")
                continue
            record = {"source": f"pdftotext (reading order) of {pdf.name}", "pages": pages}
            layout_pages = layout.split("\f")
            if layout_pages and not layout_pages[-1].strip():
                layout_pages.pop()
            if len(layout_pages) == len(pages):
                record["pages_layout"] = layout_pages
            self.write_text(folder / "pages.json", json.dumps(record, ensure_ascii=False) + "\n")
            self.write_text(folder / "paper.txt", layout if layout.endswith("\n") else layout + ("\n" if layout else ""))
            messages.append(f"{folder.name}: {len(pages)} pages")
        if messages:
            self.write_catalog()
        return messages

    def rebuild_index(self):
        database = self.root / ".lumi" / "index.sqlite"
        if database.exists():
            database.unlink()
        connection = self._connect()
        count = 0
        for work in self.load_works():
            pages = self.load_pages(work.path)
            if not pages:
                continue
            for number, text in enumerate(pages, 1):
                connection.execute("INSERT INTO pages VALUES (?, ?, ?)", (work.folder, number, text))
                count += 1
        connection.commit()
        connection.close()
        return count

    def lookup(self, query, limit=10, papers=None, raw=False):
        database = self.root / ".lumi" / "index.sqlite"
        if not database.is_file():
            self.rebuild_index()
        identifiers = None
        if papers:
            identifiers = [self.find(ref).name for ref in papers]
        connection = self._connect()
        sql = "SELECT paper, page, snippet(pages, 2, '[', ']', '…', 40) FROM pages WHERE pages MATCH ?"
        args = [query if raw else fts_query(query)]
        if identifiers:
            sql += f" AND paper IN ({','.join('?' * len(identifiers))})"
            args.extend(identifiers)
        rows = connection.execute(sql + " ORDER BY bm25(pages) LIMIT ?", (*args, limit)).fetchall()
        connection.close()
        return rows

    def _connect(self):
        directory = self.root / ".lumi"
        directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(directory / "index.sqlite")
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(paper UNINDEXED, page UNINDEXED, text, tokenize='porter unicode61')"
        )
        return connection

    # --- check -----------------------------------------------------------------

    def check(self, project_root=None):
        """Read-only report. Does not create directories or rewrite catalog files."""
        findings = []
        if not self.root.is_dir():
            return [Finding("error", "missing-library", str(self.root), "library directory does not exist")]
        works = []
        for child in sorted(self.root.iterdir(), key=lambda path: path.name):
            if not child.is_dir() or child.name.startswith("."):
                continue
            parsed = parse_folder_name(child.name)
            legacy = is_legacy_folder(child.name)
            has_meta = (child / "metadata.json").is_file()
            if not parsed and not legacy and not has_meta:
                findings.append(Finding("warning", "unknown-folder", child.name, "not a work folder, reported only"))
                continue
            findings.extend(self._check_folder(child, parsed, legacy))
            try:
                works.extend(work for work in self.load_works() if work.folder == child.name)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                findings.append(Finding("error", "metadata", f"{child.name}/metadata.json", str(exc)))
        findings.extend(self._check_keys(works))
        findings.extend(self._check_index(works))
        findings.extend(self._stale_refs(Path(project_root) if project_root else self.root.parent))
        order = {"error": 0, "warning": 1, "info": 2}
        return sorted(findings, key=lambda item: (order.get(item.level, 9), item.path, item.code, item.message))

    def _check_folder(self, path, parsed, legacy):
        findings = []
        meta = None
        meta_path = path / "metadata.json"
        if not meta_path.is_file():
            findings.append(Finding("error", "missing", f"{path.name}/metadata.json", "required canonical file"))
        else:
            try:
                meta = self.read_meta(path)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                findings.append(Finding("error", "metadata", f"{path.name}/metadata.json", str(exc)))
        if not (path / "README.md").is_file():
            findings.append(Finding("error", "missing", f"{path.name}/README.md", "required canonical file"))
        if legacy:
            suggestion = self._legacy_target_name(path, meta or {}, set())
            message = f"bare OpenAlex folder needs rename to {suggestion}" if suggestion else "bare OpenAlex folder needs rename"
            findings.append(Finding("error", "legacy-folder", path.name, message))
        if meta and parsed:
            if meta.get("key") and meta.get("key") != parsed.key:
                findings.append(Finding("error", "mismatch", path.name, f"folder key {parsed.key} != metadata key {meta.get('key')}"))
            if meta.get("folder") and meta.get("folder") != path.name:
                findings.append(Finding("error", "mismatch", path.name, f"metadata folder {meta.get('folder')} != directory {path.name}"))
            body = meta.get("work") if isinstance(meta.get("work"), dict) else {}
            meta_oa = str(meta.get("openalex_id") or openalex_id_of(body) or "")
            if parsed.kind == "openalex" and meta_oa and meta_oa != parsed.ident:
                findings.append(Finding("error", "mismatch", path.name, f"folder OpenAlex {parsed.ident} != metadata {meta_oa}"))
            if parsed.kind != "openalex" and meta_oa:
                findings.append(Finding(
                    "warning", "openalex-not-in-folder", path.name,
                    f"metadata OpenAlex {meta_oa} is not in the folder name (proposal: normalize can rename)",
                ))
        status = folder_status(path)
        if status == "metadata only":
            findings.append(Finding("info", "metadata-only", path.name, "no paper.pdf or paper.txt"))
        else:
            if not (path / "paper.pdf").is_file():
                findings.append(Finding("warning", "missing", f"{path.name}/paper.pdf", "text is present without paper.pdf"))
            if (path / "paper.pdf").is_file() and not (path / "paper.txt").is_file():
                findings.append(Finding("warning", "missing", f"{path.name}/paper.txt", "paper.pdf has not been extracted"))
            if not (path / "pages.json").is_file():
                findings.append(Finding("warning", "missing", f"{path.name}/pages.json", "no page index"))
        if not (path / "notes_mark.md").is_file():
            findings.append(Finding("info", "missing-optional", f"{path.name}/notes_mark.md", "owner notes are not created by the library"))
        if not (path / "ai_review.md").is_file():
            findings.append(Finding("info", "missing-optional", f"{path.name}/ai_review.md", "AI review is written only from verified text"))
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            if child.name.startswith("."):
                continue
            if child.is_dir():
                findings.append(Finding("warning", "noncanonical", f"{path.name}/{child.name}/", "directory, reported only, not moved"))
            elif child.name not in CANONICAL_FILES:
                findings.append(Finding("warning", "noncanonical", f"{path.name}/{child.name}", noncanonical_hint(child.name)))
        return findings

    def _check_keys(self, works):
        findings = []
        rows = self.read_keys()
        path = self.root / "KEYS.tsv"
        by_folder = {}
        if not path.is_file():
            if works:
                findings.append(Finding("error", "missing-keys", "KEYS.tsv", "literature/KEYS.tsv is missing"))
            return findings
        if rows and rows[0].get("bad_header"):
            findings.append(Finding("error", "keys-header", "KEYS.tsv", "header must be key, openalex_id, folder, title"))
        seen = set()
        for row in rows:
            if row.get("bad_header"):
                continue
            if row.get("bad"):
                findings.append(Finding("error", "keys-row", "KEYS.tsv", "row does not have 4 tab-separated columns"))
                continue
            folder = row["folder"]
            if folder in seen:
                findings.append(Finding("error", "keys-duplicate", folder, "duplicate KEYS.tsv row"))
            seen.add(folder)
            by_folder[folder] = row
            if not (self.root / folder).is_dir():
                findings.append(Finding("error", "keys-orphan", folder, "KEYS.tsv names a folder that is not on disk"))
        for work in works:
            row = by_folder.get(work.folder)
            if row is None:
                findings.append(Finding("error", "keys-missing", work.folder, "folder is not in KEYS.tsv"))
                continue
            if row["key"] != work.key:
                findings.append(Finding("error", "mismatch", work.folder, f"KEYS key {row['key']} != metadata/folder key {work.key}"))
            if (row["openalex_id"] or "") != (work.openalex_id or ""):
                findings.append(Finding("error", "mismatch", work.folder, f"KEYS OpenAlex {row['openalex_id'] or '∅'} != metadata {work.openalex_id or '∅'}"))
            if " ".join(row["title"].split()) != " ".join(work.title.split()):
                findings.append(Finding("error", "mismatch", work.folder, "KEYS title != metadata title"))
        return findings

    def _check_index(self, works):
        findings = []
        path = self.root / "INDEX.md"
        if not path.is_file():
            if works:
                findings.append(Finding("error", "missing-index", "INDEX.md", "literature/INDEX.md is missing"))
            return findings
        text = path.read_text(encoding="utf-8")
        expected = index_text(works)
        if text == expected:
            return findings
        parsed, errors = parse_index(text)
        for message in errors:
            findings.append(Finding("error", "index", "INDEX.md", message))
        by_folder = {}
        for row in parsed:
            if row["folder"] in by_folder:
                findings.append(Finding("error", "index-duplicate", row["folder"], "duplicate INDEX.md row"))
            by_folder[row["folder"]] = row
        expected_rows = {work.folder: work for work in works}
        for folder, work in expected_rows.items():
            row = by_folder.get(folder)
            if row is None:
                findings.append(Finding("error", "index-missing", folder, "folder is not linked from INDEX.md"))
                continue
            if row["title"] != work.title.replace("|", "\\|"):
                findings.append(Finding("warning", "index-stale", folder, "INDEX title != metadata title"))
            if row["year"] != work.year:
                findings.append(Finding("warning", "index-stale", folder, f"INDEX year {row['year']} != metadata {work.year}"))
            if row["status"] != work.status:
                findings.append(Finding("warning", "index-stale", folder, f"INDEX status {row['status']} != files ({work.status})"))
            if row["doi"] != work.doi:
                findings.append(Finding("warning", "index-stale", folder, "INDEX DOI != metadata DOI"))
        for folder in by_folder:
            if folder not in expected_rows:
                findings.append(Finding("error", "index-orphan", folder, "INDEX.md links a folder that is not on disk"))
        if text != expected and not findings:
            findings.append(Finding("warning", "index-stale", "INDEX.md", "INDEX.md bytes differ from a fresh render"))
        return findings

    def _stale_refs(self, project_root):
        findings = []
        if not project_root.is_dir():
            return findings
        for path in project_root.rglob("*.md"):
            if not path.is_file() or path.is_symlink():
                continue
            if any(part in SKIP_SCAN for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if STALE_REF.search(line):
                    relative = path.relative_to(project_root)
                    findings.append(Finding("warning", "stale-ref", f"{relative}:{number}", "old literature/W…/ path"))
        return findings

    # --- normalize -------------------------------------------------------------

    def plan(self):
        """Rename plan. Does not touch the disk."""
        actions = []
        taken = set()
        for path in self.work_dirs():
            parsed = parse_folder_name(path.name)
            if parsed:
                taken.add(parsed.key)
        ordered = sorted(self.work_dirs(), key=_folder_sort)
        planned = []
        reserved = {path.name for path in self.work_dirs()}
        for path in ordered:
            parsed = parse_folder_name(path.name)
            meta = {}
            if (path / "metadata.json").is_file():
                try:
                    meta = self.read_meta(path) or {}
                except (OSError, json.JSONDecodeError, ValueError):
                    meta = {}
            target = ""
            message = ""
            if is_legacy_folder(path.name):
                target = self._legacy_target_name(path, meta, taken) or ""
                message = "legacy OpenAlex folder" if target else "cannot derive a name for this legacy folder"
                if not target:
                    actions.append(Action("skip", path.name, "", message))
                    continue
            elif parsed and parsed.kind != "openalex" and UPGRADE_FALLBACK_ON_NORMALIZE:
                body = meta.get("work") if isinstance(meta.get("work"), dict) else {}
                oa = str(meta.get("openalex_id") or openalex_id_of(body) or "")
                if oa:
                    target = f"{parsed.key}-{oa}"
                    message = "proposal: fallback folder now has an OpenAlex id"
            if not target or target == path.name:
                continue
            parsed_target = parse_folder_name(target)
            if parsed_target:
                taken.add(parsed_target.key)
            if target in reserved:
                actions.append(Action("conflict", path.name, target, "target exists; not renamed"))
                continue
            reserved.add(target)
            actions.append(Action("rename", path.name, target, message))
            planned.append((path.name, target))
        if planned or self.catalog_dirty():
            actions.append(Action("catalog", "KEYS.tsv", "INDEX.md", "rewrite KEYS.tsv and INDEX.md from folders"))
        return actions

    def apply(self):
        """Apply :meth:`plan`. Refuses to continue if owner-note bytes change."""
        actions = self.plan()
        before = self._note_signature()
        for action in actions:
            if action.kind != "rename":
                continue
            destination = self.root / action.dst
            if destination.exists():
                raise FileExistsError(f"refusing to replace {action.dst}")
            os.rename(self.root / action.src, destination)
            self._patch_identity(destination)
        if any(action.kind in {"rename", "catalog"} for action in actions):
            self.write_catalog()
        after = self._note_signature()
        if before != after:
            raise RuntimeError("owner notes changed during normalize; this is a bug")
        return actions

    def _legacy_target_name(self, path, meta, taken):
        body = meta.get("work") if isinstance(meta.get("work"), dict) else {}
        oa = openalex_id_from_folder(path.name) or str(meta.get("openalex_id") or openalex_id_of(body) or "")
        if not oa:
            return None
        stored = str(meta.get("key") or "")
        used = {key.lower() for key in taken}
        if is_scholar_key(stored):
            key = stored
        else:
            source = body or meta
            if not (source.get("display_name") or source.get("title") or source.get("authorships") or source.get("author")):
                return None
            key = cite_key_for_work(source, taken=taken)
        taken.add(key)
        return f"{key}-{oa}"

    def _patch_identity(self, folder):
        """Set key / folder / openalex_id to the directory name. Leave every other field, and do not touch notes."""
        path = folder / "metadata.json"
        if not path.is_file():
            return
        meta = self.read_meta(folder)
        parsed = parse_folder_name(folder.name)
        if not parsed:
            return
        changed = False
        if meta.get("key") != parsed.key:
            meta["key"] = parsed.key
            changed = True
        if meta.get("folder") != folder.name:
            meta["folder"] = folder.name
            changed = True
        if parsed.kind == "openalex" and meta.get("openalex_id") != parsed.ident:
            meta["openalex_id"] = parsed.ident
            changed = True
        if changed:
            self.write_text(path, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    def _note_signature(self):
        items = []
        if not self.root.is_dir():
            return tuple()
        for folder in self.work_dirs():
            for child in folder.iterdir():
                if child.is_file() and is_owner_note(child.name):
                    items.append((child.name, hashlib.sha256(child.read_bytes()).hexdigest()))
        return tuple(sorted(items))

    def read_keys(self):
        path = self.root / "KEYS.tsv"
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return []
        rows = []
        if lines[0].split("\t") != ["key", "openalex_id", "folder", "title"]:
            rows.append({"bad_header": True})
        for line in lines[1:]:
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 4:
                rows.append({"bad": True, "raw": line})
                continue
            rows.append({"key": parts[0], "openalex_id": parts[1], "folder": parts[2], "title": parts[3]})
        return rows


def _author_line(work):
    names = []
    for item in (work.get("authorships") or [])[:8]:
        if isinstance(item, str):
            names.append(item)
            continue
        author = item.get("author") or {}
        if isinstance(author, str):
            names.append(author)
        elif isinstance(author, dict):
            names.append(author.get("display_name") or "")
    names = [name for name in names if name]
    return ", ".join(names) if names else "unknown"


def _folder_sort(path):
    oa = openalex_id_from_folder(path.name)
    if oa and oa[1:].isdigit():
        return (0, int(oa[1:]), path.name)
    return (1, 0, path.name)


def parse_index(text):
    rows = []
    errors = []
    lines = text.splitlines()
    if len(lines) < 4 or lines[0] != "# Literature index" or lines[2] != "| Paper | Year | Status | DOI |" or lines[3] != "|---|---:|---|---|":
        errors.append("INDEX.md header is not the literature table")
    for line in lines[4:]:
        if not line.strip():
            continue
        match = INDEX_ROW.match(line)
        if not match:
            errors.append(f"unparsed INDEX row: {line[:80]}")
            continue
        rows.append({key: value.strip() for key, value in match.groupdict().items()})
    return rows, errors


def report(findings):
    counts = {"error": 0, "warning": 0, "info": 0}
    lines = []
    for finding in findings:
        counts[finding.level] = counts.get(finding.level, 0) + 1
        lines.append(finding.render())
    lines.append(f"{counts.get('error', 0)} errors, {counts.get('warning', 0)} warnings, {counts.get('info', 0)} info")
    return "\n".join(lines) + "\n"


def check_status(findings):
    """1 when a person should look (errors or warnings). Info alone stays 0."""
    return 1 if any(finding.level in {"error", "warning"} for finding in findings) else 0
