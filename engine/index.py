"""Human-readable search and page display over the library's single index."""

from lumi.library import Library
from . import papers
from .text import snippet


def search_pages(project, query, limit=10, paper_ids=None):
    return Library(project.papers).lookup(query, limit, paper_ids, raw=True)


def lookup(project, text, limit=10, refs=None, raw=False):
    """Print matching pages as `[Gao 2026, с. 112] «…»`; returns rows (paper, pdf page, snippet)."""
    library = project if isinstance(project, Library) else Library(project.papers)
    rows = library.lookup(text, limit, refs, raw=raw)
    metas = {}
    for paper, page, text_ in rows:
        if paper not in metas:
            meta = library.read_meta(library.root / paper)
            metas[paper] = meta
        meta = metas[paper]
        print(f"[{papers.cite_name(meta)}, {papers.page_ref(meta, page)}] «{snippet(text_)}»  · {paper} p{page}")
    return rows


def show(project, ref, page=None, pdf_page=None):
    identifier = papers.resolve(project, ref)
    meta = papers.load_meta(project, identifier)
    pages = papers.load_pages(project, identifier)
    if not pages:
        raise ValueError(f"{papers.label(meta)}: нет текста (fulltext={papers.fulltext_status(project, identifier, meta)})")
    number = pdf_page or (papers.pdf_page_of(meta, page) if page is not None else None)
    if number is None and page is not None and str(page).isdigit() and not meta.get("page_labels"):
        number = int(page)
    if number is None or not 1 <= number <= len(pages):
        raise ValueError(f"нет страницы {page or pdf_page}; страницы PDF 1..{len(pages)}" + (f", печатные {meta['page_labels'][0]}..{meta['page_labels'][-1]}" if meta.get("page_labels") else ""))
    print(f"# {papers.label(meta)} — {papers.page_ref(meta, number)} (PDF-с. {number})\n")
    print(pages[number - 1])
