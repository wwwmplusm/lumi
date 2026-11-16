---
name: lumi-research
description: Search and extend a Lumi research library, read stored papers, and verify quotations against source pages using the lumi CLI.
---

Use `lumi` in the project or `lumi --project PATH …` elsewhere. Existing
`literature/` + `research/` projects need no initialization or migration.

For discovery, read the project's context and existing library first. Propose a
short search plan suited to the question. Use `search "QUERY" --db openalex`
(or arxiv, crossref, s2, core, lens), `citations ID`, and `field candidates`.
Search results record candidates and distinguish papers already in the library.
Citation traversal is one hop. Set `--limit N` for the task; search pages are fetched
until that limit, API exhaustion or an explicit failure. Citation output is limited
to N per direction. Do not infer field coverage from it or from a lack of new results.
The caller manages further rounds; cursors are not persisted between invocations.

Rank the collected batch and explain its relevance and gaps. When obtaining full
texts requires the user's decision, review the batch once per round; reuse any
existing authorization. `add DOI_OR_ID` saves metadata, catalog entries and an openly available copy when
found. Native `core:`, `lens:` and `s2:` IDs are supported, including papers without DOI. It preserves existing cards, notes and extracted text.
Inspect download/extraction results: metadata or an abstract is not a read paper.

For a question about an existing paper, go directly to `lookup "TERMS"`,
`show KEY --pdf-page N` and `check KEY "QUOTE" --pdf-page N`.
Use `--page` only for known printed labels; `pages KEY` shows the mapping.
`check --text FILE` checks quotations with references such as
`«original words» [Author Year, PDF-с. 5]`. A successful text match does not
validate the interpretation; assess methods, sample, dates and limitations.

`parse KEY` uses the recorded PDF version, including an existing OCR PDF.
It does not perform OCR. Empty pages produce a partial extraction result and exit 1;
inspect the listed PDF pages. Nonempty text on every page does not prove OCR accuracy.
Partial re-extraction preserves existing text. Report unreadable scans honestly.
`library check` is read-only; `library normalize` previews renames and requires
`--apply` to write. Personal notes and the old research knowledge files remain
user-owned. Give the answer in chat; create a reading list or document when useful.
The CLI does not run an LLM or maintain claims/beliefs. It currently prints text,
not a general JSON protocol. Exit 1 for `add` means metadata or incomplete text was
saved; exit 1 for search means a partial/failed request. Inspect messages and retained
results before continuing. Exit 2 indicates an invocation/configuration or other error.
