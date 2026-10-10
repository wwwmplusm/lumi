"""Project layout: where a project's files live, its config, questions and JSONL stores."""

import datetime as dt
import json
import os
import re
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
MARKER = "eurema.json"
DEFAULTS = {"name": "", "context": "", "worker_model": "sonnet", "worker_cap_per_day": 40}


def today():
    return dt.date.today().isoformat()


def find_root(start=None):
    """--project / EUREMA_PROJECT / nearest parent of cwd that has eurema.json."""
    if start:
        return Path(start).resolve()
    if os.environ.get("EUREMA_PROJECT"):
        return Path(os.environ["EUREMA_PROJECT"]).resolve()
    here = Path.cwd().resolve()
    for folder in (here, *here.parents):
        if (folder / MARKER).exists():
            return folder
    raise ValueError(f"не найден {MARKER}: запустите из папки проекта, задайте --project или EUREMA_PROJECT")


def read_jsonl(path):
    path = Path(path)
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def append_jsonl(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class Project:
    def __init__(self, root):
        self.root = Path(root)
        self.papers = self.root / "sources" / "papers"
        self.datasets = self.root / "sources" / "datasets"
        self.field = self.root / "field"
        self.knowledge = self.root / "knowledge"
        self.notes = self.root / "notes"
        self.sessions = self.root / "sessions"
        self.runs = self.root / "runs"
        self.cache = self.root / ".cache"
        self.index_db = self.cache / "index.sqlite"
        self.prompts = ENGINE / "prompts"
        config = self.root / MARKER
        self.config = {**DEFAULTS, **(json.loads(config.read_text(encoding="utf-8")) if config.exists() else {})}

    # --- stores
    def claims(self):
        return read_jsonl(self.knowledge / "claims.jsonl")

    def save_claims(self, claims):
        write_jsonl(self.knowledge / "claims.jsonl", claims)

    def belief_rows(self):
        return read_jsonl(self.knowledge / "beliefs.jsonl")

    def save_beliefs(self, rows):
        write_jsonl(self.knowledge / "beliefs.jsonl", rows)

    def search_log(self):
        return read_jsonl(self.field / "search_log.jsonl")

    def candidates(self):
        return read_jsonl(self.field / "candidates.jsonl")

    def save_candidates(self, rows):
        write_jsonl(self.field / "candidates.jsonl", rows)

    def paper_ids(self):
        return sorted(p.parent.name for p in self.papers.glob("*/metadata.json"))

    def questions(self):
        """`- ID: question | keywords: a, b` lines of questions.md."""
        path = self.root / "questions.md"
        out = []
        for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
            match = re.match(r"\s*[-*]\s+(\w+):\s*(.+?)(?:\s*\|\s*keywords:\s*(.+))?$", line)
            if match:
                out.append({"id": match.group(1), "question": match.group(2).strip(), "keywords": [k.strip() for k in (match.group(3) or "").split(",") if k.strip()]})
        return out


def next_id(items, prefix, width=4):
    numbers = [int(x["id"][len(prefix):]) for x in items if str(x.get("id", "")).startswith(prefix) and x["id"][len(prefix):].isdigit()]
    return f"{prefix}{max(numbers, default=0) + 1:0{width}d}"
