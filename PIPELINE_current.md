# Knowledge pipeline — reference

State as of 2026-10-10 (commit 234f392). One script, `research/library.py`; no Zotero or hosted services. LLM steps run as sterile headless workers that `library.py` launches itself. Design rationale: `research/tooling_design_2026-10-09/HANDOFF.md` (Session 5). Run history: `sessions/2026-10-09-night.md`.

## What it does

Papers go in. A versioned, checkable base of **claims** comes out. Each claim is backed by a verbatim quote with a page number, and claims are linked to each other and to the thesis questions.

```
add ─▶ parse ─▶ extract ─▶ (verify) ─▶ attribute/annotate/lint ─▶ candidates ─▶ link ─▶ review ─▶ propagate ─▶ map / gaps
 API     no LLM   worker      code        code / worker / code        code        worker   USER       code         code
```

The roles are split as follows:
- **Code** does everything mechanical: search, parsing, indexing, quote checks, propagation, reports.
- **Workers** make judgements: they extract, annotate, link and audit claims.
- **The user** owns questions and conclusions: confirms edges, edits `questions.md`, spot-checks claims.

## Current state

| | |
|---|---|
| Papers | 24 folders in `literature/`; 13 parsed with full text (340 pages); the rest are metadata only |
| Claims | 240 from 13 papers; every quote located on its page |
| Attribution | own 216 · cited 21 · reported 3; 16 findings downgraded to assumption |
| Lint | 57/240 statements contain numbers absent from their quote |
| Edges | 637: unrelated 527 · supports 100 · differs_in_scope 8 · same 1 · depends_on 1 · contradicts 0. All `candidate` (none reviewed) |
| Questions | RQ, H1–H4, D1–D2 all "covered" at the low bar (≥2 own findings from ≥2 papers); H1, H2, H4, D2 rest on exactly 2 papers |
| Workers used | 44 runs, ≈ $6.6 total |
| Tests | 22 passing |

## Files

| Path | What | Who writes |
|---|---|---|
| `literature/W*/metadata.json`, `README.md` | OpenAlex record, reading notes | `add` |
| `literature/W*/paper.pdf`, `paper.refetch.pdf` | PDF (git-ignored); the refetch copy replaces a truncated download | `add` / manual |
| `literature/W*/pages.json` | `pages` (reading order) + `pages_layout` (`-layout`, used as fallback for formulas and tables) | `parse` |
| `research/index.sqlite` | FTS5 tables `pages` and `claims` (git-ignored, rebuilt automatically) | `parse`, every step |
| `research/questions.md` | Thesis questions with keywords; **user-owned** (currently a draft copied from the project card) | user |
| `research/knowledge/claims.jsonl` | Claims, one JSON object per line | extract, attribute, annotate, lint, propagate |
| `research/knowledge/candidates.jsonl` | Claim pairs to classify | `candidates` |
| `research/knowledge/edges.jsonl` | Relations between claims | `link`, `review` |
| `research/knowledge/MAP.md`, `GAPS.md` | Generated reports (do not edit) | `map`, `gaps` |
| `research/prompts/{extract,annotate,link,audit}.md` | Worker system prompts (the only LLM instructions) | — |
| `research/runs/workers.jsonl` | One line per worker run: step, tag, seconds, cost, tokens, turns, ok/error | `run_worker` |
| `research/runs/raw/` | Raw worker output (lets `reverify` run with no new call) | `run_worker` |
| `research/runs/audit_*.jsonl`, `dry_extract_*.jsonl` | Audit results, prompt-comparison runs | `audit`, `extract --dry` |
| `research/tests/` | pytest; workers are mocked | — |
| `research/NIGHT_PLAN.md` | Last autonomous run's plan, items ticked | — |

## Data model

**Claim** (`claims.jsonl`):

| Field | Meaning |
|---|---|
| `id` | `C0001`… never reused |
| `statement` | One-sentence English paraphrase |
| `type` | finding · assumption · hypothesis · definition. `type_extracted` keeps the original when attribution downgrades it |
| `status` | active · contested · retracted · superseded. Records are never deleted |
| `evidence[]` | `{paper, page, quote}`, plus `verified_on: "pdftotext -layout"` when the quote is found only in layout text |
| `source_depth` | `fulltext` (abstract-only claims must never go into the thesis) |
| `attribution` | own · cited · reported, with `attribution_by`, `attribution_marker` (citation marker found by code) and `attribution_worker` |
| `population`, `period`, `metric` | Scope, so studies can be compared; `"unspecified"` if the paper doesn't say |
| `questions` | Ids from `questions.md` that the claim is direct evidence for |
| `unquoted_numbers` | Numbers in the statement that are missing from the quote (lint flag) |
| `depends_on`, `contradicts`, `superseded_by`, `contested_reason` | Truth-maintenance fields |
| `created`, `updated` | Dates |

**Edge** (`edges.jsonl`): `{a, b, relation, note, status, by, at, history[], proposed_relation, reviewed}`
- `relation`: same · refines · supports · contradicts · differs_in_scope · depends_on · unrelated
- `status`: candidate (set by a worker) → confirmed | rejected (set only by the user)
- `history` keeps earlier labels after `link --relink`; `proposed_relation` keeps the worker's label after the user relabels an edge

## Commands

Run all commands as `python3 research/library.py <command>`. Output is compact by design: one line per paper or batch.

**Library (no LLM).** `search`, `arxiv`, `s2`, `crossref`, `core`, `orcid`, `lens`, `patents`, `worldbank`, `citations`, `add DOI|arXiv|W…` — unchanged from before.

**Text and index (no LLM)**
- `parse [W…]` — per-page text for both versions, then rebuilds FTS5.
- `lookup "query" [--limit N] [--paper W…]` — BM25 search over pages → `W123 p.14 "…snippet…"`.
- `show W --page N` — full text of one page.

**Claims**
- `extract W… [--dry] [--force]` — one worker per paper. Then code verification: claims whose quote is not in the paper are dropped and wrong page numbers are fixed. Attribution and lint run at the same time. Papers already extracted are skipped. `--dry` writes to `runs/` only (for comparing prompts); `--force` appends (nothing is replaced).
- `reverify W…` — re-checks saved raw output against the current pages, with no worker call.
- `attribute` — re-runs the attribution rules over all claims (no LLM).
- `annotate [--batch 50]` — worker fills scope, question tags and its opinion on attribution, for claims that lack them.
- `lint` — flags statements with numbers absent from the quote (no LLM).
- `audit [--sample 40] [--seed N]` — an independent worker grades a random sample (fidelity, type, attribution, scope). Report only; claims are not changed.

**Links**
- `candidates [--k 3] [--max-bm25 -8]` — BM25 neighbours of each claim among other papers' claims.
- `link [--batch 40] [--max-batches 10] [--relink]` — worker labels the pairs; edges are always `candidate`. Stops after 3 consecutive failures.
- `review [--relation R…]` — interactive: y = confirm, n = reject, s = differs in scope, r = relabel, Enter = skip, q = quit. By default it shows everything except supports and unrelated.
- `review --set C0001 C0042 confirmed|rejected|<relation>` — the same, non-interactive.

**Reasoning and reports (no LLM)**
- `propagate` — any active claim whose premise (`depends_on` or a **confirmed** depends_on edge) is no longer active becomes `contested`, transitively. Candidate edges never propagate.
- `map` → `knowledge/MAP.md` — claims per paper, edges for review, claims that are not active.
- `gaps` → `knowledge/GAPS.md` — per question: tagged claims; covered = ≥2 own fulltext findings from ≥2 papers. For gaps it also lists library pages that match the keywords but have no claims yet, and the next search to run.

**Tests.** `uv run --no-project --with pytest python -m pytest -q research/tests`

## Rules that keep it honest

1. **Quotes are checked by code.** A claim enters the base only if its quote is found in the page text. Matching tolerates whitespace, hyphenation, ligatures and quote styles, and falls back to layout text.
2. **Numbers are checked by code.** A statement whose numbers are not in the quote is flagged. On the audit sample this caught 7/7 of the errors the auditor found.
3. **Attribution.** Code looks for citation and news markers; the worker judges whether the claim is the paper's own. News markers always win. A citation marker plus a worker verdict of "own" stays own, because methods sections cite tools. Only own claims count as findings and count towards coverage.
4. **Contradiction needs the same scope.** Otherwise the relation is `differs_in_scope`. All 3 early "contradictions" turned out to be scope differences.
5. **Nothing is deleted.** Status changes, `history`, `type_extracted` and raw outputs keep the trail; git versions everything except PDFs and the index.
6. **The user owns conclusions.** Workers only propose edges; only `review` confirms them.

## Workers

A worker is one call per task. The input arrives on stdin, the worker answers in a single turn, and code checks the result.

```
CLAUDE_CODE_DISABLE_CLAUDE_MDS=1 CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 \
claude -p --setting-sources "" --no-session-persistence --strict-mcp-config \
  --disable-slash-commands --tools Read --model sonnet --output-format json \
  --append-system-prompt-file research/prompts/<step>.md "<task>"   # cwd = research/runs
```

- **Auth:** the Claude Code subscription (OAuth). `--bare` is not used because it accepts only `ANTHROPIC_API_KEY`. An optional `research/worker_settings.json` is passed through `--settings`.
- **Isolation, verified by a probe:** tools `[Read]`, no MCP, skills, slash commands, CLAUDE.md, AGENTS.md or user hooks. Base context is about 6k tokens.
- **Cap:** `WORKER_CAP` (default 40) counts the lines in `workers.jsonl`. For more runs, set `WORKER_CAP=63` or higher explicitly.
- **Cost per run:** extract ≈ $0.13–0.23 per paper (30–50k input tokens in a single turn); link ≈ $0.15 per 40 pairs; annotate ≈ $0.17 per 50 claims; audit ≈ $0.17 per 40 claims.
- **Warnings:** printed to stderr when a run uses more than 100k input tokens or more than one turn.

## Typical session

```bash
L="python3 research/library.py"
$L add 10.xxxx/yyyy && $L parse W…        # new paper
$L extract W… && $L annotate && $L lint   # claims
$L candidates && $L link                  # proposed relations
$L review                                 # you: confirm / reject
$L propagate && $L map && $L gaps         # reports
$L audit --sample 20                      # occasional quality check
```

## Known limits and open decisions

| Issue | Evidence | Options |
|---|---|---|
| A single extraction pass is unstable | Two runs on the same paper share only 10/20 and 3/15 quotes | 2 passes + merge by quote (≈2× cost) · per-section extraction |
| Candidate pairs are mostly noise | 83% of linked pairs unrelated (527/637); BM25 cannot go below ~74% at useful recall | local embeddings (new dependency) · link only pairs sharing a question tag |
| 57 claims flagged by lint | 24% of the base | re-extract with the stricter prompt (≈10 runs) · review by hand |
| No confirmed edges | `review` never run | 10 edges currently waiting (MAP.md, "For review") |
| Thin evidence per question | H1, H2, H4, D2 rest on 2 papers each | more papers: see "Next search" in GAPS.md |
| `questions.md` is an agent draft | Copied from the project card | user to edit; keywords matter only for the "unextracted pages" hints |
| Not yet built | — | `drafts/` layer (thesis text citing claim IDs), superseding claims, abstract-only triage step, automatic session digest |
