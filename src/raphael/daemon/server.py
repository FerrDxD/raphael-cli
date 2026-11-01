"""``raphaeld`` — the local daemon.

Holds the SQLite connection, the LLM client, the job queue, and the vault
watcher. The CLI stays a thin client: it can either talk to this HTTP service
or open the database directly when the daemon is down (PRD §7, offline
reliability).

Start with ``rph daemon start`` (or ``raphaeld``).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from raphael import __version__
from raphael.config import Settings, load_settings
from raphael.service import Raphael

log = logging.getLogger("raphael.daemon")


# ----------------------------------------------------------------------
# request models
# ----------------------------------------------------------------------
class CaptureRequest(BaseModel):
    text: str
    title: str | None = None
    category: str | None = None
    project: str | None = None
    source: str | None = "daemon"
    appraise: bool = True


class AskRequest(BaseModel):
    question: str
    limit: int = 6
    history: list[dict[str, str]] = Field(default_factory=list)
    audit: bool = False


class SearchRequest(BaseModel):
    query: str
    limit: int = 10
    tag: str | None = None


class IngestRequest(BaseModel):
    paths: list[str]
    project: str | None = None


# ----------------------------------------------------------------------
# app factory
# ----------------------------------------------------------------------
def create_app(service: Raphael, *, with_watcher: bool = False) -> FastAPI:
    from raphael.daemon.watcher import VaultWatcher
    from raphael.daemon.worker import Worker

    worker = Worker(service)
    watcher = VaultWatcher(service) if with_watcher else None
    state: dict[str, Any] = {"server": None}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        service.settings.ensure_dirs()
        worker.start()
        if watcher is not None:
            watcher.start()
        _write_pid(service.settings)
        log.info("raphaeld ready on %s", service.settings.daemon_url)
        try:
            yield
        finally:
            worker.stop()
            if watcher is not None:
                watcher.stop()
            _remove_pid(service.settings)
            log.info("raphaeld stopped")

    app = FastAPI(title="raphaeld", version=__version__, lifespan=lifespan)
    app.state.raphael = service
    app.state.server = None

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            # Which vault this daemon owns: the port is a singleton, so clients
            # must be able to tell "my daemon" from "somebody else's daemon".
            "home": str(service.settings.home),
            "notes": service.db.count_notes(),
            "jobs": service.db.job_counts(),
            "worker": {"processed": worker.processed, "failures": worker.failures},
            "vec": service.db.vec_enabled,
        }

    @app.get("/status")
    def status() -> dict[str, Any]:
        return service.status()

    @app.post("/capture")
    def capture(request: CaptureRequest) -> dict[str, Any]:
        result = service.capture(
            request.text,
            title=request.title,
            category=request.category,
            project=request.project,
            source=request.source,
            appraise=request.appraise,
            embed=request.appraise,
        )
        return {
            "note": result.note.model_dump(mode="json"),
            "links": [link.model_dump(mode="json") for link in result.links],
            "engine": result.engine,
            "elapsed_ms": result.elapsed_ms,
        }

    @app.post("/ask")
    def ask(request: AskRequest) -> dict[str, Any]:
        result = service.ask(
            request.question,
            limit=request.limit,
            history=request.history,
            audit=request.audit,
        )
        return {
            "answer": result.answer,
            "notes": [note.model_dump(mode="json") for note in result.notes],
            "contradictions": result.contradictions,
            "engine": result.engine,
            "elapsed_ms": result.elapsed_ms,
        }

    @app.post("/search")
    def search(request: SearchRequest) -> dict[str, Any]:
        hits = service.search(request.query, limit=request.limit, tag=request.tag)
        return {"hits": [hit.model_dump(mode="json") for hit in hits]}

    @app.post("/ingest")
    def ingest(request: IngestRequest) -> dict[str, Any]:
        results = service.ingest([Path(p) for p in request.paths], project=request.project)
        return {"notes": [result.note.model_dump(mode="json") for result in results]}

    @app.post("/jobs/drain")
    def drain() -> dict[str, Any]:
        done = worker.drain()
        return {"processed": done, "remaining": service.db.job_counts()}

    @app.post("/reindex")
    def reindex() -> dict[str, Any]:
        return service.repair()

    @app.post("/shutdown")
    def shutdown() -> dict[str, str]:
        server = app.state.server
        if server is not None:
            server.should_exit = True
        return {"status": "stopping"}

    return app


# ----------------------------------------------------------------------
# pid + logging
# ----------------------------------------------------------------------
def _write_pid(settings: Settings) -> None:
    try:
        settings.daemon_pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        log.warning("could not write pid file")


def _remove_pid(settings: Settings) -> None:
    try:
        if settings.daemon_pid_file.exists():
            settings.daemon_pid_file.unlink()
    except OSError:
        pass


def configure_logging(settings: Settings, *, verbose: bool = False) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        settings.daemon_log, maxBytes=2_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers = [handler]
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    root.addHandler(console)
    for noisy in ("watchdog", "uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ----------------------------------------------------------------------
# entrypoint
# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="raphaeld", description="RAPHAEL background daemon")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-watch", action="store_true", help="Disable the vault watcher")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    settings = load_settings()
    settings.ensure_dirs()
    if args.host:
        settings.daemon_host = args.host
    if args.port:
        settings.daemon_port = args.port
    configure_logging(settings, verbose=args.verbose)

    os.environ["RAPHAEL_IN_DAEMON"] = "1"

    import uvicorn

    service = Raphael(settings)
    app = create_app(service, with_watcher=not args.no_watch)
    config = uvicorn.Config(
        app,
        host=settings.daemon_host,
        port=settings.daemon_port,
        log_level="debug" if args.verbose else "info",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    app.state.server = server

    def _graceful(signum: int, _frame: object) -> None:
        log.info("signal %s received", signum)
        server.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _graceful)
        except (ValueError, OSError):  # pragma: no cover - not on main thread
            pass

    try:
        server.run()
    except SystemExit as exc:  # port already bound
        log.error("daemon could not start: %s", exc)
        return 1
    finally:
        service.close()
        _remove_pid(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
