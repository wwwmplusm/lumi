"""Generated views: MAP.md (knowledge by question), FIELD.md (the field), status at session start, finish at end."""

import json
import subprocess
from collections import Counter

from . import beliefs as beliefs_mod, cards, claims as claims_mod, papers, sources
from .project import today
from .text import snippet

GENERATED = "_Генерируется кодом (`eurema {cmd}`, {date}). Не править вручную._"


def library_state(project):
    metas = papers.all_meta(project)
    claims = project.claims()
    rows = project.belief_rows()
    now = beliefs_mod.current(rows)
    return metas, claims, {c["id"]: c for c in claims}, rows, now


def paper_questions(project, metas, claims):
    out = {i: set(m.get("questions") or []) for i, m in metas.items()}
    for c in claims:
        p = claims_mod.paper_of(c)
        if p in out:
            out[p] |= set(c.get("questions") or [])
    return out


# --- what is hanging -----------------------------------------------------------------------

def hanging(project, metas, claims, claims_by_id, now):
    lines = []
    for b in now.values():
        if b["status"] == "review":
            broken = [r for r in b.get("premises") or [] if beliefs_mod.premise_problem(project, r, claims_by_id, now)]
            what = "; ".join(beliefs_mod.ref_line(project, r, claims_by_id, now, metas) for r in broken) or b.get("status_reason", "")
            lines.append(f"- «{snippet(b['statement'], 90)}» ({b['id']}) — на пересмотр: {what}")
    unseen = [b for b in now.values() if not b.get("seen_by_user") and b["status"] not in ("superseded",)]
    if unseen:
        lines.append(f"- Новые или изменённые позиции, пользователь ещё не видел: " + "; ".join(f"«{snippet(b['statement'], 60)}» ({b['id']})" for b in unseen))
    flagged = [c for c in claims if c.get("lint") and c["status"] == "active"]
    if flagged:
        lines.append(f"- Фактов с флагами lint (утверждение сильнее цитаты?): {len(flagged)} — `eurema claims --lint`")
    missing = cards.check_cards(project)
    if missing:
        lines.append(f"- Карточки: {len(missing)} проблем — " + "; ".join(missing[:5]) + (" …" if len(missing) > 5 else ""))
    bad_pages = [m for m in metas.values() if m.get("fulltext") == "yes" and m.get("page_source") in ("none", "metadata")]
    if bad_pages:
        lines.append("- Печатные страницы не подтверждены: " + ", ".join(f"{papers.label(m)} ({m.get('page_source')})" for m in bad_pages) + " — `eurema pages Ключ --first N`")
    return lines


# --- MAP.md ------------------------------------------------------------------------------

def belief_block(project, b, rows, claims_by_id, now, metas):
    mark = " ●" if not b.get("seen_by_user") else ""
    state = {"active": "новая" if not b.get("seen_by_user") else "", "review": "НА ПЕРЕСМОТР"}.get(b["status"], b["status"])
    meta_line = " · ".join(x for x in (b.get("confidence") or "?", f"v{b['v']}", state, b["id"]) if x)
    lines = [f"### {b['statement']}{mark}", meta_line]
    if b.get("reasoning"):
        lines.append(f"Почему: {b['reasoning']}")
    for ref in b.get("premises") or []:
        lines.append(f"  ✓ {beliefs_mod.ref_line(project, ref, claims_by_id, now, metas)}")
    for ref in b.get("against") or []:
        lines.append(f"  ✗ против: {beliefs_mod.ref_line(project, ref, claims_by_id, now, metas)}")
    versions = beliefs_mod.history(rows, b["id"])
    if len(versions) > 1:
        steps = []
        for r in versions:
            reason = r.get("status_reason") if r["status"] == "superseded" else ""
            steps.append(f"v{r['v']} «{snippet(r['statement'], 50)}»" + (f" (причина: {reason})" if reason else ""))
        lines.append("  история: " + " → ".join(steps))
    return lines


def build_map(project, quiet=False):
    metas, claims, claims_by_id, rows, now = library_state(project)
    active_claims = [c for c in claims if c["status"] == "active"]
    used = {r for b in now.values() for r in [*(b.get("premises") or []), *(b.get("against") or [])]}
    pq = paper_questions(project, metas, claims)
    name = project.config.get("name") or project.root.name
    lines = [f"# Карта знаний — {name}", "", GENERATED.format(cmd="map", date=today()), "",
             f"Позиций {sum(b['status'] != 'superseded' for b in now.values())} · фактов {len(active_claims)} · статей {len(metas)} (полный текст {sum(m.get('fulltext') == 'yes' for m in metas.values())})", ""]
    hang = hanging(project, metas, claims, claims_by_id, now)
    lines += ["## Висит", "", *(hang or ["- ничего"]), ""]
    questions = project.questions()
    for q in questions + [{"id": None, "question": "Без вопроса"}]:
        qb = [b for b in now.values() if b.get("question") == q["id"] and b["status"] not in ("superseded",)]
        qc = [c for c in active_claims if q["id"] and q["id"] in (c.get("questions") or [])]
        qp = {claims_mod.paper_of(c) for c in qc if claims_mod.paper_of(c)}
        if q["id"] is None and not qb:
            continue
        title = f"{q['id']} — {q['question']}" if q["id"] else q["question"]
        lines += [f"## {title}", "", f"[позиций {len(qb)} · фактов {len(qc)} · статей {len(qp)}]", ""]
        for b in sorted(qb, key=lambda b: ({"review": 0, "active": 1, "dropped": 2}.get(b["status"], 3), b["id"])):
            lines += belief_block(project, b, rows, claims_by_id, now, metas) + [""]
        if not qb:
            lines += ["_Позиций пока нет._", ""]
        unused = [c for c in qc if c["id"] not in used]
        if unused:
            lines += [f"Фактов по вопросу ещё не в позициях: {len(unused)} — `eurema claims --question {q['id']} --unused`", ""]
        if q["id"]:
            thin = [m for i, m in metas.items() if q["id"] in pq.get(i, set()) and m.get("fulltext") != "yes"]
            if thin:
                lines += [f"Без полного текста: " + "; ".join(papers.label(m) for m in thin), ""]
    (project.root / "MAP.md").write_text("\n".join(lines), encoding="utf-8")
    if not quiet:
        print(f"MAP.md: {len(now)} позиций, {len(active_claims)} фактов; висит: {len(hang)}")
    return hang


# --- FIELD.md ----------------------------------------------------------------------------

def refs_cache_path(project):
    return project.field / "refs_cache.json"


def fetch_refs(project, min_count=2):
    """Titles of works cited by ≥min_count library papers but not in the library (bibliographic coupling)."""
    metas = papers.all_meta(project)
    counts = Counter(r.rsplit("/", 1)[-1] for m in metas.values() for r in set(m["work"].get("referenced_works") or []))
    wanted = [w for w, n in counts.items() if n >= min_count and w not in metas]
    cache = json.loads(refs_cache_path(project).read_text(encoding="utf-8")) if refs_cache_path(project).exists() else {}
    todo = [w for w in wanted if w not in cache]
    for work in sources.fetch_works(todo) if todo else []:
        cache[sources.short_id(work)] = {"title": sources.title(work), "year": work.get("publication_year"), "cites": work.get("cited_by_count") or 0,
                                         "first": (papers.surnames(work) or ["?"])[0], "doi": work.get("doi")}
    project.field.mkdir(parents=True, exist_ok=True)
    refs_cache_path(project).write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"ссылки: {len(wanted)} работ цитируются ≥{min_count} нашими статьями и отсутствуют в библиотеке ({len(todo)} загружено)")


def saturation(project, question, window=5):
    log = [r for r in project.search_log() if r.get("question") == question]
    last = log[-window:]
    new = sum(r.get("new") or 0 for r in last)
    state = "насыщение?" if len(last) >= window and new == 0 else ("мало запросов" if len(log) < window else "ищем")
    return len(log), new, state


def build_field(project, quiet=False):
    metas, claims, *_ = library_state(project)
    pq = paper_questions(project, metas, claims)
    per_paper = Counter(claims_mod.paper_of(c) for c in claims if c["status"] == "active")
    name = project.config.get("name") or project.root.name
    full = sum(m.get("fulltext") == "yes" for m in metas.values())
    abstract_only = sum(m.get("fulltext") == "abstract_only" for m in metas.values())
    years = Counter(m["work"].get("publication_year") for m in metas.values())
    lines = [f"# Карта поля — {name}", "", GENERATED.format(cmd="field", date=today()), "",
             "## Библиотека", "",
             f"{len(metas)} статей: полный текст {full} · только аннотация {abstract_only} · нет текста {len(metas) - full - abstract_only}", "",
             "По годам: " + " · ".join(f"{y}: {n}" for y, n in sorted(years.items(), key=lambda x: str(x[0]))), "",
             "| Статья | Ключ | Основа | Вопросы | Цит. | Фактов |", "|---|---|---|---|---:|---:|"]
    for identifier, m in sorted(metas.items(), key=lambda x: (x[1]["work"].get("publication_year") or 0, x[1].get("key") or "")):
        basis = cards.BASIS.get(m.get("fulltext"), "?")
        lines.append(f"| [{papers.label(m)}](sources/papers/{identifier}/README.md) | {m.get('key')} | {basis} | {', '.join(sorted(pq.get(identifier, set()))) or '—'} | {m['work'].get('cited_by_count') or 0} | {per_paper.get(identifier, 0)} |")
    authors = Counter()
    for m in metas.values():
        for a in m["work"].get("authorships") or []:
            authors[((a.get("author") or {}).get("display_name") or "").strip()] += 1
    key_authors = [(a, n) for a, n in authors.most_common() if n >= 2 and a]
    lines += ["", "## Авторы с ≥2 статьями в библиотеке", ""]
    lines += [f"- {a} — {n}" for a, n in key_authors] or ["- пока нет"]
    lines += ["", "## Часто цитируются нашими статьями, но их нет в библиотеке", ""]
    if refs_cache_path(project).exists():
        cache = json.loads(refs_cache_path(project).read_text(encoding="utf-8"))
        counts = Counter(r.rsplit("/", 1)[-1] for m in metas.values() for r in set(m["work"].get("referenced_works") or []))
        top = sorted(((counts[w], w, info) for w, info in cache.items() if w not in metas and counts[w] >= 2), key=lambda x: (-x[0], -x[2].get("cites", 0)))[:25]
        lines += [f"- {info['first']} {info.get('year') or '?'} «{snippet(info['title'], 90)}» — цитируют {n} наших, всего {info.get('cites', 0)} цит. · {w}" for n, w, info in top] or ["- нет"]
    else:
        lines.append("- ещё не считали: `eurema field refs` (нужна сеть)")
    lines += ["", "## Поиск и насыщение", "", "| Вопрос | Запросов | Новых за последние 5 | Ждут решения | Состояние |", "|---|---:|---:|---:|---|"]
    candidates = project.candidates()
    for q in project.questions():
        total, new, state = saturation(project, q["id"])
        waiting = sum(1 for c in candidates if c.get("question") == q["id"] and not c.get("decision"))
        lines.append(f"| {q['id']} | {total} | {new} | {waiting} | {state} |")
    waiting = [c for c in candidates if not c.get("decision")]
    lines += ["", f"## Кандидаты ждут решения ({len(waiting)})", ""]
    for c in waiting[:40]:
        lines.append(f"- {c.get('question') or '—'} · {c.get('year') or '?'} «{snippet(c.get('title') or '?', 100)}» · {c['id']}")
    if len(waiting) > 40:
        lines.append(f"- … ещё {len(waiting) - 40}: `eurema field candidates`")
    (project.root / "FIELD.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not quiet:
        print(f"FIELD.md: {len(metas)} статей, {len(waiting)} кандидатов ждут решения")


# --- session start / end ----------------------------------------------------------------------

def git(project, *args):
    try:
        return subprocess.run(["git", *args], cwd=project.root, capture_output=True, text=True).stdout
    except OSError:
        return ""


def last_session(project):
    files = sorted(project.sessions.glob("*.md")) if project.sessions.exists() else []
    return files[-1] if files else None


def status(project):
    beliefs_mod.refresh(project, quiet=True)
    metas, claims, claims_by_id, rows, now = library_state(project)
    name = project.config.get("name") or project.root.name
    print(f"# {name}")
    print(f"статей {len(metas)} (полный текст {sum(m.get('fulltext') == 'yes' for m in metas.values())}) · фактов {sum(c['status'] == 'active' for c in claims)} · позиций {sum(b['status'] != 'superseded' for b in now.values())}")
    session = last_session(project)
    if session:
        text = session.read_text(encoding="utf-8")
        print(f"\nПоследняя сессия: {session.name}")
        for heading in ("## Дальше", "## Next"):
            if heading in text:
                print(text.split(heading, 1)[1].split("\n## ", 1)[0].strip()[:1200])
                break
    print("\nВисит:")
    for line in hanging(project, metas, claims, claims_by_id, now) or ["- ничего"]:
        print(line)
    dirty = [x for x in git(project, "status", "--porcelain").splitlines() if x.strip()]
    if dirty:
        print(f"\ngit: {len(dirty)} незакоммиченных изменений")


def finish(project):
    changed = beliefs_mod.refresh(project)
    claims_mod.lint(project)
    build_map(project)
    build_field(project)
    print(f"\nДальше: записать sessions/{today()}.md (Спрашивали / Нашли / Позиции / Дальше), обновить CONTEXT.md, git commit.")
    out = git(project, "status", "--short")
    if out.strip():
        print(out.rstrip())
    return changed
