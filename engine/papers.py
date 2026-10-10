"""Papers: metadata, human labels (key, cite, label), printed page numbers, add and parse."""

import datetime as dt
import json
import re
import shutil
import subprocess
from collections import Counter

from . import sources
from .project import today
from .text import ascii_fold

ARTICLE_STARTS = 3  # pages without a number allowed before the article starts (cover, front matter)


# --- metadata ---------------------------------------------------------------------

def meta_path(project, identifier):
    return project.papers / identifier / "metadata.json"


def load_meta(project, identifier):
    path = meta_path(project, identifier)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_meta(project, identifier, meta):
    path = meta_path(project, identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def all_meta(project):
    return {i: load_meta(project, i) for i in project.paper_ids()}


def abstract(work):
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


def make_key(work, taken):
    base = re.sub(r"[^A-Za-z]", "", ascii_fold(surnames(work)[0])) or "Anon"
    base = base[0].upper() + base[1:]
    stem = f"{base}{work.get('publication_year') or 'nd'}"
    if stem not in taken:
        return stem
    for letter in "bcdefghijklmnopqrstuvwxyz":
        if stem + letter not in taken:
            return stem + letter
    raise ValueError(f"слишком много ключей {stem}")


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
    """W-id, key (Gao2026) or 'Gao 2026' -> W-id."""
    ref = ref.strip()
    if re.fullmatch(r"W\d+", ref, re.I) and meta_path(project, ref.upper()).exists():
        return ref.upper()
    wanted = re.sub(r"[^a-z0-9]", "", ascii_fold(ref).lower())
    for identifier, meta in all_meta(project).items():
        if (meta.get("key") or "").lower() == wanted:
            return identifier
    raise ValueError(f"нет статьи «{ref}» в библиотеке")


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
    for name in ("paper.refetch.pdf", "paper.pdf"):  # refetch replaces a truncated original
        if (folder / name).exists():
            return folder / name
    return None


def pdf_pages(pdf, layout=False):
    """Per-page text via pdftotext; pages split on form feeds."""
    result = subprocess.run(["pdftotext", *(["-layout"] if layout else []), str(pdf), "-"], capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "pdftotext не смог прочитать")
    pages = result.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


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
    if load_pages(project, identifier):
        return "yes"
    return "abstract_only" if abstract(meta["work"]) else "none"


def parse_one(project, identifier):
    meta = load_meta(project, identifier)
    pdf = pdf_file(project, identifier)
    message = None
    if pdf:
        try:
            pages = pdf_pages(pdf)
            if sum(len(x) for x in pages) < 1000:
                message = f"нет текстового слоя ({len(pages)} стр.)"
            else:
                layout = pdf_pages(pdf, layout=True)
                record = {"source": f"pdftotext (reading order) of {pdf.name}", "pages": pages}
                if len(layout) == len(pages):
                    record["pages_layout"] = layout
                (project.papers / identifier / "pages.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
                detect_pages(meta, pdf, layout if len(layout) == len(pages) else pages)
                message = f"{len(pages)} стр., печатные страницы: {meta['page_source']}" + (f" ({meta['page_note']})" if meta.get("page_note") else "")
        except ValueError as exc:
            message = f"PDF не читается ({exc})"
            meta["text_extraction"] = f"failed: {exc}"
    meta["fulltext"] = fulltext_status(project, identifier, meta)
    save_meta(project, identifier, meta)
    return message or "нет PDF"


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
    try:
        data = sources.get_json(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='/')}?" + urllib.parse.urlencode({"email": email}))
    except sources.NET_ERRORS:
        return
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
    from . import cards, index
    work = sources.fetch_work(raw)
    identifier = sources.short_id(work)
    folder = project.papers / identifier
    folder.mkdir(parents=True, exist_ok=True)
    meta = load_meta(project, identifier) or {}
    taken = {m.get("key") for i, m in all_meta(project).items() if i != identifier}
    meta.update(retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat(), work=work)
    meta.setdefault("key", make_key(work, taken))
    meta.setdefault("questions", [])
    if question and question not in meta["questions"]:
        meta["questions"].append(question)
    errors = []
    if not pdf_file(project, identifier):
        for url in pdf_candidates(work):
            try:
                download_pdf(url, folder / "paper.pdf")
                meta["pdf_source"] = url
                break
            except sources.NET_ERRORS as exc:
                errors.append(f"{url}: {exc}")
    meta["pdf_errors"] = errors
    meta.setdefault("page_source", "none")
    save_meta(project, identifier, meta)
    parsed = parse_one(project, identifier)
    meta = load_meta(project, identifier)
    cards.write_stub(project, identifier)
    mark_candidate(project, identifier, work, "include")
    index.build(project)
    print(f"{label(meta)} · {meta['key']} · {identifier}")
    print(f"  текст: {meta['fulltext']} · {parsed}")
    if meta["fulltext"] != "yes":
        print("  полного текста нет — статью нельзя считать прочитанной")
    return identifier


def mark_candidate(project, identifier, work, decision, why=""):
    doi = (work.get("doi") or "").lower().replace("https://doi.org/", "")
    rows = project.candidates()
    for row in rows:
        if row["id"] == identifier or (doi and (row.get("doi") or "").lower() == doi):
            row.update(decision=decision, decided=today(), why=why or row.get("why", ""), library_id=identifier)
    project.save_candidates(rows)


def parse(project, ids=None):
    from . import index
    ids = ids or [i for i in project.paper_ids() if pdf_file(project, i)]
    for identifier in ids:
        print(f"{identifier}: {parse_one(project, identifier)}")
    index.build(project)
