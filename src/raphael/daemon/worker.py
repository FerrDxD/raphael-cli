"""Background job executor (Skill: Parallel Processing).

A single worker thread drains ``background_jobs`` in FIFO order. One thread is
deliberate: the local LLM is the bottleneck, and serialising keeps RAM flat
(PRD §7, resource footprint). When the queue is empty the worker spends idle
time discovering links between notes.
"""

from __future__ import annotations

import logging
import threading
import time

from raphael.service import Raphael

log = logging.getLogger("raphael.worker")


class Worker(threading.Thread):
    """Drains the job queue until ``stop()`` is called."""

    def __init__(
        self,
        service: Raphael,
        *,
        interval: float = 0.6,
        idle_every: float = 90.0,
    ) -> None:
        super().__init__(name="raphael-worker", daemon=True)
        self.service = service
        self.interval = interval
        self.idle_every = idle_every
        self._stop = threading.Event()
        self._last_idle = 0.0
        self.processed = 0
        self.failures = 0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:  # pragma: no cover - exercised via daemon smoke test
        log.info("worker started")
        while not self._stop.is_set():
            try:
                job = self.service.db.claim_job()
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("failed to claim job")
                self._stop.wait(self.interval)
                continue

            if job is None:
                self._maybe_idle()
                self._stop.wait(self.interval)
                continue

            log.info("job %s %s", job.job_id, job.job_type)
            try:
                self.service.process_job(job.job_type, job.payload)
            except Exception as exc:  # noqa: BLE001 - isolate a bad job
                self.failures += 1
                log.exception("job %s failed", job.job_id)
                self.service.db.retry_job(job.job_id, str(exc)[:400])
            else:
                self.processed += 1
                self.service.db.finish_job(job.job_id, ok=True)
        log.info("worker stopped")

    def drain(self, *, limit: int = 100) -> int:
        """Process queued jobs synchronously (used by tests and ``--wait``)."""

        done = 0
        for _ in range(limit):
            job = self.service.db.claim_job()
            if job is None:
                break
            try:
                self.service.process_job(job.job_type, job.payload)
            except Exception as exc:  # noqa: BLE001
                self.service.db.retry_job(job.job_id, str(exc)[:400])
            else:
                self.service.db.finish_job(job.job_id, ok=True)
                done += 1
        return done

    def _maybe_idle(self) -> None:
        """Link one orphaned note while the machine is otherwise idle."""

        if not self.idle_every:
            return
        now = time.monotonic()
        if now - self._last_idle < self.idle_every:
            return
        self._last_idle = now
        if not self.service.llm.local_available():
            return
        try:
            candidates = [
                note
                for note in self.service.db.list_notes(limit=25)
                if not self.service.db.links_for(note.id, limit=1)
            ]
            for note in candidates[:2]:
                links = self.service.find_links(note.id)
                if links:
                    log.info("idle linking: %s -> %s", note.id, len(links))
        except Exception:  # noqa: BLE001
            log.exception("idle linking failed")
