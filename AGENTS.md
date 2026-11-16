# Coding in Lumi

This is the development repository for the research CLI. The GitHub repository
is named `lumi`; the current CLI entry point is `lumi.py`.

- Read `README.md`, `docs/CONTEXT.md`, `docs/SPEC.md`, and `skills/lumi-research/SKILL.md` before changing behavior.
- User decisions are recorded in `docs/decisions/2026-11-16-user-answers.md`; comments override questionnaire option labels. Open engineering choices in `docs/SPEC.md` are not approved specifics.
- Lumi is a universal research CLI/skill. The thesis is a benchmark, never hard-coded product logic.
- Claims/beliefs, built-in LLM workers and dedicated dataset tools have been removed. Preserve existing user records; the CLI never opens or rewrites research/knowledge/.
- `docs/archive/` preserves history; do not treat archived plans as current instructions.
- `skills/lumi-research/SKILL.md` describes the agent workflow. This checkout is for developing the engine; do not create research records here.
- All new projects use literature/ + research/. PDF parsing and indexing live in lumi/library.py; engine/index.py only presents its results.
- Keep research datasets, PDFs, credentials, and generated projects outside this repository.
- Preserve quote verification, source provenance, printed page labels, and existing legacy data/history. This does not require keeping the claims/beliefs product model in the next version.
- Use temporary projects for verification; never migrate or modify the user's live research as a test.
- Run `python3 -m pytest -q tests` for engine changes and
  `python3 lumi.py --help` to check the CLI entry point.
- Do not install hooks or publish changes unless requested.

See `docs/development.md` for local setup and the code map.
