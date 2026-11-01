"""Application service: the use cases behind every CLI command.

The CLI, the daemon, and the TUI all call into this object, so behaviour is
identical no matter which surface invoked it. Nothing here prints — the
console layer owns presentation.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from raphael.brain.agents import Answerer, Appraiser, IntentRouter, Linker, Separator, Synthesizer
from raphael.brain.llm import LLM, LLMError
from raphael.brain.memory import HybridRetriever
from raphael.config import Settings, load_settings
from raphael.models import Link, Note, SearchHit, SplitResult, SynthesisResult
from raphael.storage.db import Database
from raphael.storage.parsers import ParserError, extract_many
from raphael.storage.vault import Vault
from raphael.utils import (
    content_hash,
    derive_title,
    first_heading,
    normalize_whitespace,
    now,
    now_iso,
    truncate,
)

PROJECT_TAG = "project"


def _port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    """Ask the OS whether something is listening, without any HTTP work.

    On Windows a refused connect to a closed loopback port can sit in SYN
    retransmit for seconds (firewall interception), so the timeout matters more
    than the answer: a live listener accepts in a millisecond.
    """

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


_daemon_http = None


def _daemon_client():
    """Shared loopback HTTP client for daemon calls.

    Built lazily and reused: constructing a client loads a TLS trust store,
    which costs seconds on Windows, and loopback needs neither TLS nor proxy
    environment variables.
    """

    global _daemon_http
    if _daemon_http is None:
        import httpx

        _daemon_http = httpx.Client(timeout=1.0, verify=False, trust_env=False)
    return _daemon_http


@dataclass(slots=True)
class CaptureResult:
    note: Note
    links: list[Link] = field(default_factory=list)
    engine: str = "heuristic"
    elapsed_ms: float = 0.0
    created: bool = True


@dataclass(slots=True)
class AskResult:
    answer: str
    notes: list[Note] = field(default_factory=list)
    contradictions: list[dict[str, str]] = field(default_factory=list)
    engine: str = "heuristic"
    elapsed_ms: float = 0.0


class Raphael:
    """The cognitive pipeline wired to local storage and the local model."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        offline_only: bool = False,
        init: bool = True,
    ) -> None:
        self.settings = settings or load_settings()
        if init:
            self.settings.ensure_dirs()
        self.db = Database(self.settings.db_path, self.settings.embedding_dim)
        self.vault = Vault(self.settings.vault_dir)
        if init:
            self.vault.ensure()
        self.llm = LLM(self.settings, offline_only=offline_only)
        self.retriever = HybridRetriever(self.db, self.vault, self.settings, self.llm)
        self.appraiser = Appraiser(self.llm, self.db)
        self.linker = Linker(self.llm, self.db)
        self.answerer = Answerer(self.llm, self.db)
        self.synthesizer = Synthesizer(self.llm, self.db)
        self.separator = Separator(self.llm, self.db)

    def close(self) -> None:
        self.llm.close()
        self.db.close()

    def __enter__(self) -> "Raphael":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ==================================================================
    # capture
    # ==================================================================
    def capture(
        self,
        text: str,
        *,
        title: str | None = None,
        category: str | None = None,
        project: str | None = None,
        source: str | None = None,
        appraise: bool = False,
        embed: bool = False,
        tags: Sequence[str] | None = None,
    ) -> CaptureResult:
        """Store raw text immediately, then defer analysis to the daemon.

        Kept deliberately thin: a raw capture must stay in the millisecond
        range (PRD §7), so no model call happens here unless ``appraise``.
        """

        started = time.perf_counter()
        body = normalize_whitespace(text)
        if not body:
            raise ValueError("cannot capture empty text")

        resolved_category = category or ("projects" if project else "inbox")
        note = Note(
            id=self.db.next_note_id(),
            title=truncate(title or derive_title(body), 90),
            body=body,
            category=resolved_category,  # type: ignore[arg-type]
            tags=list(dict.fromkeys([t.lower() for t in (tags or [])])),
            entities=[],
            summary="",
            appraisal_status="pending",
            source=source,
            project=project,
            created_at=now(),
            updated_at=now(),
            content_hash=content_hash(body),
        )
        if project:
            note.tags = list(dict.fromkeys([*note.tags, f"project:{project.lower().replace(' ', '-')}"]))

        self.vault.write(note)
        self.db.upsert_note(note)

        engine = "queued"
        links: list[Link] = []
        if appraise:
            note, engine = self._appraise_now(note)
            # Appraisal may have moved the note to another category, so chunk it
            # from the freshly stored copy; without this the note would never be
            # searchable beyond its title.
            self.retriever.index_note(note, embed=embed)
            links = self.find_links(note.id)
            if not embed:
                self.db.enqueue("EMBED", {"note_id": note.id})
                self.maybe_spawn_daemon()
        else:
            self.retriever.index_note(note, embed=False)
            self.db.enqueue("APPRAISE", {"note_id": note.id})
            self.db.enqueue("EMBED", {"note_id": note.id})
            self.maybe_spawn_daemon()

        return CaptureResult(
            note=note,
            links=links,
            engine=engine,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            created=True,
        )

    def _appraise_now(self, note: Note) -> tuple[Note, str]:
        result = self.appraiser.appraise(note.body, default_category=note.category)
        note.title = result.title or note.title
        note.summary = result.summary
        note.tags = list(dict.fromkeys([*note.tags, *result.tags]))
        note.entities = result.entities
        if result.category != "inbox" or note.category == "inbox":
            note.category = result.category  # type: ignore[assignment]
        note.appraisal_status = "completed" if result.engine != "heuristic" else "heuristic"
        note.updated_at = now()
        self.vault.write(note)
        self.db.upsert_note(note)
        return note, result.engine

    # ==================================================================
    # ingest
    # ==================================================================
    def ingest(
        self,
        paths: Sequence[Path],
        *,
        project: str | None = None,
        appraise: bool = True,
    ) -> list[CaptureResult]:
        """Extract local documents and capture each one as a note."""

        extracted = extract_many([Path(p) for p in paths])
        results: list[CaptureResult] = []
        for path, text in extracted:
            results.append(
                self.capture(
                    text,
                    # A document's own heading beats its filename as a title.
                    title=first_heading(text) or path.stem,
                    category="projects" if project else "references",
                    project=project,
                    source=str(path),
                    appraise=appraise,
                    embed=False,
                    tags=[path.suffix.lstrip(".").lower()] if path.suffix else None,
                )
            )
        return results

    # ==================================================================
    # query
    # ==================================================================
    def search(self, query: str, *, limit: int = 10, tag: str | None = None) -> list[SearchHit]:
        return self.retriever.search(query, limit=limit, tag=tag)

    def ask(
        self,
        question: str,
        *,
        limit: int = 6,
        history: Sequence[dict[str, str]] | None = None,
        audit: bool = False,
    ) -> AskResult:
        started = time.perf_counter()
        hits = self.retriever.search(question, limit=limit)
        context, notes = self.retriever.build_context(hits)
        answer = self.answerer.answer(question, context, notes, history=history)
        contradictions = (
            self.answerer.find_contradictions(notes) if audit and len(notes) > 1 else []
        )
        return AskResult(
            answer=answer,
            notes=notes,
            contradictions=contradictions,
            engine=self.llm.engine if self.llm.local_available() else "heuristic",
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def route(self, text: str) -> str:
        return IntentRouter.route(text)

    # ==================================================================
    # graph
    # ==================================================================
    def find_links(self, note_id: str, *, limit: int | None = None) -> list[Link]:
        note = self.db.get_note(note_id)
        if note is None:
            return []
        limit = limit or self.settings.link_limit
        candidates = self.retriever.related_notes(note, limit=limit)
        if not candidates:
            return []
        links = self.linker.link(note, candidates)
        for link in links:
            self.db.upsert_link(link)
        return links

    def links_for(self, note_id: str, limit: int = 10) -> list[Link]:
        return self.db.links_for(note_id, limit=limit)

    # ==================================================================
    # synthesis / separation
    # ==================================================================
    def resolve_notes(self, tokens: Sequence[str]) -> tuple[list[Note], list[str]]:
        found: list[Note] = []
        missing: list[str] = []
        for token in tokens:
            note = self.db.resolve_note(token)
            if note is None:
                missing.append(token)
            elif note.id not in [n.id for n in found]:
                found.append(note)
        return found, missing

    def synth(
        self,
        tokens: Sequence[str],
        *,
        instruction: str | None = None,
        title: str | None = None,
    ) -> SynthesisResult:
        notes, missing = self.resolve_notes(tokens)
        if missing:
            raise LookupError(f"note not found: {', '.join(missing)}")
        if not notes:
            raise ValueError("no notes selected for synthesis")
        return self.synthesizer.synthesize(notes, instruction=instruction, title=title)

    def split(self, target: str, *, goal: str | None = None) -> SplitResult:
        """Split either a note id/title or a free-text goal into tasks."""

        note = self.db.resolve_note(target)
        if note is not None:
            return self.separator.split(note.body, goal=goal or note.title)
        if not target.strip():
            raise ValueError("provide a note id, title, or goal text")
        return self.separator.split(target, goal=goal)

    # ==================================================================
    # background jobs (called by the daemon worker)
    # ==================================================================
    def process_job(self, job_type: str, payload: dict[str, Any]) -> None:
        if job_type == "APPRAISE":
            self._job_appraise(str(payload.get("note_id", "")))
        elif job_type == "EMBED":
            note_id = payload.get("note_id")
            if note_id:
                self.retriever.embed_pending(note_id=str(note_id))
            else:
                self.retriever.embed_pending()
        elif job_type == "FIND_LINKS":
            note_id = str(payload.get("note_id", ""))
            if note_id:
                self.find_links(note_id)
        elif job_type == "REINDEX":
            self.reindex(embed=bool(payload.get("embed", True)))
        elif job_type == "SYNTHESIZE":
            note_id = str(payload.get("note_id", ""))
            if note_id:
                self.find_links(note_id)
        else:
            raise ValueError(f"unknown job type: {job_type}")

    def _job_appraise(self, note_id: str) -> None:
        note = self.db.get_note(note_id)
        if note is None:
            return
        if note.appraisal_status in {"completed"} and note.tags:
            return
        note, _engine = self._appraise_now(note)
        self.retriever.index_note(note, embed=False)
        if self.llm.local_available():
            self.retriever.embed_pending(note_id=note.id)
        self.db.enqueue("FIND_LINKS", {"note_id": note.id})

    # ==================================================================
    # maintenance
    # ==================================================================
    def reindex(self, *, embed: bool = True) -> dict[str, int]:
        return self.retriever.reindex_all(embed=embed)

    def repair(self) -> dict[str, Any]:
        """Make the database mirror the vault again (files are authoritative)."""

        report = self.reindex(embed=False)
        embedded = self.retriever.embed_pending()
        report["embedded"] = embedded
        return report

    def status(self) -> dict[str, Any]:
        stats = self.db.stats()
        today = now().strftime("%Y-%m-%d")
        notes = self.db.list_notes(limit=200)
        today_notes = [
            n for n in notes if n.created_at and n.created_at.strftime("%Y-%m-%d") == today
        ]
        projects: dict[str, int] = {}
        for note in notes:
            if note.project:
                projects[note.project] = projects.get(note.project, 0) + 1
        pending = [n for n in notes if n.appraisal_status == "pending"]
        return {
            "stats": stats,
            "today": today_notes,
            "pending_notes": pending,
            "projects": projects,
            "recent_links": self.db.recent_links(limit=5),
            "jobs": self.db.recent_jobs(limit=5),
            "llm": self.llm.status(),
            "vault": str(self.settings.vault_dir),
            "db": str(self.settings.db_path),
            "rules": self.db.list_rules(),
        }

    def evolve(self, rule: str, *, key: str | None = None) -> str:
        clean = rule.strip()
        if not clean:
            raise ValueError("rule cannot be empty")
        rule_key = key or f"rule:{content_hash(clean)[:12]}"
        self.db.set_rule(rule_key, clean)
        return rule_key

    def forget_rule(self, key: str) -> bool:
        return self.db.delete_rule(key)

    # ==================================================================
    # daemon helpers
    # ==================================================================
    def daemon_status(self, timeout: float = 0.5) -> dict[str, Any] | None:
        """Health payload, but only from a daemon that owns *this* vault.

        The daemon port is a singleton, so "something is listening" is not the
        same as "my queue is being drained". A socket probe rejects the common
        case cheaply; only when a port answers do we pay for an HTTP round-trip.
        """

        if not _port_open(
            self.settings.daemon_host, self.settings.daemon_port, timeout=0.2
        ):
            return None
        try:
            response = _daemon_client().get(
                f"{self.settings.daemon_url}/health", timeout=timeout
            )
            data = response.json() if response.status_code == 200 else None
        except Exception:  # noqa: BLE001 - any failure means "not usable"
            return None
        if not isinstance(data, dict):
            return None
        home = data.get("home")
        if home and Path(str(home)).resolve() != self.settings.home.resolve():
            return None  # a daemon runs, but for another vault
        return data

    def daemon_running(self, timeout: float = 0.5) -> bool:
        return self.daemon_status(timeout) is not None

    def maybe_spawn_daemon(self) -> bool:
        """Fire-and-forget daemon start so queued jobs actually run.

        Never blocks: the child is detached and we do not wait for it.
        """

        if os.environ.get("RAPHAEL_IN_DAEMON"):
            return False
        if not self.settings.auto_start_daemon:
            return False
        # Socket probe only: this is the capture hot path, so no HTTP here. A
        # daemon belonging to another vault would make us skip the spawn, which
        # 'rph daemon start' then reports explicitly.
        if _port_open(self.settings.daemon_host, self.settings.daemon_port, timeout=0.2):
            return False
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
            subprocess.Popen(
                [sys.executable, "-m", "raphael.daemon.server", "--port", str(self.settings.daemon_port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
                env={**os.environ, "RAPHAEL_IN_DAEMON": "1"},
            )
            return True
        except (OSError, ValueError):
            return False

    # ==================================================================
    # helpers
    # ==================================================================
    def get_note(self, token: str) -> Note | None:
        return self.db.resolve_note(token)

    def recent_notes(self, limit: int = 10) -> list[Note]:
        return self.db.list_notes(limit=limit)

    def delete_note(self, token: str) -> bool:
        note = self.db.resolve_note(token)
        if note is None:
            return False
        self.vault.delete(note)
        self.db.delete_note(note.id)
        return True

    def edit_note(self, token: str) -> Path | None:
        note = self.db.resolve_note(token)
        if note is None:
            return None
        path = self.settings.vault_dir / note.file_path
        return path if path.exists() else None

    def touch_note(self, note_id: str) -> Note | None:
        """Refresh a note after external editing (used by the watcher)."""

        note = self.db.get_note(note_id)
        if note is None:
            return None
        path = self.settings.vault_dir / note.file_path
        if not path.exists():
            self.db.delete_note(note_id)
            return None
        fresh = self.vault.parse(path)
        if fresh is None:
            return None
        self.db.upsert_note(fresh)
        self.retriever.index_note(fresh, embed=False)
        self.db.enqueue("EMBED", {"note_id": fresh.id})
        self.db.enqueue("FIND_LINKS", {"note_id": fresh.id})
        return fresh

    def job_summary(self) -> dict[str, int]:
        return self.db.job_counts()

    def stamp(self) -> str:
        return now_iso()


def open_service(offline_only: bool = False) -> Raphael:
    """Convenience constructor used by the CLI."""

    return Raphael(load_settings(), offline_only=offline_only)


__all__ = [
    "AskResult",
    "CaptureResult",
    "LLMError",
    "ParserError",
    "Raphael",
    "open_service",
]
