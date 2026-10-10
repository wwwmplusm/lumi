"""Paper cards: sources/papers/<W>/README.md — a one-screen summary to read before the paper.

Header comes from metadata (code); the body from a worker. Sections `## Заметки` / `## Мои заметки`
are the user's and survive regeneration. A card is a guide, not a source of citations.
"""

import json
import re

from . import papers, workers
from .claims import paper_text
from .project import today
from .text import snippet

KEEP = re.compile(r"^## (?:Мои заметки|Заметки)\b.*", re.M | re.S)
BASIS = {"yes": "полный текст", "abstract_only": "только аннотация", "none": "нет текста"}


def card_path(project, identifier):
    return project.papers / identifier / "README.md"


def kept_notes(project, identifier):
    path = card_path(project, identifier)
    if not path.exists():
        return ""
    match = KEEP.search(path.read_text(encoding="utf-8"))
    return match.group().rstrip() + "\n" if match else ""


def page_span(meta, count):
    labels = [x for x in (meta.get("page_labels") or []) if x]
    return f"с. {labels[0]}–{labels[-1]}" if labels else f"{count} стр. PDF"


def header(project, identifier, meta):
    work = meta["work"]
    doi = (work.get("doi") or "").replace("https://doi.org/", "")
    kind = "препринт" if papers.is_preprint(work) else (papers.venue(work) or "?")
    lines = [f"# {papers.authors_short(meta)} {papers.year_part(meta)} — {papers.sources.title(work)}",
             f"{meta.get('key')} · {identifier}" + (f" · doi:{doi}" if doi else "") + f" · {kind} · цитирований: {work.get('cited_by_count') or 0}"]
    basis = BASIS.get(meta.get("fulltext"), "нет текста")
    if meta.get("fulltext") == "yes":
        basis += f", {page_span(meta, len(papers.load_pages(project, identifier) or []))}"
        if meta.get("page_source") in ("none", "metadata"):
            basis += f" ({meta.get('page_note')})"
    lines.append(f"Основа: {basis}")
    return "\n".join(lines) + "\n"


def write_stub(project, identifier):
    """Header + abstract until a worker writes the card. Does not overwrite a written card."""
    path = card_path(project, identifier)
    if path.exists() and "<!-- card:" in path.read_text(encoding="utf-8"):
        return
    meta = papers.load_meta(project, identifier)
    body = [header(project, identifier, meta), f"Карточка ещё не написана: `eurema card {meta.get('key')}`.\n"]
    text = papers.abstract(meta["work"])
    if text:
        body.append(f"**Аннотация (OpenAlex).** {text}\n")
    notes = kept_notes(project, identifier)
    path.write_text("\n".join(body) + ("\n" + notes if notes else ""), encoding="utf-8")


def render(project, identifier, meta, card, basis):
    def para(name, value):
        return f"**{name}.** {value.strip() if value and value.strip() else 'нет в источнике'}\n"
    results = []
    labels = meta.get("page_labels")
    count = len(papers.load_pages(project, identifier) or [])
    exists = lambda p: (labels and p in labels) or (not labels and p.isdigit() and 1 <= int(p) <= count)
    for item in card.get("results") or []:
        page = re.sub(r"\s*[-–—]\s*", "–", str(item.get("page") or "").strip())
        if basis == "abstract":
            where = " (аннотация)"
        elif page and all(exists(p) for p in page.split("–")):
            where = f" ({'с.' if labels else 'PDF-с.'} {page})"
        else:
            where = " (с. ?)"
        results.append(f"- {item.get('text', '').strip()}{where}")
    lines = [header(project, identifier, meta),
             para("Вопрос и цель", card.get("aim")),
             para("Данные и метод", card.get("data_method")),
             "**Главные результаты / аргументы.**\n" + ("\n".join(results) if results else "нет в источнике") + "\n",
             para("Ограничения", card.get("limitations")),
             para("Для нас", card.get("for_us")),
             f"<!-- card: {today()}, {'полный текст' if basis == 'fulltext' else 'аннотация'}, воркер. Путеводитель: цитировать только проверенные факты. -->\n"]
    notes = kept_notes(project, identifier)
    return "\n".join(lines) + ("\n" + notes if notes else "")


def generate(project, refs, force=False):
    questions = {q["id"]: q["question"] for q in project.questions()}
    for ref in refs:
        identifier = papers.resolve(project, ref)
        meta = papers.load_meta(project, identifier)
        path = card_path(project, identifier)
        basis = "fulltext" if meta.get("fulltext") == "yes" else "abstract"
        done = path.exists() and re.search(r"<!-- card: [\d-]+, (полный текст|аннотация)", path.read_text(encoding="utf-8"))
        if done and not force and (done.group(1) == "полный текст" or basis == "abstract"):
            print(f"{papers.label(meta)}: карточка уже есть (--force, чтобы переписать)")
            continue
        abstract = papers.abstract(meta["work"])
        if basis == "abstract" and not abstract:
            write_stub(project, identifier)
            print(f"{papers.label(meta)}: нет ни текста, ни аннотации — только шапка")
            continue
        payload = json.dumps({"paper": {"title": papers.sources.title(meta["work"]), "authors": [a.get("author", {}).get("display_name") for a in meta["work"].get("authorships") or []][:8],
                                        "year": meta["work"].get("publication_year"), "venue": papers.venue(meta["work"])},
                              "basis": basis, "project_context": project.config.get("context") or "", "questions": questions}, ensure_ascii=False)
        payload += "\n\n" + (paper_text(project, identifier) if basis == "fulltext" else f"=== ABSTRACT ===\n{abstract}")
        parsed, record = workers.run(project, "card", f"Write the card for paper {identifier} ({basis}).", payload, f"card_{identifier}")
        if not parsed:
            print(f"{papers.label(meta)}: воркер не справился ({record.get('error', '')[:120]})")
            continue
        text = render(project, identifier, meta, parsed, basis)
        path.write_text(text, encoding="utf-8")
        bad = text.count("(с. ?)")
        print(f"{papers.label(meta)}: карточка ({'полный текст' if basis == 'fulltext' else 'аннотация'}), ${record.get('cost_usd') or 0:.3f}" + (f"; {bad} ссылок на несуществующие страницы" if bad else ""))


def check_cards(project):
    """Problems for lint: missing card, basis line that disagrees with metadata."""
    problems = []
    for identifier, meta in papers.all_meta(project).items():
        path = card_path(project, identifier)
        if not path.exists():
            problems.append(f"{papers.label(meta)}: нет карточки")
            continue
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^Основа: (.+)$", text, re.M)
        expected = BASIS.get(meta.get("fulltext"), "нет текста")
        if not match or not match.group(1).startswith(expected):
            problems.append(f"{papers.label(meta)}: строка «Основа» не совпадает с текстом ({expected})")
        card = re.search(r"<!-- card: [\d-]+, (полный текст|аннотация)", text)
        if card and card.group(1) == "аннотация" and meta.get("fulltext") == "yes":
            problems.append(f"{papers.label(meta)}: карточка по аннотации, а полный текст уже есть — `eurema card {meta.get('key')} --force`")
    return problems


def preview(project, identifier):
    path = card_path(project, identifier)
    return snippet(path.read_text(encoding="utf-8"), 400) if path.exists() else ""
