"""Search APIs. Every search is logged to field/search_log.jsonl; unseen works become candidates."""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .project import append_jsonl, today

OPENALEX = "https://api.openalex.org"
ARXIV = "https://export.arxiv.org/api/query"
LENS = "https://api.lens.org/scholarly/search"
CROSSREF = "https://api.crossref.org/works"
S2 = "https://api.semanticscholar.org/graph/v1/paper/search"
CORE = "https://api.core.ac.uk/v3/search/works"
ATOM = {"a": "http://www.w3.org/2005/Atom"}
USER_AGENT = "lumi/1.0 (academic personal use)"
NET_ERRORS = (OSError, ValueError, KeyError, urllib.error.URLError, ET.ParseError)


def get_json(url, headers=None):
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    key = os.environ.get("OPENALEX_API_KEY")
    if key and parts.netloc == "api.openalex.org" and not any(name == "api_key" for name, _ in query):
        query.append(("api_key", key))
    url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def work_id(raw):
    raw = raw.strip()
    if re.fullmatch(r"W\d+", raw, re.I):
        return raw.upper()
    if "openalex.org/" in raw:
        match = re.search(r"W\d+", raw, re.I)
        if match:
            return match.group().upper()
    raw = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", raw, flags=re.I)
    if re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", raw):
        raw = "10.48550/arXiv." + re.sub(r"v\d+$", "", raw)
    if raw.lower().startswith("arxiv:"):
        raw = "10.48550/" + raw
    if raw.startswith("10."):
        return "https://doi.org/" + raw
    raise ValueError("ожидался OpenAlex ID, DOI или arXiv ID")


def fetch_work(raw):
    native = provider_id(raw)
    if native:
        provider, identifier = native.split(":", 1)
        if provider == "s2":
            key = os.environ.get("S2_API_KEY")
            params = urllib.parse.urlencode({"fields": "title,year,citationCount,externalIds,authors,abstract,openAccessPdf"})
            item = get_json(f"https://api.semanticscholar.org/graph/v1/paper/{identifier}?{params}",
                            {"x-api-key": key} if key else None)
            return s2_row(item)["work"]
        if provider == "core":
            key = required_key("CORE_API_KEY")
            return core_row(get_json(f"https://api.core.ac.uk/v3/works/{identifier}", {"Authorization": f"Bearer {key}"}))["work"]
        data = lens_request({"query": {"term": {"lens_id": identifier}}, "size": 1})
        items = data.get("data") or []
        if not items:
            raise ValueError(f"нет работы {native} в Lens")
        return lens_row(items[0])["work"]
    return get_json(f"{OPENALEX}/works/{urllib.parse.quote(work_id(raw), safe=':/.')}")


def fetch_works(ids):
    """OpenAlex records for up to 50 W-ids per request."""
    out = []
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        params = urllib.parse.urlencode({"filter": "openalex:" + "|".join(chunk), "per_page": len(chunk)})
        out += get_json(f"{OPENALEX}/works?{params}").get("results", [])
    return out


def title(work):
    return " ".join((work.get("display_name") or work.get("title") or "Untitled").split())


def short_id(work):
    return work["id"].rsplit("/", 1)[-1]


# --- logging and candidates ----------------------------------------------------

def library_ids(project):
    """Folder names plus the OpenAlex id encoded in ``<key>-W…`` or bare ``W…`` folders."""
    from lumi.naming import openalex_id_from_folder
    folders = list(project.paper_ids())
    found = set(folders)
    for name in folders:
        openalex = openalex_id_from_folder(name)
        if openalex:
            found.add(openalex)
    for folder in folders:
        work = json.loads((project.papers / folder / "metadata.json").read_text(encoding="utf-8")).get("work") or {}
        if work.get("id"):
            found.add(provider_id(work["id"]) or short_id(work))
    return found


def record_search(project, db, query, rows, question=None):
    """rows: [{id, doi, title, year}] (id may be a DOI or arXiv id for non-OpenAlex databases)."""
    folders = list(project.paper_ids())
    library = library_ids(project)
    library_dois = {d for d in (doi_of(project, w) for w in folders) if d}
    candidates = project.candidates()
    known = {provider_id(c["id"]) or c["id"] for c in candidates} | {(c.get("doi") or "").lower().replace("https://doi.org/", "") for c in candidates if c.get("doi")}
    new = 0
    for row in rows:
        doi = (row.get("doi") or "").lower().replace("https://doi.org/", "")
        row["status"] = "library" if (provider_id(row["id"]) or row["id"]) in library or (doi and doi in library_dois) else ("candidate" if (provider_id(row["id"]) or row["id"]) in known or (doi and doi in known) else "new")
        if row["status"] == "new":
            new += 1
            candidates.append({"id": row["id"], "doi": doi or None, "title": row.get("title"), "year": row.get("year"), "question": question, "db": db, "source_query": query, "date": today(), "decision": None, **({"work": row["work"]} if row.get("work") else {})})
            known.add(provider_id(row["id"]) or row["id"])
            if doi:
                known.add(doi)
    project.save_candidates(candidates)
    append_jsonl(project.field / "search_log.jsonl", {"date": today(), "db": db, "query": query, "question": question, "found": len(rows), "new": new,
                  "stop": getattr(rows, "stop", "complete"), "error": getattr(rows, "error", None),
                  "requested": getattr(rows, "requested", None), "requests": getattr(rows, "requests", None)})
    return rows


def doi_of(project, identifier):
    path = project.papers / identifier / "metadata.json"
    if not path.exists():
        return None
    return (json.loads(path.read_text(encoding="utf-8"))["work"].get("doi") or "").lower().replace("https://doi.org/", "") or None


MARK = {"library": "★ в библиотеке", "candidate": "· уже кандидат", "new": "+ новый кандидат"}


def print_rows(rows):
    for r in rows:
        print(f"{r['id']} | {r.get('year') or '?'} | {r.get('cites', 0)} цит. | {r.get('title')}  [{MARK.get(r.get('status'), '')}]")
        if r.get("extra"):
            print(f"  {r['extra']}")


# --- databases ---------------------------------------------------------------------

class SearchResults(list):
    """Rows and explicit completion information; never confuse failure with exhaustion."""
    def __init__(self, requested):
        super().__init__()
        self.requested = requested
        self.requests = 0
        self.stop = "exhausted"
        self.error = None


def collect_pages(fetch, limit, page_size=100, start=None, delay=0, stable_cursor=False):
    """fetch(cursor, size) -> (rows, next_cursor). Preserve pages on later failure."""
    result = SearchResults(limit)
    cursor, seen_cursors, seen_ids = start, set(), set()
    while len(result) < limit:
        try:
            if cursor in seen_cursors and not stable_cursor:
                raise ValueError("API повторил курсор; продолжение остановлено")
            seen_cursors.add(cursor)
            if result.requests and delay:
                time.sleep(delay)
            result.requests += 1
            batch, next_cursor = fetch(cursor, min(page_size, limit - len(result)))
            previous = len(result)
            for row in batch:
                identity = row["id"]
                if not identity:
                    raise ValueError("API вернул работу без идентификатора")
                if identity not in seen_ids:
                    result.append(row)
                    seen_ids.add(identity)
                    if len(result) == limit:
                        break
            if len(result) == limit:
                result.stop = "limit"
                break
            if next_cursor is None:
                break
            if len(result) == previous:
                raise ValueError("API не продвинул выдачу; продолжение остановлено")
            cursor = next_cursor
        except NET_ERRORS as exc:
            from .onboarding import redact
            result.stop, result.error = "partial", redact(str(exc))
            break
    return result


def result_status(rows):
    if getattr(rows, "error", None):
        print(f"Неполный результат: {len(rows)} работ; {rows.error}")
        return 1
    if isinstance(rows, SearchResults):
        reason = "достигнут заданный --limit" if rows.stop == "limit" else "выдача API исчерпана"
        print(f"Поиск: {len(rows)} работ, запросов {rows.requests}; {reason}")
    return 0


def oa_row(work):
    return {"id": short_id(work), "doi": work.get("doi"), "title": title(work),
            "year": work.get("publication_year"), "cites": work.get("cited_by_count") or 0,
            "extra": work.get("doi") or ""}


def openalex_pages(params, limit):
    def page(cursor, size):
        data = get_json(f"{OPENALEX}/works?{urllib.parse.urlencode({**params, 'per_page': size, 'cursor': cursor})}")
        rows = [oa_row(w) for w in data.get("results", [])]
        return rows, (data.get("meta") or {}).get("next_cursor") if rows else None
    return collect_pages(page, limit, start="*")


def openalex(query, limit):
    return openalex_pages({"search": query}, limit)


def arxiv(query, limit):
    def page(offset, size):
        params = urllib.parse.urlencode({"search_query": query, "start": offset, "max_results": size, "sortBy": "relevance"})
        request = urllib.request.Request(f"{ARXIV}?{params}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            feed = ET.fromstring(response.read())
        rows = []
        for entry in feed.findall("a:entry", ATOM):
            arxiv_id = re.sub(r"v\d+$", "", entry.findtext("a:id", "", ATOM).rsplit("/abs/", 1)[-1])
            if arxiv_id.endswith("api/errors") or "api/errors#" in arxiv_id:
                raise ValueError(entry.findtext("a:summary", "arXiv error", ATOM))
            names = [x.findtext("a:name", "", ATOM) for x in entry.findall("a:author", ATOM)]
            rows.append({"id": arxiv_id, "doi": f"10.48550/arxiv.{arxiv_id}", "title": " ".join(entry.findtext("a:title", "", ATOM).split()), "year": entry.findtext("a:published", "", ATOM)[:4], "extra": ", ".join(names[:5]) + (" et al." if len(names) > 5 else "")})
        return rows, offset + len(rows) if len(rows) == size else None
    return collect_pages(page, limit, start=0, delay=3)


def crossref(query, limit):
    def page(cursor, size):
        params = {"query": query, "rows": size, "cursor": cursor}
        if os.environ.get("UNPAYWALL_EMAIL"):
            params["mailto"] = os.environ["UNPAYWALL_EMAIL"]
        data = get_json(f"{CROSSREF}?{urllib.parse.urlencode(params)}")["message"]
        rows = []
        for item in data.get("items", []):
            year = ((item.get("issued") or {}).get("date-parts") or [[None]])[0][0]
            rows.append({"id": item.get("DOI"), "doi": item.get("DOI"), "title": " ".join(item.get("title") or ["?"]), "year": year, "cites": item.get("is-referenced-by-count") or 0, "extra": " / ".join(item.get("container-title") or [])})
        return rows, data.get("next-cursor") if len(rows) == size else None
    return collect_pages(page, limit, start="*")


def provider_id(raw):
    raw = str(raw).strip()
    if re.fullmatch(r"(?:core|lens|s2):[A-Za-z0-9_-]+", raw, re.I):
        name, identifier = raw.split(":", 1)
        return name.lower() + ":" + (identifier.lower() if name.lower() == "s2" else identifier)
    if re.fullmatch(r"[0-9a-f]{40}", raw, re.I):
        return "s2:" + raw.lower()
    return None


def provider_work(provider, identifier, title_, year, authors, doi=None, abstract=None, pdf=None, cites=0, original=None):
    """Common bibliographic shape, with native identity and unchanged source record."""
    work = {"id": f"{provider}:{identifier}", "display_name": title_, "publication_year": year,
            "doi": doi, "abstract_text": abstract or "", "cited_by_count": cites or 0,
            "authorships": [{"author": {"display_name": name}} for name in authors if name],
            "provider": provider, "provider_record": original or {}}
    if pdf:
        work["best_oa_location"] = {"is_oa": True, "pdf_url": pdf}
    return work


def provider_row(work):
    return {"id": work.get("doi") or work["id"], "doi": work.get("doi"), "title": work["display_name"],
            "year": work.get("publication_year"), "cites": work.get("cited_by_count") or 0, "work": work}


def s2_row(paper):
    ids = paper.get("externalIds") or {}
    doi = ids.get("DOI") or (f"10.48550/arxiv.{ids['ArXiv']}" if ids.get("ArXiv") else None)
    work = provider_work("s2", paper["paperId"], paper.get("title"), paper.get("year"),
                         [a.get("name") for a in paper.get("authors") or []], doi, paper.get("abstract"),
                         (paper.get("openAccessPdf") or {}).get("url"), paper.get("citationCount"), paper)
    return provider_row(work)


def semantic_scholar(query, limit):
    key = os.environ.get("S2_API_KEY")
    def page(offset, size):
        if offset >= 1000:
            raise ValueError("Semantic Scholar relevance API ограничен 1000 результатами; уточните запрос или выберите другой коннектор")
        params = urllib.parse.urlencode({"query": query, "offset": offset, "limit": min(size, 1000-offset),
                                        "fields": "title,year,citationCount,externalIds,authors,abstract,openAccessPdf"})
        data = get_json(f"{S2}?{params}", {"x-api-key": key} if key else None)
        rows = [s2_row(p) for p in data.get("data") or []]
        next_offset = data.get("next")
        if next_offset is None and offset + len(rows) < (data.get("total") or 0) and rows:
            next_offset = offset + len(rows)
        return rows, next_offset
    return collect_pages(page, limit, start=0)


def required_key(name):
    key = os.environ.get(name)
    if not key:
        raise ValueError(f"нужен {name}; используйте lumi setup")
    return key


def core_row(item):
    work = provider_work("core", item["id"], item.get("title"), item.get("yearPublished"),
                         [a.get("name") if isinstance(a, dict) else a for a in item.get("authors") or []],
                         item.get("doi"), item.get("abstract"), item.get("downloadUrl"), original=item)
    return provider_row(work)


def core(query, limit):
    key = required_key("CORE_API_KEY")
    def page(offset, size):
        data = get_json(f"{CORE}?{urllib.parse.urlencode({'q': query, 'limit': size, 'offset': offset})}", {"Authorization": f"Bearer {key}"})
        rows = [core_row(w) for w in data.get("results") or []]
        more = len(rows) == size
        if data.get("totalHits") is not None:
            more = offset + len(rows) < data["totalHits"] and bool(rows)
        return rows, offset + len(rows) if more else None
    return collect_pages(page, limit, start=0)


def lens_request(body):
    token = required_key("LENS_TOKEN")
    request = urllib.request.Request(LENS, data=json.dumps(body).encode(), headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return {} if response.status == 204 else json.load(response)


def lens_row(item):
    ids = {x.get("type"): x.get("value") for x in item.get("external_ids") or []}
    authors = [" ".join(filter(None, [a.get("first_name"), a.get("last_name")])) or a.get("display_name", "") for a in item.get("authors") or []]
    work = provider_work("lens", item["lens_id"], item.get("title"), item.get("year_published"), authors,
                         ids.get("doi"), item.get("abstract"), cites=item.get("scholarly_citations_count"), original=item)
    return provider_row(work)


def lens(query, limit):
    required_key("LENS_TOKEN")
    def page(cursor, size):
        body = {"scroll_id": cursor, "scroll": "1m"} if cursor else {"query": query, "size": size, "scroll": "1m"}
        data = lens_request(body)
        rows = [lens_row(w) for w in data.get("data") or []]
        return rows, data.get("scroll_id") if rows else None
    return collect_pages(page, limit, stable_cursor=True)


DATABASES = {"openalex": openalex, "arxiv": arxiv, "crossref": crossref, "s2": semantic_scholar, "core": core, "lens": lens}


def search(project, db, query, limit, question=None):
    rows = DATABASES[db](query, limit)
    return record_search(project, db, query, rows, question)


def citations(project, raw, limit, question=None):
    """Direct neighbors. The caller's limit is explicit; no hidden reference cap."""
    work = fetch_work(raw)
    identifier = short_id(work)
    if not re.fullmatch(r"W\d+", identifier):
        if work.get("doi"):
            work = fetch_work(work["doi"])
            identifier = short_id(work)
        else:
            raise ValueError("обход цитирований требует OpenAlex ID или DOI; нативный источник сохранён, но этот обход для него недоступен")
    print(f"# {title(work)} ({identifier})")
    citing = openalex_pages({"filter": f"cites:{identifier}", "sort": "cited_by_count:desc"}, limit)
    print("\nЦитируют (по числу цитирований):")
    print_rows(record_search(project, "openalex:cited_by", identifier, citing, question))
    failed = result_status(citing)
    refs = list(dict.fromkeys(r.rsplit("/", 1)[-1] for r in (work.get("referenced_works") or [])))
    referenced = [oa_row(w) for w in fetch_works(refs)] if refs else []
    referenced.sort(key=lambda r: -r["cites"])
    print(f"\nСсылается ({len(refs)} в OpenAlex, получено {len(referenced)}, показаны {min(limit, len(referenced))} самых цитируемых; --limit {limit}):")
    print_rows(record_search(project, "openalex:references", identifier, referenced[:limit], question))
    if len(referenced) < len(refs):
        print(f"Неполный результат: API не вернул {len(refs) - len(referenced)} ссылок")
        failed = 1
    return failed
