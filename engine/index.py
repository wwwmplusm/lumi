"""FTS5 full-text index over parsed pages and claims (rebuilt from files, never edited by hand)."""

import re
import sqlite3

from . import papers
from .text import snippet


def connect(project):
    project.cache.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(project.index_db)
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(paper UNINDEXED, page UNINDEXED, text, tokenize='porter unicode61')")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS claims USING fts5(id UNINDEXED, paper UNINDEXED, statement, tokenize='porter unicode61')")
    return db


def build(project, quiet=False):
    db = connect(project)
    db.execute("DELETE FROM pages")
    count = 0
    for identifier in project.paper_ids():
        for number, text in enumerate(papers.load_pages(project, identifier) or [], 1):
            db.execute("INSERT INTO pages VALUES (?, ?, ?)", (identifier, number, text))
            count += 1
    db.execute("DELETE FROM claims")
    for claim in project.claims():
        paper = (claim.get("evidence") or [{}])[0].get("paper")
        db.execute("INSERT INTO claims VALUES (?, ?, ?)", (claim["id"], paper, claim["statement"]))
    db.commit()
    db.close()
    if not quiet:
        print(f"индекс: {count} стр.")


def fts_query(text, mode="OR"):
    """Free text -> quoted-term query (FTS5 syntax-safe)."""
    terms = [t for t in re.findall(r"\w+", text.lower()) if len(t) > 2]
    return f" {mode} ".join(f'"{t}"' for t in dict.fromkeys(terms)) or '""'


def ensure(project):
    """Build the index on first use (it lives in .cache/, outside git)."""
    db = connect(project)
    empty = db.execute("SELECT count(*) FROM pages").fetchone()[0] == 0
    db.close()
    if empty and any((project.papers / i / "pages.json").exists() for i in project.paper_ids()):
        build(project, quiet=True)


def search_pages(project, query, limit=10, paper_ids=None):
    ensure(project)
    db = connect(project)
    sql = "SELECT paper, page, snippet(pages, 2, '[', ']', '…', 40) FROM pages WHERE pages MATCH ?"
    args = [query]
    if paper_ids:
        sql += f" AND paper IN ({','.join('?' * len(paper_ids))})"
        args += paper_ids
    rows = db.execute(sql + " ORDER BY bm25(pages) LIMIT ?", (*args, limit)).fetchall()
    db.close()
    return rows


def lookup(project, text, limit=10, refs=None, raw=False):
    """Print matching pages as `[Gao 2026, с. 112] «…»`; returns rows (paper, pdf page, snippet)."""
    ids = [papers.resolve(project, r) for r in refs] if refs else None
    rows = search_pages(project, text if raw else fts_query(text), limit, ids)
    metas = {}
    for paper, page, text_ in rows:
        meta = metas.setdefault(paper, papers.load_meta(project, paper))
        print(f"[{papers.cite_name(meta)}, {papers.page_ref(meta, page)}] «{snippet(text_)}»  · {paper} p{page}")
    return rows


def show(project, ref, page=None, pdf_page=None):
    identifier = papers.resolve(project, ref)
    meta = papers.load_meta(project, identifier)
    pages = papers.load_pages(project, identifier)
    if not pages:
        raise ValueError(f"{papers.label(meta)}: нет текста (fulltext={meta.get('fulltext')})")
    number = pdf_page or (papers.pdf_page_of(meta, page) if page is not None else None)
    if number is None and page is not None and str(page).isdigit() and not meta.get("page_labels"):
        number = int(page)
    if number is None or not 1 <= number <= len(pages):
        raise ValueError(f"нет страницы {page or pdf_page}; страницы PDF 1..{len(pages)}" + (f", печатные {meta['page_labels'][0]}..{meta['page_labels'][-1]}" if meta.get("page_labels") else ""))
    print(f"# {papers.label(meta)} — {papers.page_ref(meta, number)} (PDF-с. {number})\n")
    print(pages[number - 1])
