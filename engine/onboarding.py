"""User-level configuration; credentials never belong to a research project."""
import getpass
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse

# URL is an account/help entry point, not an automatic key issuance endpoint.
PROVIDERS = (
    ("OpenAlex", "OPENALEX_API_KEY", "https://openalex.org/settings/api"),
    ("Semantic Scholar", "S2_API_KEY", "https://www.semanticscholar.org/product/api"),
    ("Unpaywall (email)", "UNPAYWALL_EMAIL", "https://unpaywall.org/products/api"),
    ("CORE", "CORE_API_KEY", "https://core.ac.uk/services/api"),
    ("Lens", "LENS_TOKEN", "https://www.lens.org/lens/user/subscriptions"),
)
KEYS = {key for _, key, _ in PROVIDERS}


def config_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "lumi" / "credentials.json"


def read_credentials():
    path = config_path()
    if not path.exists():
        return {}
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError(f"Повреждён файл настроек {path}; исправьте JSON или восстановите копию") from None
    if not isinstance(values, dict) or any(not isinstance(v, str) for v in values.values()):
        raise ValueError(f"Неверный формат настроек {path}")
    return values


def load_credentials():
    for key, value in read_credentials().items():
        if key in KEYS and value:
            os.environ.setdefault(key, value)


def save_credentials(values):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".credentials-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(values, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def redact(message):
    for key in KEYS:
        value = os.environ.get(key)
        if value:
            for variant in (value, urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value)):
                message = message.replace(variant, "[скрыто]")
    return message


def setup(non_interactive=False, check=False):
    if not non_interactive and not sys.stdin.isatty():
        raise ValueError("Для мастера нужен терминал. Используйте lumi setup --non-interactive для импорта переменных окружения.")
    values = read_credentials()
    print("Lumi: первичная настройка")
    print("Ключи сохраняются локально в открытом JSON с правами 600, вне проекта.")
    print("Enter — сохранить текущее значение или пропустить. '-' — удалить настройку.")
    for name, key, url in PROVIDERS:
        current = os.environ.get(key) or values.get(key, "")
        if non_interactive:
            value = current
        else:
            print(f"\n{name}: {'настроен' if current else 'не настроен'} · {url}")
            value = getpass.getpass(f"{key}: ").strip() or current
        if value == "-":
            values.pop(key, None)
            os.environ.pop(key, None)
        elif value:
            if key == "UNPAYWALL_EMAIL" and ("@" not in value or any(c.isspace() for c in value)):
                raise ValueError("Unpaywall требует email, а не API-ключ; настройки не сохранены")
            values[key] = value
            os.environ[key] = value
    save_credentials(values)
    print(f"\nНастройки сохранены: {config_path()}")
    result = doctor(check)
    print('\nДальше: lumi init ./my-research --name "Моё исследование"')
    print("Повторная настройка: lumi setup. Проверка API: lumi doctor --online.")
    print("Удаление из файла не удаляет переменную из родительского shell.")
    return result


def doctor(online=False):
    missing = []
    print("\nЗависимости:")
    for binary in ("pdftotext", "pdfinfo", "qpdf"):
        present = bool(shutil.which(binary))
        print(f"  {binary}: {'OK' if present else 'не установлен'}")
        if not present:
            missing.append(binary)
    if missing:
        print("Установите poppler и qpdf системным пакетным менеджером (macOS: brew install poppler qpdf; Debian/Ubuntu: sudo apt install poppler-utils qpdf; Arch: sudo pacman -S poppler qpdf).")
    print("\nAPI (наличие настройки не подтверждает доступ):")
    for name, key, _ in PROVIDERS:
        print(f"  {name}: {'настройка задана' if os.environ.get(key) else 'не настроен'}")
    print("  arXiv, Crossref: ключи не требуются")
    failed = probe_apis() if online else 0
    return 1 if missing or failed else 0


def probe_apis():
    from . import sources
    email = os.environ.get("UNPAYWALL_EMAIL")
    probes = [
        ("OpenAlex", "OPENALEX_API_KEY", lambda: sources.openalex("machine learning", 1)),
        ("Semantic Scholar", None, lambda: sources.semantic_scholar("machine learning", 1)),
        ("arXiv", None, lambda: sources.arxiv("all:electron", 1)),
        ("Crossref", None, lambda: sources.crossref("machine learning", 1)),
        ("CORE", "CORE_API_KEY", lambda: sources.core("machine learning", 1)),
        ("Lens", "LENS_TOKEN", lambda: sources.lens("machine learning", 1)),
        ("Unpaywall", "UNPAYWALL_EMAIL", lambda: sources.get_json("https://api.unpaywall.org/v2/10.1038/nature12373?" + urllib.parse.urlencode({"email": email}))),
    ]
    failed = 0
    print("\nПроверка API (по одному запросу, без добавления в библиотеку):")
    for name, key, probe in probes:
        if key and not os.environ.get(key):
            print(f"  {name}: пропущен — нет {key}")
            continue
        try:
            response = probe()
            if getattr(response, "error", None):
                print(f"  {name}: ошибка API; {redact(response.error)}")
                failed += 1
            elif not response:
                print(f"  {name}: ответ пуст; работоспособность не подтверждена")
                failed += 1
            else:
                print(f"  {name}: ответ получен")
        except urllib.error.HTTPError as exc:
            print(f"  {name}: HTTP {exc.code} (проверьте доступ и квоту)")
            failed += 1
        except (*sources.NET_ERRORS, TypeError):
            # Do not echo server bodies or URLs: they can contain credentials.
            print(f"  {name}: ошибка сети или формата ответа")
            failed += 1
    return failed
