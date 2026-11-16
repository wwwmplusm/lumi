"""Quote verification ladder: FOUND → FOUND* → SEGMENTS (accepted) · DRIFT · ABSENT · SHORT (rejected).

FOUND     exact (after normalization) on the page, or across a page break
FOUND*    two parts in order: across a page break with a running header between, or inside a page with only a
          footnote number/symbol between (never words — an unmarked omission is not a quote)
SEGMENTS  the quote has … / [...] cuts; every piece found in order, close together
DRIFT     ≥80% similar but not exact — rejected; the real text is shown so it can be corrected
ABSENT    not in this paper (the library is searched for it, to catch a wrong paper)
"""

import difflib
import re

from . import index, papers
from .text import normalize, normalize_map, snippet

MIN_LEN = 20
HALF_GAP = 12          # FOUND*: gap inside a page — only footnote numbers and symbols, no words (no silent omissions)
BREAK_GAP = 250        # FOUND*: gap allowed when it crosses a page break (running header)
SEGMENT_SPAN = 1500    # SEGMENTS: max distance between consecutive pieces
DRIFT_MIN = 0.80
ACCEPTED = {"FOUND", "FOUND*", "SEGMENTS"}
ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:\.\.\.|…)\s*\]|\.\.\.|…)\s*")


def page_order(count, hint):
    order = list(range(1, count + 1))
    if hint in order:
        near = [p for p in (hint, hint - 1, hint + 1) if p in order]
        order = near + [p for p in order if p not in near]
    return order


def windows(pages):
    """Per PDF page: normalized text of the page joined with the next one, and where the page ends."""
    norm = [normalize(p) for p in pages]
    out = {}
    for i in range(1, len(pages) + 1):
        joined = norm[i - 1] + (" " + norm[i] if i < len(pages) else "")
        out[i] = (joined, len(norm[i - 1]))
    return out


def find_exact(needle, text, end):
    pos = text.find(needle)
    return pos if 0 <= pos < end else None


def find_halves(needle, text, end):
    """Two parts of the quote, split at any word boundary, in order with a small gap (a header or footnote
    line inside the sentence). Returns (start, inserted text) or None."""
    words = needle.split(" ")
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        if len(a) < 15 or len(b) < 15:
            continue
        start = 0
        while True:
            pos = text.find(a, start)
            if pos < 0 or pos >= end:
                break
            after = pos + len(a)
            nxt = text.find(b, after)
            if nxt > after:
                gap = nxt - after
                inserted = text[after:nxt]
                if (gap <= HALF_GAP and not re.search(r"[^\W\d_]{3,}", inserted)) or (gap <= BREAK_GAP and after <= end <= nxt):
                    return pos, text[after:nxt].strip()
            start = pos + 1
    return None


def find_segments(pieces, text, end):
    """All pieces in order, each within SEGMENT_SPAN of the previous; at least one piece ≥15 chars.
    Returns (start, stop) in normalized text, or None."""
    if not pieces or max(len(p) for p in pieces) < 15:
        return None
    start = 0
    while True:
        pos = text.find(pieces[0], start)
        if pos < 0 or pos >= end:
            return None
        cursor, ok = pos + len(pieces[0]), True
        for piece in pieces[1:]:
            nxt = text.find(piece, cursor)
            if nxt < 0 or nxt - cursor > SEGMENT_SPAN:
                ok = False
                break
            cursor = nxt + len(piece)
        if ok:
            return pos, cursor
        start = pos + 1


def best_drift(needle, pages, order):
    """(ratio, pdf page, original text) of the most similar passage."""
    best = (0.0, None, "")
    for number in order:
        original = pages[number - 1]
        text, positions = normalize_map(original)
        if not text:
            continue
        match = difflib.SequenceMatcher(None, text, needle, autojunk=False).find_longest_match(0, len(text), 0, len(needle))
        if match.size < 8:
            continue
        anchor = match.a - match.b
        for shift in range(-20, 21, 5):
            s = max(0, anchor + shift)
            window = text[s:s + len(needle)]
            ratio = difflib.SequenceMatcher(None, needle, window, autojunk=False).ratio()
            if ratio > best[0] and window:
                e = min(len(positions) - 1, s + len(window) - 1)
                best = (ratio, number, original[positions[s]:positions[e] + 1])
    return best


def original_span(pages, number, start, length):
    """Original (not normalized) text of a match that starts on page `number` and may run onto the next page."""
    joined = pages[number - 1] + ("\n" + pages[number] if number < len(pages) else "")
    text, positions = normalize_map(joined)
    end = min(len(positions) - 1, start + length - 1)
    return joined[positions[min(start, len(positions) - 1)]:positions[end] + 1] if positions else ""


def locate_in(pages, quote, hint=None):
    """Ladder over one page list. Returns a result dict (paper filled in by the caller)."""
    pieces = [normalize(p) for p in ELLIPSIS.split(quote) if p.strip()]
    needle = " ".join(pieces)
    if len(needle) < MIN_LEN:
        return {"verdict": "SHORT", "note": f"цитата короче {MIN_LEN} знаков — не проверяется"}
    win = windows(pages)
    order = page_order(len(pages), hint)
    if len(pieces) == 1:
        for number in order:
            pos = find_exact(needle, *win[number])
            if pos is not None:
                return {"verdict": "FOUND", "page": number, "found": original_span(pages, number, pos, len(needle))}
        for number in order:
            hit = find_halves(needle, *win[number])
            if hit:
                return {"verdict": "FOUND*", "page": number, "found": original_span(pages, number, hit[0], len(needle) + len(hit[1]) + 2), "note": f"внутри цитаты вставка «{snippet(hit[1], 80)}»"}
    else:
        for number in order:
            hit = find_segments(pieces, *win[number])
            if hit:
                return {"verdict": "SEGMENTS", "page": number, "found": original_span(pages, number, hit[0], hit[1] - hit[0]), "note": f"кусков: {len(pieces)}, пропуски на месте «…»"}
    ratio, number, text = best_drift(needle, pages, order)
    if ratio >= DRIFT_MIN and len(needle) >= 40:
        return {"verdict": "DRIFT", "page": number, "found": text, "score": round(ratio, 2), "note": "похоже, но не дословно — исправьте цитату по тексту источника"}
    return {"verdict": "ABSENT", "score": round(ratio, 2)}


def locate(project, identifier, quote, hint=None, search_library=True):
    """Ladder on reading-order text, then -layout text (tables, formulas), then the rest of the library."""
    pages = papers.load_pages(project, identifier)
    if not pages:
        return {"verdict": "NO_TEXT", "paper": identifier, "note": "у статьи нет полного текста — цитату проверить нельзя"}
    result = locate_in(pages, quote, hint)
    result.update(paper=identifier, version="reading")
    if result["verdict"] not in ACCEPTED and result["verdict"] != "SHORT":
        layout = papers.load_pages(project, identifier, layout=True)
        if layout:
            alt = locate_in(layout, quote, hint)
            if alt["verdict"] in ACCEPTED or (alt["verdict"] == "DRIFT" and result["verdict"] == "ABSENT"):
                result = {**alt, "paper": identifier, "version": "layout"}
    if result["verdict"] == "ABSENT" and search_library:
        elsewhere = find_elsewhere(project, identifier, quote)
        if elsewhere:
            result["note"] = f"есть в другой статье: {elsewhere}"
    return result


def find_elsewhere(project, identifier, quote):
    """A quote that is absent from the cited paper may be in another one (wrong attribution)."""
    terms = sorted(set(re.findall(r"\w{5,}", quote.lower())), key=len, reverse=True)[:6]
    if not terms:
        return None
    try:
        rows = index.search_pages(project, " AND ".join(f'"{t}"' for t in terms), 5)
    except Exception:
        return None
    for paper, page, _ in rows:
        if paper == identifier:
            continue
        found = locate_in(papers.load_pages(project, paper) or [], quote, page)
        if found["verdict"] in ACCEPTED:
            meta = papers.load_meta(project, paper)
            return f"[{papers.cite_name(meta)}, {papers.page_ref(meta, found['page'])}]"
    return None


# --- citations in free text -------------------------------------------------------------

QUOTED = re.compile(r'(?:«(?P<a>[^»]{10,}?)»|“(?P<b>[^”]{10,}?)”|"(?P<c>[^"\n]{10,}?)")\s*[—–-]?\s*[\[(](?P<cite>[^\])]{3,100})[\])]')
CITE = re.compile(r"^(?P<who>.+?)[,;]?\s*(?P<pdf>PDF-)?(?:с\.|c\.|стр\.|p\.|pp\.|s\.)\s*(?P<page>[\w]+)", re.I)
WHO = re.compile(r"^(?P<surname>[^\W\d][\w'’-]*).*?(?P<year>\d{4}|nd)(?P<suffix>[a-z]?)\b")


def parse_cite(project, cite):
    """'Gao и др. 2026, с. 112' / 'Gao2026 p.5' / 'W123, PDF-с. 4' -> (W-id, pdf page hint, printed label or None)."""
    match = CITE.match(cite.strip())
    if not match:
        raise ValueError(f"не понял ссылку [{cite}] — нужен вид [Автор Год, с. N]")
    who = match.group("who").strip()
    try:
        identifier = papers.resolve(project, who)
    except ValueError:
        w = WHO.match(who)
        if not w:
            raise
        identifier = papers.resolve(project, f"{w.group('surname')}{w.group('year')}{w.group('suffix')}")
    meta = papers.load_meta(project, identifier)
    page = match.group("page")
    if match.group("pdf"):
        return identifier, int(page), None
    pdf_page = papers.pdf_page_of(meta, page)
    if pdf_page is None and not meta.get("page_labels") and page.isdigit():
        pdf_page = int(page)  # no printed numbers known: the reference is to the PDF page
    return identifier, pdf_page, page


def check_one(project, ref, quote, page=None, pdf_page=None):
    identifier = papers.resolve(project, ref)
    meta = papers.load_meta(project, identifier)
    hint = pdf_page or (papers.pdf_page_of(meta, page) if page else None)
    if page is not None and hint is None and not meta.get("page_labels") and str(page).isdigit():
        hint = int(page)
    result = locate(project, identifier, quote, hint)
    result["cited_page"] = f"с. {page}" if page else (f"PDF-с. {pdf_page}" if pdf_page else None)
    if result["verdict"] in ACCEPTED and (page is not None or pdf_page is not None) and (hint is None or result["page"] != hint):
        result["page_wrong"] = True
    return result


def check_text(project, text):
    results = []
    for match in QUOTED.finditer(text):
        quote = match.group("a") or match.group("b") or match.group("c")
        try:
            identifier, hint, printed_label = parse_cite(project, match.group("cite"))
        except ValueError as exc:
            results.append({"verdict": "BAD_CITE", "quote": quote, "cite": match.group("cite"), "note": str(exc)})
            continue
        result = locate(project, identifier, quote, hint)
        result.update(quote=quote, cite=match.group("cite"), cited_page=f"с. {printed_label}" if printed_label else f"PDF-с. {hint}")
        if result["verdict"] in ACCEPTED and (hint is None or result["page"] != hint):
            result["page_wrong"] = True
        results.append(result)
    return results


def ok(result):
    return result["verdict"] in ACCEPTED and not result.get("page_wrong")


def report(project, result):
    """One human line per checked quote."""
    meta = papers.load_meta(project, result["paper"]) if result.get("paper") else None
    where = f"[{papers.cite_name(meta)}, {papers.page_ref(meta, result['page'])}]" if meta and result.get("page") else (f"[{papers.cite_name(meta)}]" if meta else f"[{result.get('cite', '?')}]")
    mark = "✓" if ok(result) else "✗"
    line = f"{mark} {result['verdict']} {where}"
    if result.get("quote"):
        line += f" «{snippet(result['quote'], 120)}»"
    if result.get("page_wrong"):
        line += f"\n    страница не та: указано {result.get('cited_page') or result.get('cite')}, цитата на {papers.page_ref(meta, result['page'])}"
    if result.get("note"):
        line += f"\n    {result['note']}"
    if result["verdict"] in ("DRIFT", "FOUND*", "SEGMENTS") and result.get("found"):
        line += f"\n    в источнике: «{snippet(result['found'], 300)}»"
    if result.get("version") == "layout" and result["verdict"] in ACCEPTED:
        line += "\n    найдено в тексте с сохранённой вёрсткой (таблица/формула)"
    return line
