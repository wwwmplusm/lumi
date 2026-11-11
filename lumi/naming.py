"""Cite keys and work-folder names.

The decided folder name is ``<key>-<OpenAlexID>``, for example
``pu2026sanctions-W7203724392``. ``key`` is a Google-Scholar-style BibTeX
key: lowercase first-author last name, four-digit year, and the first
significant title word.

The rules below were fit to the 52 works in ``literature/KEYS.tsv`` (the
handoff described 53; the file has a header plus 52 rows). They reproduce
51 of those keys. They do not reproduce ``pu2026sanctions``: the title
"Science under sanctions…" yields ``pu2026science``, because "science" is a
content word. That exception is not hardcoded. A folder that already exists
keeps its name; this module only names new folders.

Two behaviours are proposals and are meant to be changed in one place:

- ``COLLISION_STYLE`` — how a second work with the same base key is suffixed.
- ``FALLBACK_SUFFIXES`` — the folder suffix when there is no OpenAlex id.

No third-party packages. Safe to copy into another repo next to
``research/library.py``.
"""

from __future__ import annotations

import re
import unicodedata

# Proposal. "bibtex" leaves the first work unsuffixed and suffixes the next
# with b, c, d, … (the draft example is ``pu2026sanctionsb``). "scholar"
# suffixes the second work with a, then b, … which is what a reverse-engineered
# Scholar export does. Neither style appears in the 52 existing keys.
COLLISION_STYLE = "bibtex"
COLLISION_ALPHABETS = {
    "bibtex": "bcdefghijklmnopqrstuvwxyz",
    "scholar": "abcdefghijklmnopqrstuvwxyz",
}

# Proposal. Tried in order when a work has no OpenAlex id. OpenAlex always
# wins over this list; that part is decided, not a proposal.
FALLBACK_SUFFIXES = ("doi", "arxiv", "local")

# Unattested in the 52 surnames. "cut" keeps the text before the first hyphen
# (Fei-Fei → fei), which matches Google Scholar's export.
SURNAME_HYPHEN = "cut"

# Two-letter prefixes that stay glued to the rest of a hyphenated title word.
# Attested: "Co-authorship" → coauthorship, "De-democratization" →
# dedemocratization. "re" and "un" are the same pattern and are not attested.
# Longer compounds are split: "Capital-Labor" → capital, "D-Based" → based,
# "Difference-in-Differences" → difference, "Whack-a-Chip" → whack,
# "Process-Tracing" → process.
GLUE_PREFIXES = frozenset({"co", "de", "re", "un"})

# Title tokens shorter than this are skipped when they contain no digit
# (R, D, US, U.S., AI). Attested by jones1995based, lin2025sanctions,
# liu2025entity, jin2026technological, fang2026patents.
MIN_TITLE_TOKEN = 3

# Function words skipped before the first significant title word.
# Verified against the 52 keys (the key would be wrong if the word were kept):
#   a, an, the, what (from "What's"), does, will, who.
# The rest are the same class of word (article, auxiliary, wh-word,
# preposition, conjunction) and are not the first token of any of the 52 titles.
# "science", "research", "under" as a content noun, and "based" are not here.
# "under" is a preposition; it is not why pu2026sanctions fails — "science"
# is the first significant token and is kept.
STOPWORDS = frozenset({
    "a", "an", "the",
    "of", "and", "or", "but", "nor",
    "in", "on", "at", "to", "for", "from", "by", "with", "as",
    "into", "onto", "over", "under", "about", "after", "before", "between",
    "through", "during", "without", "within", "via", "vs", "versus", "per",
    "among", "against", "across", "toward", "towards",
    "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "will", "would", "can", "could", "should", "may",
    "might", "must", "shall",
    "who", "whom", "whose", "what", "when", "where", "why", "how", "which",
    "that", "this", "these", "those",
    "not", "no", "if", "than", "then",
})

# A stored key we are willing to keep when renaming a legacy folder, instead of
# generating a new one. Requires a real title word, so a v1 key such as
# "Gao2024b" is not treated as already final.
SCHOLAR_KEY_RE = re.compile(r"^[a-z]{2,}(?:\d{4}|nd)[a-z]{3,}[a-z]?$")

_DASHES = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_GENERATIONAL = frozenset({"jr", "sr", "ii", "iii", "iv", "v", "phd", "md"})
# Underscores are allowed in doi_/arxiv_ tokens because sanitize_token turns "/" into "_".
_FOLDER_RE = re.compile(
    r"^(?P<key>[a-z0-9]+)-(?P<suffix>W\d+|doi_[A-Za-z0-9._]+|arxiv_[A-Za-z0-9._]+|local_[0-9a-f]{8})$"
)
_LEGACY_RE = re.compile(r"^W\d+$", re.I)


def ascii_fold(text):
    """ASCII folding: NFKD, drop combining marks, drop remaining non-ASCII."""
    folded = unicodedata.normalize("NFKD", str(text))
    return "".join(ch for ch in folded if not unicodedata.combining(ch)).encode("ascii", "ignore").decode()


def _is_last_first(value):
    """True when ``value`` is a ``Last, First`` string, as in OpenAlex ``raw_author_name``."""
    text = str(value or "")
    if "," not in text:
        return False
    last, first = text.split(",", 1)
    return bool(last.strip()) and bool(first.strip())


def last_name(display_name):
    """Lowercase last name of one person, ASCII-folded.

    "Xiaodie Pu" → pu. "Griliches, Zvi" → griliches (text before the comma).
    A display name without a comma uses the last token, so "Di Tong" stays
    tong and is not swallowed as an Italian particle. None of the 52 keys
    needed a particle rule. Generational suffixes (Jr, III) are dropped.
    A trailing single-letter token is an initial, not the surname, and is
    dropped when another token remains ("Jared Kaplan A" → kaplan). A name
    that is only an initial is left as-is.
    """
    name = " ".join(ascii_fold(display_name or "").replace(".", " ").split())
    if not name:
        return "anon"
    if "," in name:
        name = name.split(",", 1)[0].strip()
    parts = name.split()
    while parts:
        token = parts[-1]
        letters = re.sub(r"[^A-Za-z]", "", token)
        generational = token.lower().rstrip(".") in _GENERATIONAL
        initial = len(letters) == 1 and len(parts) > 1
        if not generational and not initial:
            break
        parts.pop()
    if not parts:
        return "anon"
    if "," in (display_name or ""):
        token = " ".join(parts)
    else:
        token = parts[-1]
    if SURNAME_HYPHEN == "join":
        token = token.replace("-", "")
    else:
        token = token.split("-", 1)[0]
    token = re.sub(r"[^A-Za-z]", "", token).lower()
    return token or "anon"


def year_part(year):
    """Four-digit year, or ``nd`` when the work has none."""
    if year is None or year == "":
        return "nd"
    match = re.search(r"\d{4}", str(year))
    return match.group(0) if match else "nd"


def _prepare_title(title):
    text = str(title or "")
    for ch in "“”«»„":
        text = text.replace(ch, " ")
    for ch in "‘’´`":
        text = text.replace(ch, "'")
    for ch in _DASHES:
        text = text.replace(ch, "-")
    text = text.replace("&", " ")
    return ascii_fold(text).lower()


def _norm_piece(piece):
    piece = piece.strip(".,:;!?()[]{}\"'")
    piece = re.sub(r"'s$", "", piece)
    piece = piece.replace("'", "")
    return re.sub(r"[^a-z0-9]", "", piece)


def title_tokens(title):
    """Title words after folding, with hyphen prefixes glued and other hyphens split."""
    tokens = []
    for raw in _prepare_title(title).split():
        raw = raw.strip(".,:;!?()[]{}\"'")
        if not raw:
            continue
        parts = [part for part in raw.split("-") if part]
        if len(parts) >= 2 and _norm_piece(parts[0]) in GLUE_PREFIXES:
            glued = "".join(_norm_piece(part) for part in parts)
            if glued:
                tokens.append(glued)
            continue
        for part in parts or [raw]:
            word = _norm_piece(part)
            if word:
                tokens.append(word)
    return tokens


def _significant(token):
    if token in STOPWORDS:
        return False
    if len(token) < MIN_TITLE_TOKEN and not any(ch.isdigit() for ch in token):
        return False
    return True


def title_word(title):
    """First significant title word, or ``untitled`` when the title is empty."""
    tokens = title_tokens(title)
    for token in tokens:
        if _significant(token):
            return token
    return tokens[0] if tokens else "untitled"


def make_cite_key(author, year, title, *, taken=(), collision=None):
    """``lastname + year + titleword``, with a collision suffix when ``taken`` has the base key.

    ``author`` is one person's display name ("Xiaodie Pu" or "Pu, Xiaodie").
    ``taken`` is an iterable of keys already used by other works. The work's own
    current key must not be in ``taken`` when it is being saved again.
    """
    base = f"{last_name(author)}{year_part(year)}{title_word(title)}"
    style = collision or COLLISION_STYLE
    try:
        alphabet = COLLISION_ALPHABETS[style]
    except KeyError as exc:
        raise ValueError(f"unknown collision style {style!r}") from exc
    used = {str(key).lower() for key in taken}
    if base.lower() not in used:
        return base
    for letter in alphabet:
        candidate = base + letter
        if candidate.lower() not in used:
            return candidate
    raise ValueError(f"too many cite-key collisions for {base}")


def first_author_name(work):
    """Name of the first author from an OpenAlex-like work dict.

    A ``raw_author_name`` written ``Last, First`` wins over ``display_name``.
    OpenAlex sometimes appends a stray initial to the display name
    ("Jared Kaplan A") while the raw name stays "Kaplan, Jared".
    """
    authorships = work.get("authorships") or []
    if authorships:
        first = authorships[0]
        if isinstance(first, str):
            return first
        author = first.get("author") or {}
        if isinstance(author, str):
            return author
        if not isinstance(author, dict):
            author = {}
        for raw in (first.get("raw_author_name"), author.get("raw_author_name")):
            if _is_last_first(raw):
                return raw
        name = author.get("display_name") or first.get("raw_author_name") or author.get("raw_author_name") or ""
        if name:
            return name
    authors = work.get("authors")
    if isinstance(authors, str) and authors.strip():
        return authors.split(" and ", 1)[0].strip()
    if isinstance(authors, list) and authors:
        first = authors[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("display_name") or first.get("name") or ""
    return str(work.get("author") or "")


def work_title(work):
    return " ".join(str(work.get("display_name") or work.get("title") or "").split())


def work_year(work):
    if "publication_year" in work and work.get("publication_year") not in (None, ""):
        return work.get("publication_year")
    return work.get("year")


def cite_key_for_work(work, *, taken=(), collision=None):
    return make_cite_key(first_author_name(work), work_year(work), work_title(work), taken=taken, collision=collision)


def is_scholar_key(key):
    """True for a folder cite key, false for a v1 key such as Gao2024b."""
    return bool(SCHOLAR_KEY_RE.fullmatch(key or ""))


def bare_doi(value):
    if not value:
        return ""
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", str(value).strip(), flags=re.I)


def doi_url(value):
    bare = bare_doi(value)
    return f"https://doi.org/{bare}" if bare else ""


def sanitize_token(value):
    """Folder-safe id: ``/`` and every other character outside ``[A-Za-z0-9.]`` become ``_``."""
    text = bare_doi(value)
    text = re.sub(r"^arxiv:\s*", "", text, flags=re.I)
    text = re.sub(r"[^A-Za-z0-9.]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text


def openalex_id_of(work):
    candidates = [work.get("openalex_id"), work.get("id")]
    ids = work.get("ids") or {}
    if isinstance(ids, dict):
        candidates.append(ids.get("openalex"))
    for candidate in candidates:
        if not candidate:
            continue
        match = re.search(r"W\d+", str(candidate), re.I)
        if match:
            return match.group(0).upper()
    return None


def arxiv_id_of(work):
    ids = work.get("ids") or {}
    raw = ""
    if work.get("arxiv_id"):
        raw = str(work.get("arxiv_id"))
    elif isinstance(ids, dict) and ids.get("arxiv"):
        raw = str(ids.get("arxiv"))
    else:
        doi = bare_doi(work.get("doi") or (ids.get("doi") if isinstance(ids, dict) else "") or "")
        match = re.search(r"arxiv[./](\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?", doi, re.I)
        if match:
            raw = match.group(1)
    if not raw:
        return ""
    raw = re.sub(r"^arxiv:\s*", "", raw, flags=re.I).strip()
    raw = re.sub(r"v\d+$", "", raw)
    return sanitize_token(raw)


def openalex_id_from_text(value):
    """An OpenAlex id when ``value`` is exactly ``W123`` or a URL that ends in one."""
    text = str(value or "").strip()
    if re.fullmatch(r"W\d+", text, re.I):
        return text.upper()
    if "openalex.org/" in text.lower():
        match = re.search(r"W\d+", text, re.I)
        return match.group(0).upper() if match else None
    return None


class FolderName:
    """Parsed ``<key>-<suffix>`` folder. ``kind`` is openalex, doi, arxiv, or local."""

    def __init__(self, key, kind, ident):
        self.key = key
        self.kind = kind
        self.ident = ident

    def __repr__(self):
        return f"FolderName({self.key!r}, {self.kind!r}, {self.ident!r})"


def parse_folder_name(name):
    match = _FOLDER_RE.fullmatch(name or "")
    if not match:
        return None
    suffix = match.group("suffix")
    if suffix.startswith("W"):
        return FolderName(match.group("key"), "openalex", suffix)
    kind, ident = suffix.split("_", 1)
    return FolderName(match.group("key"), kind, ident)


def is_legacy_folder(name):
    """A bare ``W123`` directory from the old scheme."""
    return bool(_LEGACY_RE.fullmatch(name or ""))


def openalex_id_from_folder(name):
    if is_legacy_folder(name):
        return name.upper()
    parsed = parse_folder_name(name)
    if parsed and parsed.kind == "openalex":
        return parsed.ident
    return None


def folder_name_for(key, work, pdf_sha256=None, *, fallbacks=None):
    """Folder name for a new work. Existing folders are not passed through here.

    OpenAlex id → ``<key>-W…``. Otherwise the first available entry of
    ``FALLBACK_SUFFIXES`` (proposal): ``doi_``, ``arxiv_``, ``local_``.
    """
    oa = openalex_id_of(work or {})
    if oa:
        return f"{key}-{oa}"
    order = tuple(fallbacks if fallbacks is not None else FALLBACK_SUFFIXES)
    doi = sanitize_token(bare_doi((work or {}).get("doi")))
    arxiv = arxiv_id_of(work or {})
    local = (pdf_sha256 or "")[:8].lower()
    available = {"doi": doi, "arxiv": arxiv, "local": local if len(local) == 8 else ""}
    for kind in order:
        token = available.get(kind) or ""
        if token:
            return f"{key}-{kind}_{token}"
    raise ValueError("cannot name a folder: no OpenAlex id, DOI, arXiv id, or PDF sha256")


def resolve_ref(names, ref, keys=None):
    """Return the folder name for a folder, ``W…`` id, or cite key.

    ``keys`` maps folder name → the ``key`` stored in metadata (v1 keys such as
    ``Gao2024`` live there, not in the folder name). Returns ``None`` when
    nothing matches. Raises ``ValueError`` when more than one folder matches.
    """
    ref = str(ref or "").strip()
    if not ref:
        return None
    names = list(names)
    if ref in names:
        return ref
    by_lower = {}
    for name in names:
        by_lower.setdefault(name.lower(), []).append(name)
    exact = by_lower.get(ref.lower())
    if exact and len(exact) == 1 and (parse_folder_name(exact[0]) or is_legacy_folder(exact[0])):
        return exact[0]
    oa = openalex_id_from_text(ref)
    if oa:
        hits = [name for name in names if openalex_id_from_folder(name) == oa]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ValueError(f"ambiguous OpenAlex id {oa}: {', '.join(hits)}")
        return None
    wanted = re.sub(r"[^a-z0-9]", "", ascii_fold(ref).lower())
    if not wanted:
        return None
    hits = []
    for name in names:
        parsed = parse_folder_name(name)
        folder_key = parsed.key if parsed else ""
        meta_key = str((keys or {}).get(name) or "")
        meta_folded = re.sub(r"[^a-z0-9]", "", meta_key.lower())
        if wanted in {folder_key.lower(), meta_key.lower(), meta_folded} and wanted:
            hits.append(name)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise ValueError(f"ambiguous cite key {ref}: {', '.join(hits)}")
    return None
