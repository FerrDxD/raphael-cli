"""Small shared helpers: identifiers, hashing, chunking, and text hygiene."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"[ \t]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

STOPWORDS = {
    # English
    "the", "and", "for", "with", "that", "this", "from", "have", "will", "are",
    "was", "were", "but", "not", "you", "your", "our", "its", "into", "than",
    "then", "them", "they", "there", "here", "what", "when", "which", "while",
    "about", "would", "could", "should", "also", "just", "some", "such", "very",
    "more", "most", "other", "each", "been", "being", "does", "did", "how",
    # Indonesian
    "yang", "dan", "untuk", "dengan", "dari", "pada", "adalah", "akan", "tidak",
    "dalam", "ini", "itu", "atau", "juga", "bisa", "dapat", "sudah", "belum",
    "saya", "kamu", "kita", "mereka", "karena", "jika", "maka", "serta", "agar",
    "oleh", "sebagai", "secara", "lebih", "paling", "masih", "hanya", "para",
    "suatu", "sebuah", "para", "telah", "saat", "ketika", "antara", "tanpa",
}


def now() -> datetime:
    """Timezone-aware local timestamp."""

    return datetime.now().astimezone()


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def slugify(value: str, *, max_length: int = 60) -> str:
    slug = _SLUG_STRIP.sub("-", value.lower()).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or "note"


def normalize_whitespace(text: str) -> str:
    # A stray BOM is invisible in an editor but would become the first character
    # of a title and break prefix matching on it.
    text = text.replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS.sub(" ", line).rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def first_sentences(text: str, count: int = 2, limit: int = 320) -> str:
    """Deterministic fallback summary used when no LLM is reachable."""

    clean = normalize_whitespace(text)
    if not clean:
        return ""
    sentences = [s for s in _SENTENCE.split(clean) if s.strip()]
    summary = " ".join(sentences[:count]).strip()
    if len(summary) > limit:
        summary = summary[: limit - 1].rstrip() + "\u2026"
    return summary


def keywords(text: str, limit: int = 6) -> list[str]:
    """Frequency-based keyword extraction (fallback when no LLM is available)."""

    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", text.lower())
    counts: dict[str, int] = {}
    for word in words:
        if word in STOPWORDS:
            continue
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ranked[:limit]]


def capitalize_entities(text: str, limit: int = 8) -> list[str]:
    """Extract capitalised tokens as a crude entity fallback."""

    found: list[str] = []
    seen: set[str] = set()
    for match in re.findall(r"\b[A-Z][A-Za-z0-9'-]{2,}\b", text):
        key = match.lower()
        if key in seen or key in STOPWORDS:
            continue
        seen.add(key)
        found.append(match)
        if len(found) >= limit:
            break
    return found


def first_heading(text: str) -> str | None:
    """Return the first Markdown heading, if the document has one.

    An ingested document usually names itself better than its filename does.
    """

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
        elif stripped:
            # Only a leading heading counts; a '#' mid-document is a code comment.
            return None
    return None


def derive_title(text: str, limit: int = 80) -> str:
    """Derive a human title from the first meaningful line."""

    for line in normalize_whitespace(text).split("\n"):
        candidate = line.strip().lstrip("#>-* ").strip()
        if candidate:
            if len(candidate) > limit:
                candidate = candidate[: limit - 1].rstrip() + "\u2026"
            return candidate
    return "Untitled note"


def chunk_text(text: str, size: int = 900, overlap: int = 120) -> list[str]:
    """Split text into paragraph-aware chunks with a small overlap.

    Paragraphs are never split unless a single paragraph exceeds ``size``.
    """

    clean = normalize_whitespace(text)
    if not clean:
        return []
    if len(clean) <= size:
        return [clean]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean) if p.strip()]
    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        if len(paragraph) > size:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start : start + size])
                start += size - overlap if size > overlap else size
            continue
        if not buffer:
            buffer = paragraph
        elif len(buffer) + len(paragraph) + 2 <= size:
            buffer = f"{buffer}\n\n{paragraph}"
        else:
            chunks.append(buffer)
            tail = buffer[-overlap:] if overlap else ""
            buffer = f"{tail}\n\n{paragraph}".strip() if tail else paragraph
    if buffer:
        chunks.append(buffer)
    return chunks


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-python cosine similarity (used when NumPy is unavailable)."""

    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def parse_id_list(values: list[str] | None) -> list[str]:
    """Accept both repeated arguments and comma-separated values."""

    if not values:
        return []
    out: list[str] = []
    for value in values:
        for piece in str(value).split(","):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out
