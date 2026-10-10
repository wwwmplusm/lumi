"""Claims (facts): extraction by workers, verification by code, attribution, lint, audit, manual add."""

import json
import random
import re

from . import index, papers, quotes, workers
from .project import next_id, today
from .text import normalize, numbers, snippet

TYPES = {"finding", "assumption", "hypothesis", "definition"}
STATUSES = {"active", "contested", "retracted", "superseded"}
SCOPE_FIELDS = ("population", "period", "metric")


def paper_of(claim):
    return (claim.get("evidence") or [{}])[0].get("paper")


def cite(project, claim, metas=None):
    """'[Gao 2026, с. 112]' or '[D:name, file]' for dataset claims."""
    ev = (claim.get("evidence") or [{}])[0]
    if ev.get("dataset"):
        return f"[D:{ev['dataset']}, {ev.get('file', '')}]"
    meta = (metas or {}).get(ev["paper"]) or papers.load_meta(project, ev["paper"])
    return f"[{papers.cite_name(meta)}, {papers.page_ref(meta, ev['page'])}]" if meta else f"[{ev.get('paper')}]"


# --- attribution (code markers + worker opinion) ----------------------------------------

CITED = [
    re.compile(r"\(\s*(?:e\.g\.,?\s*|see\s+)?(?!(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4]\b|fy|table|fig|figure|section|eq)\w*)[a-z][\w'-]+(?: et al\.?)?(?:,? (?:and )?[a-z][\w'-]+)*,? (?:19|20)\d{2}[a-z]?(?:[;,][^()]{0,80})?\)"),
    re.compile(r"[a-z][\w'-]+ et al\.?,? \(?(?:19|20)\d{2}"),
    re.compile(r"\[\s*\d+(?:\s*[,-]\s*\d+)*\s*\]"),
]
REPORTED = re.compile(r"\b(?:according to|as reported|reported by|financial times|reuters|bloomberg|new york times|wall street journal|south china morning post)\b")


def quote_context(quote, page_text):
    needle, text = normalize(quote), normalize(page_text)
    at = text.find(needle)
    if at < 0:
        return needle
    start = max(text.rfind(". ", 0, at) + 2, at - 300, 0)
    end = text.find(". ", at + len(needle))
    end = min(end if end >= 0 else len(text), at + len(needle) + 200)
    return text[start:end]


def detect_attribution(quote, page_text):
    context = quote_context(quote, page_text)
    match = REPORTED.search(context)
    if match:
        return "reported", match.group()
    for pattern in CITED:
        match = pattern.search(context)
        if match:
            return "cited", match.group()
    return None


def apply_attribution(claim, page_text, worker_value=None):
    """News marker -> reported (code wins); citation marker + worker 'own' -> own (methods cite tools);
    citation marker otherwise -> cited. Non-own findings become assumptions; the original type is kept."""
    detected = detect_attribution(claim["evidence"][0]["quote"], page_text)
    claim.pop("attribution_marker", None)
    if detected:
        claim["attribution_marker"] = detected[1]
    if detected and detected[0] == "reported":
        value, by = "reported", "code"
    elif detected and worker_value == "own":
        value, by = "own", "worker (code saw citation marker)"
    elif detected:
        value, by = "cited", "code" if worker_value is None else f"code+worker:{worker_value}"
    elif worker_value in {"own", "cited", "reported"}:
        value, by = worker_value, "worker"
    else:
        value, by = "own", "default"
    claim["attribution"], claim["attribution_by"] = value, by
    original = claim.get("type_extracted", claim["type"])
    if value != "own" and original == "finding":
        claim["type_extracted"] = "finding"
        claim["type"] = "assumption"
    elif "type_extracted" in claim:
        claim["type"] = claim["type_extracted"]
    return claim


# --- lint (no LLM): does the statement say more than the quote? ---------------------------

HEDGES = re.compile(r"\b(may|might|could|suggests?|suggesting|appears? to|likely|possibly|potentially|associated with|correlat\w*|tends? to|tended to|preliminary)\b")
CAUSAL = re.compile(r"\b(caus\w*|led to|leads? to|result(?:s|ed)? in|drive[sn]?|drove|because|due to|effect of|impact\w*|forc\w*|enabl\w*)\b")
QUANT = re.compile(r"\b(all|every|majority of|always|never|none of)\b")
NAME = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
COMMON = {"The", "This", "These", "Authors", "Paper", "Study", "However", "Chinese", "China", "American", "US", "USA", "United", "States", "AI"}


def lint_claim(claim, page_text=""):
    """Flags (Russian, for humans). Numbers, dropped hedges, causal/quantifier words and names not in the source."""
    ev = (claim.get("evidence") or [{}])[0]
    quote = ev.get("quote") or ""
    if not quote:
        return []
    statement, q = claim["statement"], quote.lower()
    s = statement.lower()
    flags = []
    missing = sorted(numbers(statement) - numbers(quote))
    if missing:
        flags.append(f"числа не из цитаты: {', '.join(missing)}")
    if HEDGES.search(q) and not HEDGES.search(s):
        flags.append(f"пропала оговорка «{HEDGES.search(q).group()}»")
    if CAUSAL.search(s) and not CAUSAL.search(q):
        flags.append(f"причинность не из цитаты: «{CAUSAL.search(s).group()}»")
    if QUANT.search(s) and not QUANT.search(q):
        flags.append(f"квантор не из цитаты: «{QUANT.search(s).group()}»")
    context = normalize(page_text or quote)
    names = [m.group() for m in NAME.finditer(statement) if m.start() > 0 and statement[:m.start()].rstrip()[-1:] not in ".!?"]
    names = [n for n in dict.fromkeys(names) if n not in COMMON and normalize(n) not in context]
    if names:
        flags.append(f"имена не со страницы: {', '.join(names[:4])}")
    return flags


def lint(project, quiet=False):
    claims = project.claims()
    flagged = 0
    for claim in claims:
        ev = (claim.get("evidence") or [{}])[0]
        pages = papers.load_pages(project, ev["paper"]) if ev.get("paper") else None
        page_text = pages[ev["page"] - 1] if pages and 1 <= ev.get("page", 0) <= len(pages) else ""
        flags = lint_claim(claim, page_text)
        claim.pop("unquoted_numbers", None)
        if flags:
            claim["lint"] = flags
            flagged += 1
        else:
            claim.pop("lint", None)
    project.save_claims(claims)
    if not quiet:
        print(f"lint: {flagged}/{len(claims)} фактов с флагами (утверждение сильнее цитаты?)")
    return flagged


# --- verification of worker output ---------------------------------------------------------

def verify(project, identifier, raw_claims):
    """Keep only claims whose quote is in the paper (FOUND / FOUND*); fix pages. Returns (accepted, rejected)."""
    pages = papers.load_pages(project, identifier)
    accepted, rejected = [], []
    for item in raw_claims:
        quote = str(item.get("quote", "")).strip()
        hint = item.get("page") if isinstance(item.get("page"), int) else None
        result = quotes.locate(project, identifier, quote, hint, search_library=False)
        if result["verdict"] not in {"FOUND", "FOUND*"} or not str(item.get("statement", "")).strip():
            rejected.append({**item, "verdict": result["verdict"]})
            continue
        page = result["page"]
        claim = {"statement": item["statement"].strip(), "type": item.get("type") if item.get("type") in TYPES else "assumption", "status": "active",
                 "evidence": [{"paper": identifier, "page": page, "quote": quote, "match": result["verdict"]}], "source_depth": "fulltext",
                 "created": today(), "updated": today(), "page_corrected": page != hint, "by": "worker:extract"}
        if result.get("version") == "layout":
            claim["evidence"][0]["verified_on"] = "pdftotext -layout"
        claim.update({f: str(item[f]).strip() for f in SCOPE_FIELDS if item.get(f)})
        if item.get("attribution") in {"own", "cited", "reported"}:
            claim["attribution_worker"] = item["attribution"]
        apply_attribution(claim, pages[page - 1], item.get("attribution"))
        flags = lint_claim(claim, pages[page - 1])
        if flags:
            claim["lint"] = flags
        accepted.append(claim)
    return accepted, rejected


def paper_text(project, identifier):
    meta = papers.load_meta(project, identifier)
    out = []
    for n, page in enumerate(papers.load_pages(project, identifier), 1):
        printed = papers.printed(meta, n)
        out.append(f"=== PAGE {n}{f' (printed {printed})' if printed and printed != str(n) else ''} ===\n" + re.sub(r"[ \t]{2,}", "  ", page))
    return "\n".join(out)


def store(project, accepted, questions=None):
    claims = project.claims()
    def key(c):
        ev = c["evidence"][0]
        return (ev.get("paper") or ev.get("dataset"), normalize(ev.get("quote") or c["statement"]))
    known = {key(c) for c in claims if c.get("evidence")}
    fresh = []
    for claim in accepted:
        if key(claim) in known:
            continue
        known.add(key(claim))
        if questions:
            claim["questions"] = sorted(set(claim.get("questions") or []) | set(questions))
        claims.append({"id": next_id(claims, "C"), **claim})
        fresh.append(claims[-1])
    project.save_claims(claims)
    return fresh


def extract(project, refs, force=False, dry=False):
    context = project.config.get("context") or ""
    for ref in refs:
        identifier = papers.resolve(project, ref)
        meta = papers.load_meta(project, identifier)
        if not force and not dry and any(paper_of(c) == identifier for c in project.claims()):
            print(f"{papers.label(meta)}: факты уже извлечены (--force, чтобы добавить)")
            continue
        if not papers.load_pages(project, identifier):
            print(f"{papers.label(meta)}: нет полного текста")
            continue
        payload = (f"Project context: {context}\n\n" if context else "") + paper_text(project, identifier)
        parsed, record = workers.run(project, "extract", f"Extract claims from paper {identifier} (text on stdin).", payload, identifier)
        if not parsed or not isinstance(parsed.get("claims"), list):
            print(f"{papers.label(meta)}: воркер не справился ({record.get('error', '')[:120]})")
            continue
        accepted, rejected = verify(project, identifier, parsed["claims"])
        if dry:
            path = project.runs / f"dry_extract_{identifier}.jsonl"
            path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in accepted), encoding="utf-8")
            print(f"{papers.label(meta)} (dry): {len(accepted)} проверено, {len(rejected)} отброшено → {path.name}")
            continue
        fresh = store(project, accepted)
        print(f"{papers.label(meta)}: +{len(fresh)} фактов, {len(rejected)} отброшено (цитаты нет в тексте), {sum(bool(c.get('lint')) for c in fresh)} с флагами lint, ${record.get('cost_usd') or 0:.3f}")
    index.build(project, quiet=True)


def reverify(project, refs):
    for ref in refs:
        identifier = papers.resolve(project, ref)
        parsed = workers.latest_raw(project, "extract", identifier)
        if not parsed:
            print(f"{identifier}: нет сохранённого ответа воркера")
            continue
        accepted, rejected = verify(project, identifier, parsed.get("claims") or [])
        fresh = store(project, accepted)
        print(f"{papers.label(papers.load_meta(project, identifier))}: {len(accepted)} проверено (+{len(fresh)} новых), {len(rejected)} отброшено")
    index.build(project, quiet=True)


def attribute(project):
    claims = project.claims()
    for claim in claims:
        ev = claim["evidence"][0]
        if ev.get("paper"):
            apply_attribution(claim, papers.load_pages(project, ev["paper"])[ev["page"] - 1], claim.get("attribution_worker"))
    project.save_claims(claims)
    counts = {k: sum(c.get("attribution") == k for c in claims) for k in ("own", "cited", "reported")}
    print(f"атрибуция: {counts}")


def annotate(project, batch=50):
    claims = project.claims()
    todo = [c for c in claims if c.get("evidence") and c["evidence"][0].get("paper") and (not any(c.get(f) for f in SCOPE_FIELDS) or "questions" not in c)]
    by_id = {c["id"]: c for c in claims}
    questions = {q["id"]: q["question"] for q in project.questions()}
    failures = 0
    for start in range(0, len(todo), batch):
        chunk = todo[start:start + batch]
        payload = json.dumps({"questions": questions, "claims": [{"id": c["id"], "paper": papers.label(papers.load_meta(project, paper_of(c))), "statement": c["statement"], "quote": c["evidence"][0]["quote"]} for c in chunk]}, ensure_ascii=False)
        parsed, record = workers.run(project, "annotate", f"Annotate scope of {len(chunk)} claims (JSON on stdin).", payload, f"batch{start // batch}")
        if not parsed or not isinstance(parsed.get("claims"), list):
            failures += 1
            print(f"annotate {start // batch}: воркер не справился")
            if failures >= 3:
                break
            continue
        failures = 0
        wanted = {c["id"] for c in chunk}
        for item in parsed["claims"]:
            claim = by_id.get(item.get("id"))
            if not claim or claim["id"] not in wanted:
                continue
            claim.update({f: claim.get(f) or str(item.get(f) or "unspecified").strip()[:200] for f in SCOPE_FIELDS})
            claim["questions"] = sorted(set(claim.get("questions") or []) | {q for q in item.get("questions") or [] if q in questions})
            if item.get("attribution") in {"own", "cited", "reported"}:
                claim["attribution_worker"] = item["attribution"]
                ev = claim["evidence"][0]
                apply_attribution(claim, papers.load_pages(project, ev["paper"])[ev["page"] - 1], item["attribution"])
            claim["updated"] = today()
        project.save_claims(claims)
        print(f"annotate {start // batch}: {len(chunk)} фактов, ${record.get('cost_usd') or 0:.3f}")


def context_window(quote, page_text, before=400, after=400):
    flat = " ".join(page_text.split())
    probe = " ".join(quote.split())[:40]
    at = flat.find(probe)
    return quote if at < 0 else flat[max(0, at - before): at + len(" ".join(quote.split())) + after]


def audit(project, sample=40, seed=None):
    """Independent worker grades a random sample. Report only: runs/audit_<date>.jsonl."""
    active = [c for c in project.claims() if c["status"] == "active" and c["evidence"][0].get("paper")]
    chunk = random.Random(seed).sample(active, min(sample, len(active)))
    items = [{"id": c["id"], "statement": c["statement"], "type": c["type"], "attribution": c.get("attribution"), **{f: c.get(f) for f in SCOPE_FIELDS},
              "context": context_window(c["evidence"][0]["quote"], papers.load_pages(project, paper_of(c))[c["evidence"][0]["page"] - 1])} for c in chunk]
    parsed, record = workers.run(project, "audit", f"Audit {len(items)} claims (JSON on stdin).", json.dumps(items, ensure_ascii=False), f"sample{len(items)}")
    if not parsed or not isinstance(parsed.get("audit"), list):
        raise ValueError(f"аудитор не справился ({record.get('error', '')[:120]})")
    by_id = {c["id"]: c for c in chunk}
    rows = [dict(r, paper=paper_of(by_id[r["id"]])) for r in parsed["audit"] if r.get("id") in by_id]
    out = project.runs / f"audit_{today()}.jsonl"
    with out.open("a") as handle:
        for r in rows:
            handle.write(json.dumps(r, ensure_ascii=False) + "\n")
    fidelity = {k: sum(r.get("fidelity") == k for r in rows) for k in ("exact", "overstated", "distorted", "unsupported")}
    print(f"аудит: {len(rows)} фактов; точность {fidelity}; тип неверен {sum(not r.get('type_ok') for r in rows)}; атрибуция неверна {sum(not r.get('attribution_ok') for r in rows)}")
    for r in rows:
        if r.get("issue"):
            print(f"  {cite(project, by_id[r['id']])} [{r['fidelity']}] {r['issue']} · {r['id']}")
    return rows


# --- manual claims (from a chat session) and status changes ----------------------------------

def add_manual(project, statement, ref=None, quote=None, page=None, pdf_page=None, kind="finding", questions=None, dataset=None, file=None, how=None, attribution=None):
    if dataset:
        folder = project.datasets / dataset
        if not (folder / "README.md").exists():
            raise ValueError(f"нет sources/datasets/{dataset}/README.md — сначала опишите датасет")
        if file and not (folder / file).exists():
            raise ValueError(f"нет файла sources/datasets/{dataset}/{file}")
        if not how:
            raise ValueError("для факта из датасета нужен --how (как получено число)")
        claim = {"statement": statement, "type": kind, "status": "active", "evidence": [{"dataset": dataset, "file": file, "how": how}], "source_depth": "dataset", "attribution": "own", "attribution_by": "user", "created": today(), "updated": today(), "by": "manual"}
    else:
        result = quotes.check_one(project, ref, quote, page=page, pdf_page=pdf_page)
        if result["verdict"] not in quotes.ACCEPTED:
            raise ValueError(quotes.report(project, {**result, "quote": quote}))
        identifier = result["paper"]
        pages = papers.load_pages(project, identifier)
        claim = {"statement": statement, "type": kind if kind in TYPES else "assumption", "status": "active",
                 "evidence": [{"paper": identifier, "page": result["page"], "quote": quote, "match": result["verdict"]}], "source_depth": "fulltext",
                 "created": today(), "updated": today(), "by": "manual"}
        if result.get("page_wrong"):
            print(f"страница исправлена: цитата на {papers.page_ref(papers.load_meta(project, identifier), result['page'])}")
        apply_attribution(claim, pages[result["page"] - 1], attribution)
        flags = lint_claim(claim, pages[result["page"] - 1])
        if flags:
            claim["lint"] = flags
    if questions:
        claim["questions"] = sorted(set(questions))
    fresh = store(project, [claim])
    if not fresh:
        print("такой факт уже есть")
        return None
    claim = fresh[0]
    print(f"{claim['id']}: {snippet(statement, 120)} {cite(project, claim)} [{claim['type']}, {claim.get('attribution')}]")
    for flag in claim.get("lint") or []:
        print(f"  lint: {flag}")
    index.build(project, quiet=True)
    return claim


def set_status(project, claim_id, status, reason):
    if status not in STATUSES:
        raise ValueError(f"статус: {', '.join(sorted(STATUSES))}")
    claims = project.claims()
    for claim in claims:
        if claim["id"] == claim_id:
            claim.setdefault("history", []).append({"status": claim["status"], "at": claim.get("updated")})
            claim.update(status=status, status_reason=reason, updated=today())
            project.save_claims(claims)
            print(f"{claim_id} → {status}: {snippet(claim['statement'], 100)}")
            return claim
    raise ValueError(f"нет факта {claim_id}")
