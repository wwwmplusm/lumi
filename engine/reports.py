"""Library status and missing reference discovery."""

import json
from collections import Counter

from . import papers, sources
from .onboarding import redact


def refs_cache_path(project):
    return project.field / "refs_cache.json"


def fetch_refs(project, min_count=1):
    """Print all missing references; return 1 when metadata retrieval is incomplete."""
    if min_count < 1:
        raise ValueError("min_count должен быть не меньше 1")
    metas = papers.all_meta(project)
    counts = Counter(
        r for m in metas.values()
        for r in {ref.rsplit("/", 1)[-1] for ref in m["work"].get("referenced_works") or []}
    )
    library = sources.library_ids(project, metas)
    wanted = sorted(w for w, n in counts.items() if n >= min_count and w not in library)
    path = refs_cache_path(project)
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    todo = [w for w in wanted if w not in cache]
    errors = []
    loaded = 0
    # Keep completed requests even if a later request fails. fetch_works itself
    # batches requests but cannot return earlier batches after an exception.
    for start in range(0, len(todo), 50):
        chunk = todo[start:start + 50]
        try:
            works = sources.fetch_works(chunk)
        except sources.NET_ERRORS as exc:
            errors.append("сеть/API: " + redact(str(exc)))
            break
        for work in works:
            try:
                identifier = sources.short_id(work)
                if identifier not in chunk:
                    raise ValueError("неожиданный идентификатор работы")
                info = {
                    "title": sources.title(work), "year": work.get("publication_year"),
                    "cites": int(work.get("cited_by_count") or 0),
                    "first": (papers.surnames(work) or ["?"])[0], "doi": work.get("doi"),
                }
            except (KeyError, ValueError, TypeError, AttributeError) as exc:
                errors.append("повреждённая запись API: " + redact(str(exc)))
                continue
            cache[identifier] = info
            loaded += 1
    project.field.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    missing = [w for w in wanted if w not in cache]
    print(f"ссылки: {len(wanted)} работ цитируются ≥{min_count} нашими статьями и отсутствуют в библиотеке ({loaded} загружено)")
    for identifier in sorted(wanted, key=lambda w: (-counts[w], -(cache.get(w, {}).get("cites") or 0), w)):
        info = cache.get(identifier)
        if info is None:
            print(f"- {identifier} — цитируют {counts[identifier]} наших; метаданные не получены")
        else:
            print(f"- {info.get('first') or '?'} {info.get('year') or '?'} «{info.get('title') or '?'}» — цитируют {counts[identifier]} наших, всего {info.get('cites', 0)} цит. · {identifier}")
    if missing or errors:
        print(f"Неполный результат: метаданные не получены для {len(missing)} работ.")
        for error in errors:
            print(f"Ошибка получения ссылок: {error}")
        return 1
    return 0


def status(project):
    metas = papers.all_meta(project)
    print(f"# {project.config.get('name') or project.root.name}")
    print(f"Библиотека: {project.papers}")
    statuses = Counter(papers.fulltext_status(project, identifier, meta) for identifier, meta in metas.items())
    print(f"статей {len(metas)} (полный текст {statuses['yes']})")
    print(f"Неполный текст: {statuses['partial']}")
    print(f"Вопросов {len(project.questions())} · кандидатов {len(project.candidates())} · запросов {len(project.search_log())}")
    print(f"История поиска: {project.field}")
