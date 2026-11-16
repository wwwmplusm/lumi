"""Text normalization for quote matching, with a map back to the original characters."""

import re
import unicodedata
from functools import lru_cache

DASHES = "‐‑‒–—―−"
SINGLE_QUOTES = "‘’‚′`´"
DOUBLE_QUOTES = "“”„″«»"
KEEP = set("%.,;:()[]'\"-")


@lru_cache(maxsize=4096)
def normalize_map(text):
    """(normalized, positions): lowercase, NFKC (ligatures), joined line-break hyphenation, unified dashes/quotes,
    other punctuation and whitespace collapsed to one space. positions[i] = index in `text` of normalized char i."""
    chars = []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).lower():
            chars.append((c, i))
    flat = "".join(c for c, _ in chars)
    drop = set()
    for match in re.finditer(r"-[ \t]*\n\s*", flat):  # hyphen at a line break: "la-\nboratories"
        drop.update(range(match.start(), match.end()))
    out, pos = [], []
    for k, (c, i) in enumerate(chars):
        if k in drop:
            continue
        if c in DASHES:
            c = "-"
        elif c in SINGLE_QUOTES:
            c = "'"
        elif c in DOUBLE_QUOTES:
            c = '"'
        if not (c.isalnum() or c == "_" or c in KEEP):
            c = " "
        if c == " " and (not out or out[-1] == " "):
            continue
        out.append(c)
        pos.append(i)
    while out and out[-1] == " ":
        out.pop()
        pos.pop()
    return "".join(out), tuple(pos)


def normalize(text):
    return normalize_map(text)[0]


def snippet(text, limit=300):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
