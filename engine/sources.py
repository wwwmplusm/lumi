"""Search APIs. Every search is logged to field/search_log.jsonl; unseen works become candidates."""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .project import append_jsonl, today

OPENALEX = "https://api.openalex.org"
ARXIV = "https://export.arxiv.org/api/query"
ORCID = "https://pub.orcid.org/v3.0"
LENS = "https://api.lens.org/scholarly/search"
CROSSREF = "https://api.crossref.org/works"
S2 = "https://api.semanticscholar.org/graph/v1/paper/search"
CORE = "https://api.core.ac.uk/v3/search/works"
PATENTSVIEW = "https://search.patentsview.org/api/v1/patent/"
WORLDBANK = "https://api.worldbank.org/v2"
ATOM = {"a": "http://www.w3.org/2005/Atom"}
USER_AGENT = "eurema/1.0 (academic personal use)"
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

def record_search(project, db, query, rows, question=None):
    """rows: [{id, doi, title, year}] (id may be a DOI or arXiv id for non-OpenAlex databases)."""
    library = set(project.paper_ids())
    library_dois = {d for d in (doi_of(project, w) for w in library) if d}
    candidates = project.candidates()
    known = {c["id"] for c in candidates} | {(c.get("doi") or "").lower() for c in candidates if c.get("doi")}
    new = 0
    for row in rows:
        doi = (row.get("doi") or "").lower().replace("https://doi.org/", "")
        row["status"] = "library" if row["id"] in library or (doi and doi in library_dois) else ("candidate" if row["id"] in known or (doi and doi in known) else "new")
        if row["status"] == "new":
            new += 1
            candidates.append({"id": row["id"], "doi": doi or None, "title": row.get("title"), "year": row.get("year"), "question": question, "db": db, "source_query": query, "date": today(), "decision": None})
            known.add(row["id"])
    project.save_candidates(candidates)
    append_jsonl(project.field / "search_log.jsonl", {"date": today(), "db": db, "query": query, "question": question, "found": len(rows), "new": new})
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

def openalex(query, limit):
    data = get_json(f"{OPENALEX}/works?{urllib.parse.urlencode({'search': query, 'per_page': limit})}")
    return [{"id": short_id(w), "doi": w.get("doi"), "title": title(w), "year": w.get("publication_year"), "cites": w.get("cited_by_count") or 0, "extra": w.get("doi") or ""} for w in data.get("results", [])]


def arxiv(query, limit):
    params = urllib.parse.urlencode({"search_query": query, "max_results": limit, "sortBy": "relevance"})
    request = urllib.request.Request(f"{ARXIV}?{params}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        feed = ET.fromstring(response.read())
    rows = []
    for entry in feed.findall("a:entry", ATOM):
        arxiv_id = re.sub(r"v\d+$", "", entry.findtext("a:id", "", ATOM).rsplit("/abs/", 1)[-1])
        names = [x.findtext("a:name", "", ATOM) for x in entry.findall("a:author", ATOM)]
        rows.append({"id": arxiv_id, "doi": f"10.48550/arxiv.{arxiv_id}", "title": " ".join(entry.findtext("a:title", "", ATOM).split()), "year": entry.findtext("a:published", "", ATOM)[:4], "extra": ", ".join(names[:5]) + (" et al." if len(names) > 5 else "")})
    return rows


def crossref(query, limit):
    params = {"query": query, "rows": limit}
    if os.environ.get("UNPAYWALL_EMAIL"):
        params["mailto"] = os.environ["UNPAYWALL_EMAIL"]
    rows = []
    for item in get_json(f"{CROSSREF}?{urllib.parse.urlencode(params)}")["message"].get("items", []):
        year = ((item.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        rows.append({"id": item.get("DOI"), "doi": item.get("DOI"), "title": " ".join(item.get("title") or ["?"]), "year": year, "cites": item.get("is-referenced-by-count") or 0, "extra": " / ".join(item.get("container-title") or [])})
    return rows


def semantic_scholar(query, limit):
    params = urllib.parse.urlencode({"query": query, "limit": limit, "fields": "title,year,citationCount,externalIds"})
    key = os.environ.get("S2_API_KEY")
    rows = []
    for paper in get_json(f"{S2}?{params}", {"x-api-key": key} if key else None).get("data") or []:
        ids = paper.get("externalIds") or {}
        doi = ids.get("DOI") or (f"10.48550/arxiv.{ids['ArXiv']}" if ids.get("ArXiv") else None)
        rows.append({"id": doi or paper["paperId"], "doi": doi, "title": paper.get("title"), "year": paper.get("year"), "cites": paper.get("citationCount") or 0})
    return rows


def core(query, limit):
    key = os.environ.get("CORE_API_KEY")
    if not key:
        raise ValueError("нужен CORE_API_KEY (бесплатно: https://core.ac.uk/services/api)")
    rows = []
    for work in get_json(f"{CORE}?{urllib.parse.urlencode({'q': query, 'limit': limit})}", {"Authorization": f"Bearer {key}"}).get("results") or []:
        rows.append({"id": work.get("doi") or f"core:{work.get('id')}", "doi": work.get("doi"), "title": work.get("title"), "year": work.get("yearPublished"), "extra": work.get("downloadUrl") or "нет полного текста"})
    return rows


def lens(query, limit):
    token = os.environ.get("LENS_TOKEN")
    if not token:
        raise ValueError("нужен LENS_TOKEN (https://www.lens.org/lens/user/subscriptions)")
    body = json.dumps({"query": query, "size": limit, "include": ["lens_id", "title", "year_published", "external_ids", "scholarly_citations_count", "patent_citations_count"]}).encode()
    request = urllib.request.Request(LENS, data=body, headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    rows = []
    for work in data.get("data") or []:
        ids = {x.get("type"): x.get("value") for x in work.get("external_ids") or []}
        rows.append({"id": ids.get("doi") or f"lens:{work.get('lens_id')}", "doi": ids.get("doi"), "title": work.get("title"), "year": work.get("year_published"), "cites": work.get("scholarly_citations_count") or 0, "extra": f"{work.get('patent_citations_count') or 0} цитирований в патентах"})
    return rows


DATABASES = {"openalex": openalex, "arxiv": arxiv, "crossref": crossref, "s2": semantic_scholar, "core": core, "lens": lens}


def search(project, db, query, limit, question=None):
    rows = DATABASES[db](query, limit)
    return record_search(project, db, query, rows, question)


# --- not paper searches (printed only, logged) -----------------------------------------

def orcid(query, limit):
    match = re.search(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", query)
    if not match:
        for person in get_json(f"{ORCID}/expanded-search/?{urllib.parse.urlencode({'q': query, 'rows': limit})}").get("expanded-result") or []:
            name = f"{person.get('given-names') or ''} {person.get('family-names') or ''}".strip()
            print(f"{person['orcid-id']} | {name} | {'; '.join(person.get('institution-name') or [])}")
        return
    for group in (get_json(f"{ORCID}/{match.group()}/works").get("group") or [])[:limit]:
        summary = group["work-summary"][0]
        ids = {x["external-id-type"]: x["external-id-value"] for x in group.get("external-ids", {}).get("external-id", [])}
        year = ((summary.get("publication-date") or {}).get("year") or {}).get("value") or "?"
        print(f"{year} | {summary['title']['title']['value']}")
        print(f"  {ids.get('doi') or ids.get('arxiv') or 'нет DOI'}")


def patents(query, limit):
    key = os.environ.get("PATENTSVIEW_API_KEY")
    if not key:
        raise ValueError("нужен PATENTSVIEW_API_KEY (https://patentsview.org/apis/keyrequest)")
    params = urllib.parse.urlencode({
        "q": json.dumps({"_text_any": {"patent_title": query}}),
        "f": json.dumps(["patent_id", "patent_date", "patent_title", "assignees.assignee_organization"]),
        "o": json.dumps({"size": limit}),
    })
    for item in get_json(f"{PATENTSVIEW}?{params}", {"X-Api-Key": key}).get("patents") or []:
        orgs = {a.get("assignee_organization") for a in item.get("assignees") or [] if a.get("assignee_organization")}
        print(f"US{item['patent_id']} | {item.get('patent_date') or '?'} | {item.get('patent_title')}")
        print(f"  {'; '.join(sorted(orgs)) or 'нет правообладателя'}")


def worldbank(indicator, countries, limit):
    data = get_json(f"{WORLDBANK}/country/{countries}/indicator/{indicator}?{urllib.parse.urlencode({'format': 'json', 'per_page': limit, 'date': '2000:2030'})}")
    if len(data) < 2:
        raise ValueError(data[0].get("message", [{}])[0].get("value", "World Bank: нет данных"))
    for row in data[1] or []:
        if row.get("value") is not None:
            print(f"{row['countryiso3code']} | {row['date']} | {row['value']} | {row['indicator']['value']}")


def citations(project, raw, limit, question=None):
    """Forward (citing) and backward (referenced) works of one paper; both logged as searches."""
    work = fetch_work(raw)
    identifier = short_id(work)
    print(f"# {title(work)} ({identifier})")
    params = urllib.parse.urlencode({"filter": f"cites:{identifier}", "sort": "cited_by_count:desc", "per_page": limit})
    citing = [{"id": short_id(w), "doi": w.get("doi"), "title": title(w), "year": w.get("publication_year"), "cites": w.get("cited_by_count") or 0} for w in get_json(f"{OPENALEX}/works?{params}").get("results", [])]
    print("\nЦитируют (по числу цитирований):")
    print_rows(record_search(project, "openalex:cited_by", identifier, citing, question))
    refs = [r.rsplit("/", 1)[-1] for r in (work.get("referenced_works") or [])]
    referenced = [{"id": short_id(w), "doi": w.get("doi"), "title": title(w), "year": w.get("publication_year"), "cites": w.get("cited_by_count") or 0} for w in fetch_works(refs[:200])] if refs else []
    referenced.sort(key=lambda r: -r["cites"])
    print(f"\nСсылается ({len(refs)} в OpenAlex, показаны {min(limit, len(referenced))} самых цитируемых):")
    print_rows(record_search(project, "openalex:references", identifier, referenced[:limit], question))
