"""Markdown vault: the human-readable source of truth.

Every note is a plain ``.md`` file with YAML frontmatter (ARCHITECT.md §5).
The vault is self-contained: copy ``~/.raphael/vault/`` anywhere and the
knowledge base travels with it. SQLite is only an index over these files.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from raphael.models import Note
from raphael.utils import (
    content_hash,
    derive_title,
    normalize_whitespace,
    now,
    slugify,
    truncate,
)

SUMMARY_HEADING = "## [Raphael Analytical Summary]"
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)

CATEGORIES = ("inbox", "projects", "references", "synthesized", "journal")


class Vault:
    """Reads and writes Markdown notes inside a single root directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._ready = False

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------
    def ensure(self) -> None:
        if self._ready:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        for category in CATEGORIES:
            (self.root / category).mkdir(parents=True, exist_ok=True)
        self._ready = True

    def category_dir(self, category: str) -> Path:
        category = category if category in CATEGORIES else "inbox"
        return self.root / category

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------
    def relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def path_for(self, note: Note) -> Path:
        """Stable, readable file name: ``<category>/<id>-<slug>.md``."""

        slug = slugify(note.title or derive_title(note.body), max_length=48)
        return self.category_dir(note.category) / f"{note.id}-{slug}.md"

    def all_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(p for p in self.root.rglob("*.md") if p.is_file())

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------
    def render(self, note: Note) -> str:
        created = (note.created_at or now()).isoformat(timespec="seconds")
        updated = (note.updated_at or now()).isoformat(timespec="seconds")
        front = {
            "id": note.id,
            "title": note.title,
            "created_at": created,
            "updated_at": updated,
            "category": note.category,
            "tags": list(note.tags),
            "entities": list(note.entities),
            "appraisal_status": note.appraisal_status,
        }
        if note.source:
            front["source"] = note.source
        if note.project:
            front["project"] = note.project

        head = yaml.safe_dump(
            front, sort_keys=False, allow_unicode=True, default_flow_style=False
        ).strip()
        body = note.body.rstrip()
        parts = ["---", head, "---", "", body]
        if note.summary.strip():
            parts.extend(["", SUMMARY_HEADING, f"> {note.summary.strip()}"])
        return "\n".join(parts).rstrip() + "\n"

    def parse(self, path: Path) -> Note | None:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        meta: dict = {}
        body = raw
        match = _FRONTMATTER.match(raw)
        if match:
            try:
                loaded = yaml.safe_load(match.group(1)) or {}
                if isinstance(loaded, dict):
                    meta = loaded
            except yaml.YAMLError:
                meta = {}
            body = raw[match.end() :]

        body, embedded_summary = self._strip_summary(body)
        summary = str(meta.get("summary") or embedded_summary or "").strip()

        note_id = str(meta.get("id") or "").strip()
        if not note_id:
            note_id = f"rph-file-{content_hash(str(path))[:10]}"

        title = str(meta.get("title") or "").strip() or derive_title(body)
        category = str(meta.get("category") or "inbox").strip()
        if category not in CATEGORIES:
            category = "inbox"

        status = str(meta.get("appraisal_status") or "pending").strip()
        if status not in {"pending", "completed", "heuristic", "failed"}:
            status = "pending"

        body = normalize_whitespace(body)
        return Note(
            id=note_id,
            title=title,
            body=body,
            category=category,  # type: ignore[arg-type]
            tags=_as_list(meta.get("tags")),
            entities=_as_list(meta.get("entities")),
            summary=summary,
            appraisal_status=status,  # type: ignore[arg-type]
            file_path=self.relative(path),
            content_hash=content_hash(body),
            source=_as_optional(meta.get("source")),
            project=_as_optional(meta.get("project")),
            created_at=_as_datetime(meta.get("created_at")),
            updated_at=_as_datetime(meta.get("updated_at")),
        )

    @staticmethod
    def _strip_summary(body: str) -> tuple[str, str]:
        """Split the trailing analytical summary off the note body."""

        index = body.find(SUMMARY_HEADING)
        if index == -1:
            return body, ""
        head = body[:index]
        tail = body[index + len(SUMMARY_HEADING) :].strip()
        lines = [line.lstrip("> ").strip() for line in tail.splitlines() if line.strip()]
        return head, " ".join(lines)

    # ------------------------------------------------------------------
    # write / delete
    # ------------------------------------------------------------------
    def write(self, note: Note) -> Path:
        self.ensure()
        target = self.path_for(note)
        target.parent.mkdir(parents=True, exist_ok=True)

        # A category or title change moves the file; drop the previous copy.
        # We rely on the recorded path instead of scanning the vault so that a
        # raw capture stays in the low-millisecond range (PRD §7).
        previous = note.file_path
        target.write_text(self.render(note), encoding="utf-8", newline="\n")
        note.file_path = self.relative(target)
        if previous and previous != note.file_path:
            stale = self.root / previous
            try:
                if stale.is_file():
                    stale.unlink()
            except OSError:
                pass
        return target

    def delete(self, note: Note) -> bool:
        path = self.root / note.file_path if note.file_path else None
        if path and path.exists():
            try:
                path.unlink()
                return True
            except OSError:
                return False
        return False

    def read_by_id(self, note_id: str) -> Note | None:
        for path in self.all_files():
            if path.name.startswith(f"{note_id}-") or path.name == f"{note_id}.md":
                return self.parse(path)
        return None

    def excerpt(self, note: Note, limit: int = 400) -> str:
        return truncate(note.summary or note.body, limit)


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _as_optional(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _as_datetime(value: object):
    from datetime import datetime

    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
