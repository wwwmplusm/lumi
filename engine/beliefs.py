"""Beliefs (positions): versioned conclusions resting on premises — claims, other beliefs, notes.

One JSONL line per version. A new version supersedes the old one; nothing is deleted.
Code checks premises exist, sends a belief to `review` when a premise stops being active,
and derives confidence by rule.
"""

import re

from . import claims as claims_mod, papers
from .project import next_id, today
from .text import snippet

STATUSES = {"active", "review", "superseded", "dropped"}


def current(rows):
    """id -> newest version."""
    out = {}
    for row in rows:
        if row["id"] not in out or row["v"] > out[row["id"]]["v"]:
            out[row["id"]] = row
    return out


def history(rows, belief_id):
    return sorted((r for r in rows if r["id"] == belief_id), key=lambda r: r["v"])


# --- premise references -------------------------------------------------------------------

def note_ref(project, ref):
    """'N:notes/x.md#Heading' -> (path, heading)."""
    body = ref[2:]
    path, _, heading = body.partition("#")
    return project.root / path, heading


def note_label(project, ref):
    path, heading = note_ref(project, ref)
    if not heading and path.exists() and path.suffix == ".md":
        match = re.search(r"^#+\s+(.+)$", path.read_text(encoding="utf-8", errors="ignore"), re.M)
        heading = match.group(1).strip() if match else ""
    rel = path.relative_to(project.root).with_suffix("") if path.is_relative_to(project.root) else path
    return f"{rel}" + (f" · «{heading}»" if heading else "")


def premise_problem(project, ref, claims_by_id, beliefs_now):
    """None if the premise is usable, else a human reason."""
    if ref.startswith("C"):
        claim = claims_by_id.get(ref)
        if not claim:
            return f"нет факта {ref}"
        return None if claim["status"] == "active" else f"факт {ref} стал {claim['status']}"
    if ref.startswith("B"):
        belief = beliefs_now.get(ref)
        if not belief:
            return f"нет позиции {ref}"
        return None if belief["status"] == "active" else f"позиция {ref} в статусе {belief['status']}"
    if ref.startswith("N:"):
        path, heading = note_ref(project, ref)
        if not path.exists():
            return f"нет заметки {path.relative_to(project.root) if path.is_relative_to(project.root) else path}"
        if heading and path.suffix == ".md" and heading not in path.read_text(encoding="utf-8", errors="ignore"):
            return f"в заметке нет заголовка «{heading}»"
        return None
    return f"непонятная ссылка {ref} (нужно C…, B… или N:notes/файл.md)"


def validate_refs(project, refs, claims_by_id, beliefs_now, self_id=None):
    for ref in refs:
        if ref == self_id:
            raise ValueError("позиция не может опираться на себя")
        problem = premise_problem(project, ref, claims_by_id, beliefs_now)
        if problem and (problem.startswith("нет") or problem.startswith("непонятная") or problem.startswith("в заметке")):
            raise ValueError(problem)


# --- confidence by rule --------------------------------------------------------------------

def supporting_claims(belief, claims_by_id, beliefs_now, seen=None):
    seen = seen or set()
    out = []
    for ref in belief.get("premises") or []:
        if ref in seen:
            continue
        seen.add(ref)
        if ref.startswith("C") and ref in claims_by_id:
            out.append(claims_by_id[ref])
        elif ref.startswith("B") and ref in beliefs_now:
            out += supporting_claims(beliefs_now[ref], claims_by_id, beliefs_now, seen)
    return out


def confidence(belief, claims_by_id, beliefs_now, same_as=None):
    """high: ≥2 own fulltext findings from ≥2 independent papers and nothing against;
    low: fewer than 2 papers, or something against; medium otherwise."""
    if belief.get("kind") == "idea":
        return "idea"
    same_as = same_as or {}
    source = lambda c: same_as.get(claims_mod.paper_of(c), claims_mod.paper_of(c)) or c["evidence"][0].get("dataset")
    active = [c for c in supporting_claims(belief, claims_by_id, beliefs_now) if c["status"] == "active"]
    own = [c for c in active if c["type"] == "finding" and c.get("attribution", "own") == "own" and c.get("source_depth") in ("fulltext", "dataset")]
    against = [r for r in belief.get("against") or [] if not (r.startswith("C") and claims_by_id.get(r, {}).get("status") not in (None, "active"))]
    if len({source(c) for c in active}) < 2 or against:
        return "low"
    return "high" if len({source(c) for c in own}) >= 2 else "medium"


def same_as_map(project):
    return {i: m.get("same_as") for i, m in papers.all_meta(project).items() if m and m.get("same_as")}


def refresh(project, rows=None, quiet=False):
    """Propagate: active beliefs with a broken premise go to review (transitively); recompute confidence."""
    rows = rows if rows is not None else project.belief_rows()
    claims_by_id = {c["id"]: c for c in project.claims()}
    same_as = same_as_map(project)
    changed = []
    while True:
        now = current(rows)
        step = []
        for belief in now.values():
            if belief["status"] != "active":
                continue
            problems = [p for p in (premise_problem(project, r, claims_by_id, now) for r in belief.get("premises") or []) if p]
            if problems:
                belief.update(status="review", status_reason="; ".join(problems), review_since=today(), seen_by_user=False)
                step.append(belief["id"])
        if not step:
            break
        changed += step
    now = current(rows)
    for belief in now.values():
        belief["confidence"] = confidence(belief, claims_by_id, now, same_as)
    project.save_beliefs(rows)
    if changed and not quiet:
        for bid in changed:
            print(f"на пересмотр: «{snippet(now[bid]['statement'], 80)}» ({bid}) — {now[bid]['status_reason']}")
    return changed


# --- commands ----------------------------------------------------------------------------------

def add(project, question, statement, premises=(), against=(), reasoning="", kind="position", by="claude"):
    rows = project.belief_rows()
    claims_by_id = {c["id"]: c for c in project.claims()}
    now = current(rows)
    validate_refs(project, [*premises, *against], claims_by_id, now)
    if kind == "position" and not premises:
        raise ValueError("у позиции должны быть посылки (или --idea для догадки без опоры)")
    known = {q["id"] for q in project.questions()}
    if question and known and question not in known:
        raise ValueError(f"нет вопроса {question} в questions.md ({', '.join(sorted(known))})")
    belief = {"id": next_id(rows, "B", 3), "v": 1, "question": question, "statement": statement.strip(), "kind": kind,
              "confidence": None, "premises": list(premises), "against": list(against), "reasoning": reasoning.strip(),
              "status": "active", "status_reason": "", "by": by, "seen_by_user": by == "user", "date": today()}
    rows.append(belief)
    refresh(project, rows, quiet=True)
    print(f"{belief['id']} v1 · {belief['confidence']}: «{snippet(statement, 100)}»")
    return belief


def revise(project, belief_id, reason, statement=None, premises=None, against=None, reasoning=None, add_premises=(), add_against=(), by="claude"):
    rows = project.belief_rows()
    now = current(rows)
    old = now.get(belief_id)
    if not old:
        raise ValueError(f"нет позиции {belief_id}")
    new = {**old, "v": old["v"] + 1, "date": today(), "by": by, "seen_by_user": by == "user", "status": "active", "status_reason": reason}
    new.pop("review_since", None)
    if statement:
        new["statement"] = statement.strip()
    if premises is not None:
        new["premises"] = list(premises)
    if against is not None:
        new["against"] = list(against)
    if reasoning is not None:
        new["reasoning"] = reasoning.strip()
    new["premises"] = list(dict.fromkeys([*new["premises"], *add_premises]))
    new["against"] = list(dict.fromkeys([*new["against"], *add_against]))
    claims_by_id = {c["id"]: c for c in project.claims()}
    validate_refs(project, [*new["premises"], *new["against"]], claims_by_id, now, belief_id)
    old.update(status="superseded", status_reason=reason)
    rows.append(new)
    refresh(project, rows, quiet=True)
    print(f"{belief_id} v{new['v']} · {new['confidence']}: «{snippet(new['statement'], 100)}» (было v{old['v']}: {reason})")
    return new


def set_status(project, belief_id, status, reason):
    if status not in {"dropped", "active", "review"}:
        raise ValueError("статус: dropped | active | review")
    rows = project.belief_rows()
    belief = current(rows).get(belief_id)
    if not belief:
        raise ValueError(f"нет позиции {belief_id}")
    belief.update(status=status, status_reason=reason, date=today())
    refresh(project, rows, quiet=True)
    print(f"{belief_id} → {status}: {reason}")


def mark_seen(project, ids=None):
    rows = project.belief_rows()
    now = current(rows)
    targets = ids or [b for b, row in now.items() if not row.get("seen_by_user")]
    for bid in targets:
        if bid not in now:
            raise ValueError(f"нет позиции {bid}")
        now[bid]["seen_by_user"] = True
    project.save_beliefs(rows)
    print(f"просмотрено: {len(targets)}")


def ref_line(project, ref, claims_by_id, beliefs_now, metas):
    """Human line for one premise: quote + citation for a fact, statement for a belief, file for a note."""
    if ref.startswith("C") and ref in claims_by_id:
        claim = claims_by_id[ref]
        ev = claim["evidence"][0]
        status = "" if claim["status"] == "active" else f" [{claim['status']}]"
        if ev.get("dataset"):
            return f"{snippet(claim['statement'], 160)} — {claims_mod.cite(project, claim, metas)} ({snippet(ev.get('how', ''), 80)}){status} · {ref}"
        meta = metas.get(ev["paper"]) or papers.load_meta(project, ev["paper"])
        return f"«{snippet(ev['quote'], 200)}» — {claims_mod.cite(project, claim, metas)} «{papers.short_title(meta['work'])}»{status} · {ref}"
    if ref.startswith("B") and ref in beliefs_now:
        b = beliefs_now[ref]
        return f"→ позиция «{snippet(b['statement'], 100)}» ({b['confidence']}, {b['status']}) · {ref}"
    if ref.startswith("N:"):
        return f"заметка {note_label(project, ref)}"
    return f"{ref} (не найдено)"


def show(project, belief_id):
    rows = project.belief_rows()
    now = current(rows)
    if belief_id not in now:
        raise ValueError(f"нет позиции {belief_id}")
    claims_by_id = {c["id"]: c for c in project.claims()}
    metas = papers.all_meta(project)
    b = now[belief_id]
    print(f"{b['statement']}\n{b['confidence']} · v{b['v']} · {b['status']} · вопрос {b.get('question') or '—'} · {belief_id}")
    if b.get("reasoning"):
        print(f"Почему: {b['reasoning']}")
    for ref in b.get("premises") or []:
        print(f"  ✓ {ref_line(project, ref, claims_by_id, now, metas)}")
    for ref in b.get("against") or []:
        print(f"  ✗ против: {ref_line(project, ref, claims_by_id, now, metas)}")
    versions = history(rows, belief_id)
    if len(versions) > 1:
        print("  история: " + " → ".join(f"v{r['v']} «{snippet(r['statement'], 60)}»" + (f" ({r['status_reason']})" if r['status'] == 'superseded' and r.get('status_reason') else "") for r in versions))


def listing(project, question=None):
    now = current(project.belief_rows())
    for b in sorted(now.values(), key=lambda r: (r.get("question") or "", r["id"])):
        if question and b.get("question") != question:
            continue
        mark = " ●" if not b.get("seen_by_user") else ""
        print(f"{b.get('question') or '—'} · «{snippet(b['statement'], 90)}» · {b['confidence']} · {b['status']} · v{b['v']} · {b['id']}{mark}")
