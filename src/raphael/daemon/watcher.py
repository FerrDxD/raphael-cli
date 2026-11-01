"""Vault watcher (Watchdog): keeps the index honest when you edit files.

Editing a note in Neovim/VSCode must be as valid as editing through ``rph``.
The filesystem is the source of truth, so the watcher re-parses changed files
and reconciles them into SQLite.

Self-writes are filtered by content hash: if the file already matches the
indexed hash, nothing happens — no feedback loop with the daemon's own writes.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from raphael.service import Raphael

log = logging.getLogger("raphael.watcher")

DEBOUNCE_SECONDS = 0.6


class _Handler(FileSystemEventHandler):
    def __init__(self, queue: dict[Path, str], lock: threading.Lock) -> None:
        super().__init__()
        self._queue = queue
        self._lock = lock

    def _push(self, path: str, action: str) -> None:
        candidate = Path(path)
        if candidate.suffix.lower() != ".md":
            return
        if candidate.name.startswith("."):
            return
        with self._lock:
            self._queue[candidate] = action

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._push(event.src_path, "upsert")

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._push(event.src_path, "upsert")

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._push(getattr(event, "dest_path", event.src_path), "upsert")

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._push(event.src_path, "delete")


class VaultWatcher:
    """Owns the Watchdog observer plus a debounced reconciliation loop."""

    def __init__(self, service: Raphael, *, interval: float = 0.5) -> None:
        self.service = service
        self.root = service.settings.vault_dir
        self.interval = interval
        self._queue: dict[Path, str] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._observer: Observer | None = None  # type: ignore[valid-type]
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)
        self._observer = Observer()
        self._observer.schedule(_Handler(self._queue, self._lock), str(self.root), recursive=True)
        self._observer.start()
        self._thread = threading.Thread(target=self._loop, name="raphael-watch", daemon=True)
        self._thread.start()
        log.info("watching %s", self.root)

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        log.info("watcher stopped")

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self.interval)
            pending = self._flush_queue()
            for path, action in pending:
                try:
                    self._reconcile(path, action)
                except Exception:  # noqa: BLE001 - one bad file must not stop us
                    log.exception("failed to reconcile %s", path)

    def _flush_queue(self) -> list[tuple[Path, str]]:
        """Wait for a file to settle before reading it (editors write in bursts)."""

        time.sleep(DEBOUNCE_SECONDS)
        with self._lock:
            pending = list(self._queue.items())
            self._queue.clear()
        return pending

    def _reconcile(self, path: Path, action: str) -> None:
        relative = self.service.vault.relative(path)
        if action == "delete" or not path.exists():
            existing = self.service.db.get_note_by_path(relative)
            if existing is not None:
                self.service.db.delete_note(existing.id)
                log.info("removed %s (file deleted)", existing.id)
            return

        note = self.service.vault.parse(path)
        if note is None:
            return

        existing = self.service.db.get_note(note.id) or self.service.db.get_note_by_path(relative)
        if existing is not None and existing.content_hash == note.content_hash:
            return  # our own write, or an edit that changed nothing

        note.id = existing.id if existing is not None else note.id
        note.created_at = note.created_at or (existing.created_at if existing else None)
        self.service.db.upsert_note(note)
        self.service.retriever.index_note(note, embed=False)
        self.service.db.enqueue("EMBED", {"note_id": note.id})
        self.service.db.enqueue("FIND_LINKS", {"note_id": note.id})
        log.info("reconciled %s", note.id)
