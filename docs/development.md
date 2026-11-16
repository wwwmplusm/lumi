# Development

Canonical checkout: `/home/kesh/MyCode/lumi`.
Remote: https://github.com/wwwmplusm/lumi

Python 3.11+, standard library only. Tests require pytest:

```sh
uv run --no-project --with pytest python -m pytest -q tests
python3 lumi.py --help
```

PDF operations use Poppler (`pdftotext`, `pdfinfo`) and `qpdf`.
Use temporary projects outside this checkout for verification. Never migrate or
modify live research as a test. No LLM workers or hooks are part of the package.

## Code map

- `lumi/cli.py`, `lumi/library_cli.py`: CLI commands.
- `lumi/library.py`: storage, catalog, the single PDF parser and FTS index.
- `lumi/naming.py`: citation keys, stable folder names and reference resolution.
- `engine/project.py`: project discovery, research questions and search history.
- `engine/sources.py`: paginated scientific search, native provider metadata and direct citation neighbors.
- `engine/papers.py`: source acquisition, metadata and printed page labels.
- `engine/index.py`: human-readable search/page output over `Library`.
- `engine/text.py`, `engine/quotes.py`: deterministic quote verification.
- `engine/reports.py`: library status and discovery reports.
- `engine/onboarding.py`: API credentials and diagnostics.
- `skills/lumi-research/SKILL.md`: agent workflow, shipped in the package.
- `templates/`: new projects use `literature/` + `research/`.
- `docs/SPEC.md`, `docs/PLAN.md`: target requirements and remaining work.
- `docs/CONTEXT.md`: observed implementation state.
- `docs/archive/`, `docs/decisions/`: history; not installed runtime instructions.

The installed `lumi` is a package snapshot. After changing code:

```sh
uv tool install --force --reinstall .
```

`lumi.py` and `python -m lumi` are small entry-point adapters to the same CLI.
Keep the old `research/index.sqlite`, knowledge records, notes and originals
untouched. All current lookup commands share `literature/.lumi/index.sqlite`.

## Audit regression coverage

`tests/test_audit_regressions.py` covers partial extraction, preservation on reparse,
exit codes, multilingual lookup and the prohibition on opening legacy knowledge.
`tests/test_search_pagination.py` covers page boundaries, provider errors and limits,
repeated cursors, reference lists longer than 200, and native-ID search/add/re-add.
All fixtures are temporary; connector responses are mocked. A green run does not
certify current API access, quotas, OCR quality or research usefulness.

Pagination contracts checked on 2026-11-16:

- [OpenAlex](https://help.openalex.org/api/paging/): cursor, supported page size 100; key required after first cursor page.
- [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/): cursor; short page ends iteration.
- [Semantic Scholar](https://api.semanticscholar.org/api-docs/graph): offset/next, 100 per page and 1000 relevance results.
- [Lens](https://docs.api.lens.org/request-scholar.html): scroll/scroll_id; HTTP 204 ends iteration.
- [CORE](https://api.core.ac.uk/docs/v3): works metadata and offset/limit search.
- [arXiv](https://info.arxiv.org/help/api/user-manual.html): start/max_results; delay between pages.
