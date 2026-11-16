"""Lumi — поиск источников, чтение и проверка цитат."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from engine import onboarding, index, papers, quotes, reports, sources
from engine.project import ENGINE, MARKER, Project, find_root
from engine.text import snippet


def install_files(root, name):
    import shlex
    values = {"{{PYTHON}}": shlex.quote(sys.executable), "{{NAME}}": name,
              "{{ENGINE_REL}}": os.path.relpath(ENGINE, root)}
    files = [("templates/CONTEXT.md", "CONTEXT.md"),
             ("templates/questions.md", "research/questions.md"),
             ("templates/AGENTS.md", "AGENTS.md"),
             ("templates/gitignore", ".gitignore"), ("templates/lumi", "lumi"),
             ("skills/lumi-research/SKILL.md", ".agents/skills/lumi-research/SKILL.md")]
    for src, dst in files:
        target = root / dst
        if target.exists():
            continue
        text = (ENGINE / src).read_text(encoding="utf-8")
        for key, value in values.items():
            text = text.replace(key, value)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if not (root / "CLAUDE.md").exists():
        (root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    (root / "lumi").chmod(0o755)


def cmd_init(args):
    root = Path(args.path).resolve()
    if (root / MARKER).exists() or (root / "literature").exists():
        raise ValueError(f"{root}: проект уже существует; используйте lumi --project ПУТЬ status")
    if (root / "eurema.json").exists() or (root / "sources" / "papers").exists():
        raise ValueError("старый проект: init не мигрирует и не изменяет существующие данные")
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("literature", "research/field"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    config = {"name": args.name or root.name, "context": args.context or ""}
    (root / MARKER).write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    install_files(root, config["name"])
    print(f"проект создан: {root}\nКонтекст: CONTEXT.md; вопросы: research/questions.md")


def cmd_pages(project, args):
    identifier = papers.resolve(project, args.ref)
    if args.first is not None:
        meta = papers.set_pages_manual(project, identifier, args.first, args.at)
    else:
        meta = papers.load_meta(project, identifier)
        if args.detect:
            result = papers.parse(project, [identifier])
            if result:
                return result
            meta = papers.load_meta(project, identifier)
    labels = meta.get("page_labels") or []
    print(f"{papers.label(meta)}: печатные страницы — {meta.get('page_source')}" + (f" ({meta.get('page_note')})" if meta.get("page_note") else ""))
    if labels:
        print("  PDF→печатная: " + ", ".join(f"{i}→{x or '·'}" for i, x in enumerate(labels[:8], 1)) + (" …" if len(labels) > 8 else ""))
    return 0

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
        print(f"{args.id}: {args.decision}" + (" — добавить: `lumi add " + args.id + "`" if args.decision == "include" else ""))
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

def build_parser():
    parser = argparse.ArgumentParser(prog="lumi", description=__doc__)
    parser.add_argument("--project", help="папка проекта (по умолчанию определяется вверх от текущей)")
    sub = parser.add_subparsers(dest="command", required=True)
    from .library_cli import add_parser as add_library_parser
    add_library_parser(sub)

    setup = sub.add_parser("setup", help="первичная настройка API и проверка зависимостей")
    setup.add_argument("--non-interactive", action="store_true", help="сохранить доступные переменные окружения без вопросов")
    setup.add_argument("--check", action="store_true", help="после настройки проверить API запросами")
    doctor = sub.add_parser("doctor", help="проверить зависимости и настройки без показа ключей")
    doctor.add_argument("--online", action="store_true", help="проверить API живыми запросами (расходует квоты)")

    p = sub.add_parser("init", help="создать проект")
    p.add_argument("path")
    p.add_argument("--name")
    p.add_argument("--context", help="краткий контекст исследования")

    sub.add_parser("status", help="библиотека, вопросы и история поиска")
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
    p = sub.add_parser("add", help="добавить статью: метаданные, открытый PDF, текст и каталог")
    p.add_argument("identifier")
    p.add_argument("--question")
    p = sub.add_parser("parse", help="текст по страницам и печатные номера")
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

    return parser

def run(args):
    if getattr(args, "limit", 1) < 1:
        raise ValueError("--limit должен быть положительным числом")
    if args.command == "library":
        from .library_cli import run as run_library
        return run_library(args)
    if args.command == "setup":
        return onboarding.setup(args.non_interactive, args.check)
    if args.command == "doctor":
        return onboarding.doctor(args.online)
    if args.command == "init":
        return cmd_init(args)
    project = Project(find_root(args.project))
    c = args.command
    if c == "status":
        reports.status(project)
    elif c == "field":
        cmd_field(project, args)
    elif c == "search":
        rows = sources.search(project, args.db, args.query, args.limit, args.question)
        sources.print_rows(rows)
        return sources.result_status(rows)
    elif c == "citations":
        try:
            folder = papers.resolve(project, args.ref)
            ref = papers.load_meta(project, folder)["work"]["id"]
        except ValueError:
            ref = args.ref
        return sources.citations(project, ref, args.limit, args.question)
    elif c == "add":
        identifier = papers.add(project, args.identifier, args.question)
        return 0 if papers.load_meta(project, identifier)["fulltext"] == "yes" else 1
    elif c == "parse":
        return papers.parse(project, [papers.resolve(project, r) for r in args.refs] or None)
    elif c == "pages":
        return cmd_pages(project, args)
    elif c == "lookup":
        index.lookup(project, args.query, args.limit, args.paper, args.raw)
    elif c == "show":
        index.show(project, args.ref, args.page, args.pdf_page)
    elif c == "check":
        return cmd_check(project, args)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        if sys.stdin.isatty():
            argv = ["setup"]
        else:
            build_parser().print_help()
            return 0
    args = build_parser().parse_args(argv)
    try:
        onboarding.load_credentials()
        return run(args) or 0
    except (OSError, ValueError, KeyError, sqlite3.Error, *sources.NET_ERRORS) as exc:
        print(f"Ошибка: {onboarding.redact(str(exc))}", file=sys.stderr)
        return 2
