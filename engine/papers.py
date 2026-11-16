"""Papers: metadata, human labels (key, cite, label), printed page numbers, add and parse."""

import json
import re
import shutil
import subprocess
from collections import Counter

from . import sources
from .project import today

ARTICLE_STARTS = 3  # pages without a number allowed before the article starts (cover, front matter)


# --- metadata ---------------------------------------------------------------------

def meta_path(project, identifier):
    return project.papers / identifier / "metadata.json"


def load_meta(project, identifier):
    path = meta_path(project, identifier)
    if not path.exists():
        return None
    meta = json.loads(path.read_text(encoding="utf-8"))
    from lumi.naming import parse_folder_name
    parsed = parse_folder_name(identifier)
    if parsed:
        meta.setdefault("key", parsed.key)
    meta["fulltext"] = fulltext_status(project, identifier, meta)
    meta.setdefault("page_source", "none")
    return meta


def save_meta(project, identifier, meta):
    path = meta_path(project, identifier)
    from lumi.library import Library
    Library(project.papers).write_text(path, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")


def all_meta(project):
    return {i: load_meta(project, i) for i in project.paper_ids()}


def abstract(work):
    if work.get("abstract_text"):
        return work["abstract_text"]
    index = work.get("abstract_inverted_index") or {}
    words = sorted((pos, word) for word, places in index.items() for pos in places)
    return " ".join(w for _, w in words)


def venue(work):
    return ((work.get("primary_location") or {}).get("source") or {}).get("display_name") or ""


def is_preprint(work):
    doi = (work.get("doi") or "").lower()
    return "10.48550/arxiv" in doi or "arxiv" in venue(work).lower() or work.get("type") == "preprint"


# --- human labels -------------------------------------------------------------------

def surnames(work):
    out = []
    for a in work.get("authorships") or []:
        name = " ".join(((a.get("author") or {}).get("display_name") or "").split())
        if name:
            out.append(name.split()[-1])
    return out or ["Anon"]


def year_part(meta):
    """'2026' or '2026b' — the year as it appears in citations (suffix from the key)."""
    match = re.search(r"(\d{4}|nd)([a-z]?)$", meta.get("key") or "")
    return match.group(1) + match.group(2) if match else str(meta["work"].get("publication_year") or "nd")


def cite_name(meta):
    """'Gao 2026' — what goes into [Gao 2026, с. 112]."""
    return f"{surnames(meta['work'])[0]} {year_part(meta)}"


def authors_short(meta):
    names = surnames(meta["work"])
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} и {names[1]}"
    return f"{names[0]} и др."


def short_title(work, words=6):
    full = sources.title(work)
    head = full.split(":")[0] if len(full.split(":")[0].split()) >= 3 else full
    parts = head.split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words or head != full else "")


def label(meta):
    """'Gao и др. 2026 «Chinese AI research after sanctions»'."""
    return f"{authors_short(meta)} {year_part(meta)} «{short_title(meta['work'])}»"


def resolve(project, ref):
    """Folder name for a W-id, a ``<key>-W…`` folder, a cite key, or 'Gao 2026'.

    Bare ``W…`` folders still resolve. A folder named ``pu2026sanctions-W7203724392``
    also resolves from ``W7203724392`` or ``pu2026sanctions``.
    """
    from lumi.naming import resolve_ref
    names = project.paper_ids()
    keys = {identifier: (meta.get("key") or "") for identifier, meta in all_meta(project).items()}
    found = resolve_ref(names, ref, keys)
    if not found:
        from lumi.library import Library
        try:
            found = Library(project.papers).find(ref).name
        except LookupError:
            pass
    if not found:
        normalized = re.sub(r"\s+", "", ref).casefold()
        matches = [name for name, meta in all_meta(project).items()
                   if re.sub(r"\s+", "", cite_name(meta)).casefold() == normalized]
        if len(matches) > 1:
            raise ValueError(f"неоднозначная ссылка «{ref}»: {', '.join(matches)}; используйте ключ или DOI")
        found = matches[0] if matches else None
    if not found:
        raise ValueError(f"нет статьи «{ref}» в библиотеке")
    return found


# --- printed page numbers -----------------------------------------------------------

def printed(meta, pdf_page):
    labels = meta.get("page_labels")
    if labels and 1 <= pdf_page <= len(labels) and labels[pdf_page - 1]:
        return labels[pdf_page - 1]
    return None


def page_ref(meta, pdf_page):
    """'с. 112' when the printed number is known, otherwise 'PDF-с. 5' (honest: not a journal page)."""
    label_ = printed(meta, pdf_page)
    return f"с. {label_}" if label_ else f"PDF-с. {pdf_page}"


def pdf_page_of(meta, printed_label):
    labels = meta.get("page_labels") or []
    if str(printed_label) in labels:
        return labels.index(str(printed_label)) + 1
    return None


def roman(n, upper=False):
    out = ""
    for value, sym in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= value:
            out += sym
            n -= value
    return out.upper() if upper else out


def qpdf_labels(pdf, count):
    """Printed labels from the PDF's /PageLabels tree; None if absent or just 1..N."""
    if not shutil.which("qpdf"):
        return None
    result = subprocess.run(["qpdf", "--json", "--json-key=pagelabels", str(pdf)], capture_output=True, text=True)
    try:
        ranges = json.loads(result.stdout).get("pagelabels") or []
    except json.JSONDecodeError:
        return None
    if not ranges:
        return None
    labels = []
    ranges = sorted(ranges, key=lambda r: r["index"])
    for k, r in enumerate(ranges):
        end = ranges[k + 1]["index"] if k + 1 < len(ranges) else count
        spec = r.get("label") or {}
        style, start = spec.get("/S"), int(spec.get("/St") or 1)
        prefix = re.sub(r"^u:", "", str(spec.get("/P") or ""))
        for j in range(end - r["index"]):
            n = start + j
            body = {"/D": str(n), "/r": roman(n), "/R": roman(n, True), "/a": chr(96 + (n - 1) % 26 + 1) * ((n - 1) // 26 + 1), "/A": chr(64 + (n - 1) % 26 + 1) * ((n - 1) // 26 + 1)}.get(style, "")
            labels.append(prefix + body)
    labels = labels[:count] + [""] * (count - len(labels))
    return None if labels == [str(i + 1) for i in range(count)] else labels


HEADER_NUMBER = re.compile(r"(?<![\w.,/:-])(\d{1,4})(?![\w.,/%:-])")


def header_offset(layout_pages):
    """Mode of (number in first/last 3 lines − PDF index); accepted when ≥60% of pages agree."""
    votes = Counter()
    for i, text in enumerate(layout_pages, 1):
        lines = [x for x in text.splitlines() if x.strip()]
        found = {int(m) - i for line in lines[:3] + lines[-3:] for m in HEADER_NUMBER.findall(line)}
        votes.update(found)
    if len(layout_pages) < 3 or not votes:
        return None
    offset, count = votes.most_common(1)[0]
    return offset if count >= max(3, 0.6 * (len(layout_pages) - 1)) else None


def metadata_offset(work, count):
    """first_page−1 from OpenAlex when its page range fits the PDF (article numbers like e0123 are skipped)."""
    biblio = work.get("biblio") or {}
    try:
        first, last = int(biblio.get("first_page")), int(biblio.get("last_page"))
    except (TypeError, ValueError):
        return None
    if not first < last or abs((last - first + 1) - count) > ARTICLE_STARTS:
        return None
    return first - 1 - max(0, count - (last - first + 1))


def detect_pages(meta, pdf, layout_pages):
    """Set page_labels + page_source: pagelabels → header (checked against metadata) → metadata → preprint → none."""
    count = len(layout_pages)
    from_offset = lambda off: [str(i + off) if i + off > 0 else "" for i in range(1, count + 1)]
    labels = qpdf_labels(pdf, count) if pdf else None
    meta_off = metadata_offset(meta["work"], count)
    head_off = header_offset(layout_pages)
    if labels:
        meta.update(page_labels=labels, page_source="pagelabels", page_note="")
    elif head_off is not None:
        note = "" if meta_off in (None, head_off) else f"метаданные дают сдвиг {meta_off}, колонтитулы {head_off}"
        meta.update(page_labels=from_offset(head_off), page_source="header", page_note=note)
    elif meta_off is not None:
        meta.update(page_labels=from_offset(meta_off), page_source="metadata", page_note="по метаданным, колонтитулами не подтверждено")
    elif is_preprint(meta["work"]):
        meta.update(page_labels=from_offset(0), page_source="preprint", page_note="препринт: страницы PDF и есть страницы")
    else:
        meta.update(page_labels=None, page_source="none", page_note="печатные страницы не найдены — ссылки идут на страницы PDF")
    return meta


def set_pages_manual(project, identifier, first_printed, at_pdf_page=1):
    meta = load_meta(project, identifier)
    pages = load_pages(project, identifier, layout=True) or load_pages(project, identifier) or []
    offset = int(first_printed) - int(at_pdf_page)
    meta.update(page_labels=[str(i + offset) if i + offset > 0 else "" for i in range(1, len(pages) + 1)], page_source="manual", page_note=f"вручную {today()}")
    save_meta(project, identifier, meta)
    return meta


# --- pages ------------------------------------------------------------------------------

def pdf_file(project, identifier):
    folder = project.papers / identifier
    return selected_pdf(folder)


def selected_pdf(folder):
    """Respect the recorded text version, including already OCRed originals."""
    path = folder / "metadata.json"
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    selected = meta.get("text_source_pdf")
    if selected:
        if selected not in ("paper.pdf", "paper.ocr.pdf", "paper.refetch.pdf") or not (folder / selected).is_file():
            raise ValueError(f"{folder.name}: выбранный text_source_pdf отсутствует или не поддерживается: {selected}")
        return folder / selected
    for name in ("paper.ocr.pdf", "paper.refetch.pdf", "paper.pdf"):
        if (folder / name).exists():
            return folder / name
    return None


def pdf_ok(path):
    """A downloaded PDF must open and have pages (catches truncated files that start with %PDF-)."""
    if not shutil.which("pdfinfo"):
        return True
    result = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True)
    match = re.search(r"^Pages:\s+(\d+)", result.stdout, re.M)
    return result.returncode == 0 and match and int(match.group(1)) > 0


def load_pages(project, identifier, layout=False):
    path = project.papers / identifier / "pages.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("pages_layout" if layout else "pages")


def fulltext_status(project, identifier, meta):
    from lumi.library import extraction_status
    status = extraction_status(load_pages(project, identifier))
    if status != "none":
        return status
    return "abstract_only" if abstract(meta["work"]) else "none"


# --- add ----------------------------------------------------------------------------------

def pdf_candidates(work):
    locations = [work.get("best_oa_location"), work.get("primary_location"), *(work.get("locations") or [])]
    seen = set()
    for location in locations:
        if not location or not location.get("is_oa"):
            continue
        url = location.get("pdf_url")
        if url and url not in seen:
            seen.add(url)
            yield url
    match = re.search(r"arxiv\.(\d{4}\.\d{4,5})(?:v\d+)?", (work.get("doi") or "").lower())
    if match and f"https://arxiv.org/pdf/{match.group(1)}" not in seen:
        yield f"https://arxiv.org/pdf/{match.group(1)}"
    yield from unpaywall_pdfs(work.get("doi") or "", seen)


def unpaywall_pdfs(doi, seen):
    import os
    import urllib.parse
    email = os.environ.get("UNPAYWALL_EMAIL")
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    if not email or not doi:
        return
    data = sources.get_json(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='/')}?" + urllib.parse.urlencode({"email": email}))
    for location in data.get("oa_locations") or []:
        url = location.get("url_for_pdf")
        if url and url not in seen:
            seen.add(url)
            yield url


def download_pdf(url, destination):
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": sources.USER_AGENT, "Accept": "application/pdf"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read(40_000_001)
    if len(content) > 40_000_000 or not content.startswith(b"%PDF-"):
        raise ValueError("ответ не PDF или больше 40 МБ")
    destination.write_bytes(content)
    if not pdf_ok(destination):
        destination.unlink()
        raise ValueError("PDF обрезан или не открывается")


def add(project, raw, question=None):
    """Use the existing catalog and preserve hand-edited cards and text versions."""
    from lumi.library import Library
    from . import index
    library = Library(project.papers)
    try:
        identifier = resolve(project, raw)
    except ValueError:
        candidate = next((row for row in project.candidates()
                          if row["id"] == raw or (row.get("doi") or "").lower() == raw.lower()
                          or (sources.provider_id(raw) and sources.provider_id(row["id"]) == sources.provider_id(raw))), None)
        # Search already returned bibliographic metadata and provider provenance.
        # Do not require the same work to also exist in OpenAlex.
        work = candidate.get("work") if candidate else None
        if not work:
            work = sources.fetch_work(raw)
        identifier = library.save_work(work)
    folder = project.papers / identifier
    meta = load_meta(project, identifier)
    work = meta["work"]
    if question:
        meta.setdefault("questions", [])
        if question not in meta["questions"]:
            meta["questions"].append(question)
    if not pdf_file(project, identifier):
        errors = []
        try:
            for url in pdf_candidates(work):
                try:
                    download_pdf(url, folder / "paper.pdf")
                    meta["pdf_source"] = url
                    break
                except sources.NET_ERRORS as exc:
                    errors.append(f"{url}: {exc}")
        except sources.NET_ERRORS as exc:
            errors.append(f"поиск открытой копии: {exc}")
        from .onboarding import redact
        meta.setdefault("pdf_errors", []).extend(redact(error) for error in errors)
        if not (folder / "paper.pdf").exists():
            meta["download_status"] = "failed" if errors else "no_open_copy"
        else:
            meta["download_status"] = "saved"
    save_meta(project, identifier, meta)
    # Re-adding a paper must not replace verified/OCR text already in use.
    if not load_pages(project, identifier):
        parsed = "; ".join(library.parse([identifier]))
    else:
        parsed = "сохранён существующий текст"
    mark_candidate(project, identifier, work, "include")
    library.write_catalog()
    index.build(project)
    meta = load_meta(project, identifier)
    print(f"{label(meta)} · {meta.get('key')} · {identifier}")
    print(f"  текст: {meta['fulltext']} · {parsed}")
    if meta["fulltext"] not in ("yes", "partial"):
        from .onboarding import redact
        for error in meta.get("pdf_errors") or []:
            print(f"  загрузка: {redact(error)}")
        if meta.get("download_status") == "no_open_copy":
            print("  загрузка: открытая копия не найдена; сохранены метаданные")
    return identifier


def mark_candidate(project, identifier, work, decision, why=""):
    doi = (work.get("doi") or "").lower().replace("https://doi.org/", "")
    rows = project.candidates()
    for row in rows:
        native = sources.provider_id(work.get("id", ""))
        if (row["id"] in (identifier, sources.short_id(work))
                or (native and sources.provider_id(row["id"]) == native)
                or (doi and (row.get("doi") or "").lower().replace("https://doi.org/", "") == doi)):
            row.update(decision=decision, decided=today(), why=why or row.get("why", ""), library_id=identifier)
    project.save_candidates(rows)


def parse(project, ids=None):
    from lumi.library import Library
    library = Library(project.papers)
    for message in library.parse(ids):
        print(message)
    return 1 if library.parse_errors else 0
