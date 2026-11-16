"""Library status and discovery reports; no interpretation of research claims."""

import json
import os
from collections import Counter

from . import papers, sources
from .project import today
from .text import snippet

GENERATED = "_Генерируется кодом (`lumi {cmd}`, {date})._"
BASIS = {"yes": "полный текст", "partial": "неполный текст", "abstract_only": "только аннотация", "none": "нет текста"}


def refs_cache_path(project):
    return project.field / "refs_cache.json"


def fetch_refs(project, min_count=2):
    """Titles of works cited by ≥min_count library papers but not in the library."""
    metas = papers.all_meta(project)
    counts = Counter(r.rsplit("/", 1)[-1] for m in metas.values() for r in set(m["work"].get("referenced_works") or []))
    wanted = [w for w, n in counts.items() if n >= min_count and w not in sources.library_ids(project)]
    cache = json.loads(refs_cache_path(project).read_text(encoding="utf-8")) if refs_cache_path(project).exists() else {}
    todo = [w for w in wanted if w not in cache]
    for work in sources.fetch_works(todo) if todo else []:
        cache[sources.short_id(work)] = {"title": sources.title(work), "year": work.get("publication_year"), "cites": work.get("cited_by_count") or 0,
                                         "first": (papers.surnames(work) or ["?"])[0], "doi": work.get("doi")}
    project.field.mkdir(parents=True, exist_ok=True)
    refs_cache_path(project).write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"ссылки: {len(wanted)} работ цитируются ≥{min_count} нашими статьями и отсутствуют в библиотеке ({len(todo)} загружено)")


def build_field(project, quiet=False):
    destination = project.field / "FIELD.md"
    metas = papers.all_meta(project)
    pq = {i: set(m.get("questions") or []) for i, m in metas.items()}
    name = project.config.get("name") or project.root.name
    full = sum(m.get("fulltext") == "yes" for m in metas.values())
    partial = sum(m.get("fulltext") == "partial" for m in metas.values())
    abstract_only = sum(m.get("fulltext") == "abstract_only" for m in metas.values())
    years = Counter(m["work"].get("publication_year") for m in metas.values())
    lines = [f"# Карта поля — {name}", "", GENERATED.format(cmd="field", date=today()), "",
             "## Библиотека", "",
             f"{len(metas)} статей: полный текст {full} · неполный текст {partial} · только аннотация {abstract_only} · нет текста {len(metas) - full - partial - abstract_only}", "",
             "По годам: " + " · ".join(f"{y}: {n}" for y, n in sorted(years.items(), key=lambda x: str(x[0]))), "",
             "| Статья | Ключ | Основа | Вопросы | Цит. |", "|---|---|---|---|---:|"]
    for identifier, m in sorted(metas.items(), key=lambda x: (x[1]["work"].get("publication_year") or 0, x[1].get("key") or "")):
        basis = BASIS.get(m.get("fulltext"), "?")
        link = os.path.relpath(project.papers / identifier / "README.md", destination.parent)
        lines.append(f"| [{papers.label(m)}]({link}) | {m.get('key')} | {basis} | {', '.join(sorted(pq.get(identifier, set()))) or '—'} | {m['work'].get('cited_by_count') or 0} |")
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
        top = sorted(((counts[w], w, info) for w, info in cache.items() if w not in sources.library_ids(project) and counts[w] >= 2), key=lambda x: (-x[0], -x[2].get("cites", 0)))[:25]
        lines += [f"- {info['first']} {info.get('year') or '?'} «{snippet(info['title'], 90)}» — цитируют {n} наших, всего {info.get('cites', 0)} цит. · {w}" for n, w, info in top] or ["- нет"]
    else:
        lines.append("- ещё не считали: `lumi field refs` (нужна сеть)")
    lines += ["", "## История поиска", "", "| Вопрос | Запросов | Новых за последние 5 | Ждут решения |", "|---|---:|---:|---:|"]
    candidates = project.candidates()
    for q in project.questions():
        log = [r for r in project.search_log() if r.get("question") == q["id"]]
        total, new = len(log), sum(r.get("new") or 0 for r in log[-5:])
        waiting = sum(1 for c in candidates if c.get("question") == q["id"] and not c.get("decision"))
        lines.append(f"| {q['id']} | {total} | {new} | {waiting} |")
    waiting = [c for c in candidates if not c.get("decision")]
    lines += ["", f"## Кандидаты ждут решения ({len(waiting)})", ""]
    for c in waiting[:40]:
        lines.append(f"- {c.get('question') or '—'} · {c.get('year') or '?'} «{snippet(c.get('title') or '?', 100)}» · {c['id']}")
    if len(waiting) > 40:
        lines.append(f"- … ещё {len(waiting) - 40}: `lumi field candidates`")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not quiet:
        print(f"FIELD.md: {len(metas)} статей, {len(waiting)} кандидатов ждут решения")


def status(project):
    metas = papers.all_meta(project)
    print(f"# {project.config.get('name') or project.root.name}")
    print(f"Библиотека: {project.papers}")
    print(f"статей {len(metas)} (полный текст {sum(m.get('fulltext') == 'yes' for m in metas.values())})")
    print(f"Неполный текст: {sum(m.get('fulltext') == 'partial' for m in metas.values())}")
    print(f"Вопросов {len(project.questions())} · кандидатов {len(project.candidates())} · запросов {len(project.search_log())}")
    print(f"История поиска: {project.field}")
