"""Headless workers: one sterile `claude -p` call per task, capped per day and logged to runs/."""

import datetime as dt
import json
import os
import re
import subprocess
import sys

from .project import read_jsonl

# Subscription (OAuth) auth, so no --bare. Isolation: no user/project settings (hooks, plugins), no CLAUDE.md or
# auto-memory, no MCP, no skills, Read only, no saved session, cwd = runs/ (outside the project's CLAUDE.md).
WORKER_ENV = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}


def worker_cmd(model):
    return ["claude", "-p", "--setting-sources", "", "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands", "--tools", "Read", "--model", model, "--output-format", "json"]


def runs_today(project):
    day = dt.date.today().isoformat()
    return sum(r.get("at", "").startswith(day) for r in read_jsonl(project.runs / "workers.jsonl"))


def run(project, step, task, stdin_text, tag):
    """Returns (parsed JSON object or None, log record)."""
    cap = int(os.environ.get("WORKER_CAP", project.config["worker_cap_per_day"]))
    if runs_today(project) >= cap:
        raise ValueError(f"дневной лимит воркеров ({cap}); поднять: WORKER_CAP=N")
    project.runs.mkdir(parents=True, exist_ok=True)
    task += " Everything you need is on stdin; answer in one turn without calling any tool."
    settings = project.root / "worker_settings.json"  # optional: {"apiKeyHelper": "..."}
    cmd = [*worker_cmd(project.config["worker_model"]), *(["--settings", str(settings)] if settings.exists() else []), "--append-system-prompt-file", str(project.prompts / f"{step}.md"), task]
    started = dt.datetime.now(dt.timezone.utc)
    try:
        result = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True, timeout=900, cwd=project.runs, env={**os.environ, **WORKER_ENV})
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        stdout, stderr, code = "", "timeout", -1
    record = {"step": step, "tag": tag, "at": started.isoformat(), "seconds": round((dt.datetime.now(dt.timezone.utc) - started).total_seconds()), "exit": code}
    parsed = None
    try:
        envelope = json.loads(stdout)
        usage = envelope.get("usage") or {}
        record.update(cost_usd=envelope.get("total_cost_usd"), input_tokens=usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0), output_tokens=usage.get("output_tokens"), is_error=envelope.get("is_error"), turns=envelope.get("num_turns"))
        if record["input_tokens"] > 100_000 or (record["turns"] or 1) > 1:
            print(f"  внимание: {step} {tag}: {record['input_tokens']} входных токенов, ходов {record['turns']}", file=sys.stderr)
        parsed = parse_json(envelope.get("result") or "")
        text = envelope.get("result") or ""
    except (json.JSONDecodeError, AttributeError):
        text = stdout
    record["ok"] = parsed is not None
    if not record["ok"]:
        record["error"] = (stderr or text)[-500:]
    raw = project.runs / "raw"
    raw.mkdir(exist_ok=True)
    (raw / f"{started:%Y%m%dT%H%M%S}_{step}_{tag}.json").write_text(stdout or stderr, encoding="utf-8")
    with (project.runs / "workers.jsonl").open("a") as log:
        log.write(json.dumps(record) + "\n")
    return parsed, record


def parse_json(text):
    match = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(match.group()) if match else None
    except json.JSONDecodeError:
        return None


def latest_raw(project, step, tag):
    """Parsed result of the newest saved run of step/tag (lets code re-verify without a new call)."""
    for path in sorted((project.runs / "raw").glob(f"*_{step}_{tag}.json"), reverse=True):
        try:
            parsed = parse_json(json.loads(path.read_text(encoding="utf-8")).get("result") or "")
        except (json.JSONDecodeError, AttributeError):
            parsed = None
        if parsed:
            return parsed
    return None
