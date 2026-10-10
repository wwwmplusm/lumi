"""eurema — research engine CLI. Run from inside a project folder (one that has eurema.json)."""

import argparse
import json
import sqlite3
import sys

from . import beliefs, cards, claims, index, papers, quotes, reports, sources
from .project import ENGINE, MARKER, Project, find_root
from .text import snippet


def install_files(root, name, overwrite=()):
    """Project files from templates: CONTEXT.md, questions.md, CLAUDE.md, .gitignore, ./eurema wrapper."""
    import os
    rel = os.path.relpath(ENGINE, root)
    values = {"{{NAME}}": name, "{{ENGINE_REL}}": rel, "{{RULES}}": f"{rel}/RULES.md", "{{SCHEMA}}": f"{rel}/SCHEMA.md"}
    for src, dst in (("CONTEXT.md", "CONTEXT.md"), ("questions.md", "questions.md"), ("CLAUDE.md", "CLAUDE.md"), ("gitignore", ".gitignore"), ("eurema", "eurema")):
        if (root / dst).exists() and dst not in overwrite:
            continue
        text = (ENGINE / "templates" / src).read_text(encoding="utf-8")
        for key, value in values.items():
            text = text.replace(key, value)
        (root / dst).write_text(text, encoding="utf-8")
    (root / "eurema").chmod(0o755)


def cmd_init(args):
    root = __import__("pathlib").Path(args.path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / MARKER).exists():
        raise ValueError(f"{root} уже проект Eurema")
    for sub in ("sources/papers", "sources/datasets", "field", "knowledge", "notes", "drafts", "sessions"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    config = {"name": args.name or root.name, "context": args.context or "", "worker_model": "sonnet", "worker_cap_per_day": 40}
    (root / MARKER).write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    install_files(root, config["name"])
    print(f"проект создан: {root}\nдальше: заполнить CONTEXT.md и questions.md, `git init`")


def cmd_claims(project, args):
    rows = project.claims()
    now = beliefs.current(project.belief_rows())
    used = {r for b in now.values() for r in [*(b.get("premises") or []), *(b.get("against") or [])]}
    paper = papers.resolve(project, args.paper) if args.paper else None
    metas = papers.all_meta(project)
    shown = 0
    for c in rows:
        if args.question and args.question not in (c.get("questions") or []):
            continue
        if args.unused and c["id"] in used:
            continue
        if args.lint and not c.get("lint"):
            continue
        if paper and claims.paper_of(c) != paper:
            continue
        if not args.all and c["status"] != "active":
            continue
        shown += 1
        flags = f"\n    lint: {'; '.join(c['lint'])}" if c.get("lint") else ""
        print(f"{claims.cite(project, c, metas)} [{c['type']}, {c.get('attribution', '?')}{'' if c['status'] == 'active' else ', ' + c['status']}] {snippet(c['statement'], 220)} · {c['id']}{flags}")
    print(f"— {shown} фактов")


def cmd_pages(project, args):
    identifier = papers.resolve(project, args.ref)
    if args.first is not None:
        meta = papers.set_pages_manual(project, identifier, args.first, args.at)
    else:
        meta = papers.load_meta(project, identifier)
        if args.detect:
            print(papers.parse_one(project, identifier))
            meta = papers.load_meta(project, identifier)
    labels = meta.get("page_labels") or []
    print(f"{papers.label(meta)}: печатные страницы — {meta.get('page_source')}" + (f" ({meta.get('page_note')})" if meta.get("page_note") else ""))
    if labels:
        print("  PDF→печатная: " + ", ".join(f"{i}→{x or '·'}" for i, x in enumerate(labels[:8], 1)) + (" …" if len(labels) > 8 else ""))


def cmd_field(project, args):
    if args.action == "refs":
        reports.fetch_refs(project, args.min)
        reports.build_field(project)
    elif args.action == "candidates":
        for c in project.candidates():
            if c.get("decision") and not args.all:
                continue
            print(f"{c.get('question') or '—'} · {c.get('year') or '?'} «{snippet(c.get('title') or '?', 110)}» · {c['id']} [{c.get('decision') or 'ждёт'}]")
    elif args.action == "decide":
        if not args.id or args.decision not in ("include", "exclude", "maybe"):
            raise ValueError("field decide ID include|exclude|maybe --why «…»")
        rows = project.candidates()
        hits = [r for r in rows if r["id"] == args.id or (r.get("doi") or "") == args.id.lower()]
        if not hits:
            raise ValueError(f"нет кандидата {args.id}")
        for r in hits:
            r.update(decision=args.decision, why=args.why or "", decided=__import__("datetime").date.today().isoformat())
        project.save_candidates(rows)
        print(f"{args.id}: {args.decision}" + (" — добавить: `eurema add " + args.id + "`" if args.decision == "include" else ""))
    else:
        reports.build_field(project)


def cmd_check(project, args):
    if args.text is not None:
        text = sys.stdin.read() if args.text == "-" else open(args.text, encoding="utf-8").read()
        results = quotes.check_text(project, text)
        if not results:
            print("цитат со ссылками не найдено (нужен вид «цитата» [Автор Год, с. N])")
            return 0
    else:
        if not args.ref or not args.quote:
            raise ValueError("check Ключ «цитата» [--page N] или check --text файл|-")
        results = [{**quotes.check_one(project, args.ref, args.quote, args.page, args.pdf_page), "quote": args.quote}]
    for r in results:
        print(quotes.report(project, r))
    bad = sum(not quotes.ok(r) for r in results)
    print(f"— проверено {len(results)}, не прошли {bad}")
    return 1 if bad else 0


def cmd_lint(project):
    problems = 0
    keys = {}
    for identifier, meta in papers.all_meta(project).items():
        if not meta.get("key"):
            print(f"{identifier}: нет ключа")
            problems += 1
        elif meta["key"] in keys:
            print(f"ключ {meta['key']} у двух статей: {keys[meta['key']]} и {identifier}")
            problems += 1
        keys[meta.get("key")] = identifier
    for line in cards.check_cards(project):
        print(line)
        problems += 1
    changed = beliefs.refresh(project)
    flagged = claims.lint(project)
    print(f"— проблем библиотеки {problems}, позиций на пересмотр {len(changed)}, фактов с флагами {flagged}")
    return 1 if problems else 0


def build_parser():
    parser = argparse.ArgumentParser(prog="eurema", description=__doc__)
    parser.add_argument("--project", help="папка проекта (по умолчанию ищется eurema.json вверх от текущей)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="создать проект")
    p.add_argument("path")
    p.add_argument("--name")
    p.add_argument("--context", help="1–2 предложения о теме для воркеров")

    sub.add_parser("status", help="где мы: последняя сессия, что висит (начало сессии)")
    sub.add_parser("finish", help="конец сессии: пересчёт позиций, lint, MAP.md, FIELD.md")
    sub.add_parser("map", help="пересобрать MAP.md")
    p = sub.add_parser("field", help="FIELD.md; refs — часто цитируемые; candidates; decide")
    p.add_argument("action", nargs="?", choices=["build", "refs", "candidates", "decide"], default="build")
    p.add_argument("id", nargs="?")
    p.add_argument("decision", nargs="?")
    p.add_argument("--why")
    p.add_argument("--min", type=int, default=2)
    p.add_argument("--all", action="store_true")

    p = sub.add_parser("search", help="поиск статей (логируется, новые → кандидаты)")
    p.add_argument("query")
    p.add_argument("--db", choices=sorted(sources.DATABASES), default="openalex")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--question")
    p = sub.add_parser("citations", help="кто цитирует статью и на что она ссылается")
    p.add_argument("ref")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--question")
    for name in ("orcid", "patents"):
        p = sub.add_parser(name)
        p.add_argument("query")
        p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("worldbank", help="индикатор, напр. GB.XPD.RSDV.GD.ZS")
    p.add_argument("indicator")
    p.add_argument("--countries", default="CHN;USA")
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("add", help="добавить статью: метаданные, PDF, текст, страницы, шапка карточки")
    p.add_argument("identifier")
    p.add_argument("--question")
    p = sub.add_parser("parse", help="текст по страницам + печатные номера + индекс")
    p.add_argument("refs", nargs="*")
    p = sub.add_parser("pages", help="печатные страницы статьи; --first N [--at PDF] задать вручную")
    p.add_argument("ref")
    p.add_argument("--first", help="печатный номер страницы PDF --at (по умолчанию 1)")
    p.add_argument("--at", type=int, default=1)
    p.add_argument("--detect", action="store_true")
    p = sub.add_parser("lookup", help="полнотекстовый поиск по страницам")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--paper", action="append")
    p.add_argument("--raw", action="store_true", help="запрос в синтаксисе FTS5")
    p = sub.add_parser("show", help="текст страницы")
    p.add_argument("ref")
    p.add_argument("--page", help="печатная страница")
    p.add_argument("--pdf-page", type=int)
    p = sub.add_parser("check", help="проверить цитату или все цитаты в тексте")
    p.add_argument("ref", nargs="?")
    p.add_argument("quote", nargs="?")
    p.add_argument("--page")
    p.add_argument("--pdf-page", type=int)
    p.add_argument("--text", help="файл или - (stdin): ищет «цитата» [Автор Год, с. N]")

    p = sub.add_parser("card", help="воркер пишет карточку статьи (README.md)")
    p.add_argument("refs", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("extract", help="воркер извлекает факты; код оставляет только проверенные цитаты")
    p.add_argument("refs", nargs="+")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry", action="store_true")
    p = sub.add_parser("reverify", help="перепроверить сохранённый ответ воркера (без вызова)")
    p.add_argument("refs", nargs="+")
    sub.add_parser("attribute", help="пересчитать атрибуцию own/cited/reported")
    p = sub.add_parser("annotate", help="воркер заполняет охват и вопросы фактов")
    p.add_argument("--batch", type=int, default=50)
    p = sub.add_parser("audit", help="независимый воркер проверяет выборку фактов (отчёт)")
    p.add_argument("--sample", type=int, default=40)
    p.add_argument("--seed", type=int)
    sub.add_parser("lint", help="ключи, карточки, позиции, флаги фактов")

    p = sub.add_parser("claims", help="список фактов")
    p.add_argument("--question")
    p.add_argument("--paper")
    p.add_argument("--unused", action="store_true", help="не используются в позициях")
    p.add_argument("--lint", action="store_true")
    p.add_argument("--all", action="store_true", help="включая неактивные")
    p = sub.add_parser("claim", help="add — факт из чата (цитата проверяется); status — сменить статус")
    p.add_argument("action", choices=["add", "status"])
    p.add_argument("target", nargs="?", help="add: статья (ключ); status: C…")
    p.add_argument("--quote")
    p.add_argument("--page")
    p.add_argument("--pdf-page", type=int)
    p.add_argument("--statement")
    p.add_argument("--type", default="finding")
    p.add_argument("--attribution", choices=["own", "cited", "reported"])
    p.add_argument("--question", action="append")
    p.add_argument("--dataset")
    p.add_argument("--file")
    p.add_argument("--how")
    p.add_argument("--status")
    p.add_argument("--reason", default="")

    p = sub.add_parser("belief", help="позиции: add | revise | drop | restore | seen | show | list")
    p.add_argument("action", choices=["add", "revise", "drop", "restore", "seen", "show", "list"])
    p.add_argument("ids", nargs="*")
    p.add_argument("--question")
    p.add_argument("--statement")
    p.add_argument("--premise", action="append")
    p.add_argument("--against", action="append")
    p.add_argument("--add-premise", action="append", default=[])
    p.add_argument("--add-against", action="append", default=[])
    p.add_argument("--reasoning")
    p.add_argument("--reason", default="")
    p.add_argument("--idea", action="store_true")
    p.add_argument("--by", choices=["claude", "user"], default="claude")
    return parser


def run(args):
    if args.command == "init":
        return cmd_init(args)
    project = Project(find_root(args.project))
    c = args.command
    if c == "status":
        reports.status(project)
    elif c == "finish":
        reports.finish(project)
    elif c == "map":
        beliefs.refresh(project)
        reports.build_map(project)
    elif c == "field":
        cmd_field(project, args)
    elif c == "search":
        sources.print_rows(sources.search(project, args.db, args.query, max(1, min(args.limit, 100)), args.question))
    elif c == "citations":
        try:
            ref = papers.resolve(project, args.ref)
        except ValueError:
            ref = args.ref
        sources.citations(project, ref, max(1, min(args.limit, 100)), args.question)
    elif c == "orcid":
        sources.orcid(args.query, args.limit)
    elif c == "patents":
        sources.patents(args.query, args.limit)
    elif c == "worldbank":
        sources.worldbank(args.indicator, args.countries, args.limit)
    elif c == "add":
        papers.add(project, args.identifier, args.question)
    elif c == "parse":
        papers.parse(project, [papers.resolve(project, r) for r in args.refs] or None)
    elif c == "pages":
        cmd_pages(project, args)
    elif c == "lookup":
        index.lookup(project, args.query, max(1, min(args.limit, 100)), args.paper, args.raw)
    elif c == "show":
        index.show(project, args.ref, args.page, args.pdf_page)
    elif c == "check":
        return cmd_check(project, args)
    elif c == "card":
        refs = project.paper_ids() if args.all else args.refs
        if not refs:
            raise ValueError("card Ключ… или --all")
        cards.generate(project, refs, args.force)
    elif c == "extract":
        claims.extract(project, args.refs, args.force, args.dry)
    elif c == "reverify":
        claims.reverify(project, args.refs)
    elif c == "attribute":
        claims.attribute(project)
    elif c == "annotate":
        claims.annotate(project, args.batch)
    elif c == "audit":
        claims.audit(project, args.sample, args.seed)
    elif c == "lint":
        return cmd_lint(project)
    elif c == "claims":
        cmd_claims(project, args)
    elif c == "claim":
        if args.action == "add":
            if not args.statement:
                raise ValueError("нужен --statement")
            if args.dataset:
                claims.add_manual(project, args.statement, kind=args.type, questions=args.question, dataset=args.dataset, file=args.file, how=args.how)
            else:
                if not args.target or not args.quote:
                    raise ValueError("claim add Ключ --quote «…» --page N --statement «…»")
                claims.add_manual(project, args.statement, args.target, args.quote, args.page, args.pdf_page, args.type, args.question, attribution=args.attribution)
        else:
            claims.set_status(project, args.target, args.status, args.reason)
            beliefs.refresh(project)
    elif c == "belief":
        a = args.action
        if a == "add":
            if not args.statement:
                raise ValueError("нужен --statement")
            beliefs.add(project, args.question, args.statement, args.premise or [], args.against or [], args.reasoning or "", "idea" if args.idea else "position", args.by)
        elif a == "revise":
            if not args.ids or not args.reason:
                raise ValueError("belief revise B… --reason «почему» [--statement …] [--premise …] [--add-premise …]")
            beliefs.revise(project, args.ids[0], args.reason, args.statement, args.premise, args.against, args.reasoning, args.add_premise, args.add_against, args.by)
        elif a in ("drop", "restore"):
            beliefs.set_status(project, args.ids[0], "dropped" if a == "drop" else "active", args.reason)
        elif a == "seen":
            beliefs.mark_seen(project, args.ids or None)
        elif a == "show":
            beliefs.show(project, args.ids[0])
        else:
            beliefs.listing(project, args.question)
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return run(args) or 0
    except (OSError, ValueError, KeyError, sqlite3.Error, *sources.NET_ERRORS) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
