"""``lumi library`` commands. Check never writes. Normalize writes only with ``--apply``."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from lumi.library import Library, check_status, report


def _library(path, project=None):
    if path is None:
        from engine.project import Project, find_root
        try:
            detected = find_root(project)
        except ValueError:
            if project:
                raise
        else:
            return Library(Project(detected).papers)
    root = Path(path or "literature")
    if not root.is_absolute():
        root = Path.cwd() / root
    return Library(root)


def add_parser(sub):
    library = sub.add_parser("library", help="folders, KEYS.tsv, INDEX.md")
    actions = library.add_subparsers(dest="action", required=True)

    check = actions.add_parser("check", help="read-only folder, KEYS, and INDEX report")
    check.add_argument("--library", help="library folder (default: detected project library)")
    check.add_argument("--project", dest="scan_project", help="markdown root for stale literature/W…/ paths (default: parent of the library)")

    normalize = actions.add_parser("normalize", help="print a rename plan; write only with --apply")
    normalize.add_argument("--library", help="library folder (default: detected project library)")
    normalize.add_argument("--apply", action="store_true", help="rename legacy folders and rewrite KEYS.tsv and INDEX.md")

    save = actions.add_parser("save", help="save a work JSON into <key>-<id>/")
    save.add_argument("work_json")
    save.add_argument("--pdf")
    save.add_argument("--library", help="library folder (default: detected project library)")

    parse = actions.add_parser("parse", help="extract paper.txt and pages.json")
    parse.add_argument("refs", nargs="*")
    parse.add_argument("--library", help="library folder (default: detected project library)")

    index = actions.add_parser("index", help="rebuild the full-text index")
    index.add_argument("--library", help="library folder (default: detected project library)")

    lookup = actions.add_parser("lookup", help="search parsed pages")
    lookup.add_argument("query")
    lookup.add_argument("--limit", type=int, default=10)
    lookup.add_argument("--paper", action="append")
    lookup.add_argument("--library", help="library folder (default: detected project library)")

    find = actions.add_parser("find", help="resolve a folder, OpenAlex id, cite key, or DOI")
    find.add_argument("ref")
    find.add_argument("--library", help="library folder (default: detected project library)")


def run(args):
    try:
        root = _library(args.library, args.project)
        if args.action == "check":
            findings = root.check(args.scan_project)
            sys.stdout.write(report(findings))
            return check_status(findings)
        if args.action == "normalize":
            if args.apply:
                actions_ = root.apply()
            else:
                actions_ = root.plan()
            if not actions_:
                print("nothing to rename")
            else:
                for action in actions_:
                    print(action.render())
            if args.apply and any(action.kind == "conflict" for action in actions_):
                return 1
            return 0
        if args.action == "save":
            payload = json.loads(Path(args.work_json).read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("work"), dict) and "display_name" not in payload and "title" not in payload:
                work = payload["work"]
            else:
                work = payload
            folder = root.save_work(work, pdf_path=args.pdf)
            print(folder)
            return 0
        if args.action == "parse":
            for line in root.parse(args.refs or None):
                print(line)
            return 1 if root.parse_errors else 0
        if args.action == "index":
            print(f"index: {root.rebuild_index()} pages")
            return 0
        if args.action == "lookup":
            for paper, page, snippet in root.lookup(args.query, args.limit, args.paper):
                print(f'{paper} p.{page} "{snippet}"')
            return 0
        if args.action == "find":
            print(root.find(args.ref))
            return 0
        raise ValueError(f"unknown library action: {args.action}")
    except (OSError, ValueError, KeyError, LookupError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0

