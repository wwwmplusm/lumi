# Eurema

Движок для качественных исследований: поиск и библиотека статей, проверка цитат кодом, факты → позиции → черновик, карта знаний и карта поля. Без сторонних сервисов: локальные файлы, CLI, git.

- `CONTEXT.md` — зачем и что решили; `SCHEMA.md` — данные и правила; `RULES.md` — как работает Claude в проекте.
- Новый проект: `python3 eurema.py init ../мой_проект --name "…" --context "…"`, дальше в проекте `./eurema status`.
- Перенос старого проекта: `python3 tools/migrate_v0.py ../diplom --dry-run`.
- Тесты: `uv run --no-project --with pytest python -m pytest -q tests`.
- Зависимости: Python 3.11+, `pdftotext`/`pdfinfo` (poppler), `qpdf`; воркеры — `claude` CLI.
