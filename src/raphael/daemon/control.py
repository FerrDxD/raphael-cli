"""Daemon lifecycle control used by ``rph daemon ...``."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from raphael.config import Settings, load_settings
from raphael.console import get_console, notice, report, warning


_client: httpx.Client | None = None


def _http() -> httpx.Client:
    """One pooled loopback client.

    ``httpx.get`` builds a fresh client per call, and each one loads a TLS trust
    store — seconds on Windows. The start/stop loops poll several times a
    second, so the client must be shared (and never inherit proxy env vars).
    """

    global _client
    if _client is None:
        _client = httpx.Client(timeout=2.0, verify=False, trust_env=False)
    return _client


def _raw_health(settings: Settings, timeout: float = 1.5) -> dict | None:
    """Whatever daemon is answering on the port, foreign or not."""

    try:
        response = _http().get(f"{settings.daemon_url}/health", timeout=timeout)
        if response.status_code == 200:
            return response.json()
    except Exception:  # noqa: BLE001 - any failure means "not running"
        return None
    return None


def _serves_home(data: dict, settings: Settings) -> bool:
    """A daemon only owns one vault; another home's daemon must not count."""

    home = data.get("home")
    if not home:
        return True  # older daemon without the field
    try:
        return Path(str(home)).resolve() == settings.home.resolve()
    except OSError:  # pragma: no cover - exotic paths
        return False


def _healthy(settings: Settings, timeout: float = 1.5) -> dict | None:
    data = _raw_health(settings, timeout)
    if data is None or not _serves_home(data, settings):
        return None
    return data


def _foreign_home(settings: Settings) -> str | None:
    data = _raw_health(settings, timeout=0.5)
    if data is not None and not _serves_home(data, settings):
        return str(data.get("home"))
    return None


def _pid(settings: Settings) -> int | None:
    try:
        return int(settings.daemon_pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _spawn(settings: Settings) -> subprocess.Popen | None:
    settings.ensure_dirs()
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    try:
        with open(settings.daemon_log, "ab") as log_handle:
            return subprocess.Popen(
                [sys.executable, "-m", "raphael.daemon.server", "--port", str(settings.daemon_port)],
                stdout=log_handle,
                stderr=log_handle,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
                env={**os.environ, "RAPHAEL_IN_DAEMON": "1"},
            )
    except OSError as exc:
        warning(f"Gagal menjalankan daemon: {exc}")
        return None


def start(settings: Settings) -> int:
    if _healthy(settings):
        report(f"Daemon sudah berjalan di {settings.daemon_url}")
        return 0
    foreign = _foreign_home(settings)
    if foreign:
        warning(
            f"Port {settings.daemon_port} sudah dipakai daemon vault lain ({foreign}). "
            "Hentikan daemon itu atau ubah port di config.toml."
        )
        return 1
    process = _spawn(settings)
    if process is None:
        return 1

    # A cold Python start with FastAPI + uvicorn can take several seconds on a
    # laptop running antivirus; be patient rather than declaring failure.
    deadline = time.time() + 45.0
    while time.time() < deadline:
        if _healthy(settings, timeout=1.0):
            report(f"Daemon aktif di {settings.daemon_url} [muted](pid {process.pid})[/]")
            return 0
        if process.poll() is not None:
            warning(f"Daemon berhenti saat start (exit {process.returncode}). Log: {settings.daemon_log}")
            return 1
        time.sleep(0.3)
    warning(f"Daemon belum merespons. Periksa log: {settings.daemon_log}")
    return 1


def stop(settings: Settings) -> int:
    if not _healthy(settings, timeout=0.3):
        pid = _pid(settings)
        if pid and _alive(pid):
            _terminate(pid)
            report("Proses daemon dihentikan paksa.")
            return 0
        notice("Daemon tidak sedang berjalan.")
        return 0

    try:
        _http().post(f"{settings.daemon_url}/shutdown", timeout=2.0)
    except httpx.HTTPError:
        pass

    deadline = time.time() + 8.0
    while time.time() < deadline:
        if not _healthy(settings, timeout=0.3):
            report("Daemon dihentikan.")
            return 0
        time.sleep(0.2)

    pid = _pid(settings)
    if pid and _alive(pid):
        _terminate(pid)
        report("Daemon dihentikan paksa.")
        return 0
    warning("Daemon masih berjalan; periksa proses manual.")
    return 1


def restart(settings: Settings) -> int:
    stop(settings)
    time.sleep(0.5)
    return start(settings)


def status(settings: Settings) -> int:
    data = _healthy(settings)
    if data is None:
        foreign = _foreign_home(settings)
        if foreign:
            warning(f"Port {settings.daemon_port} melayani vault lain: {foreign}")
            get_console().print(
                "   [muted]hentikan daemon itu (dari home-nya) atau ubah port di config.toml[/]"
            )
            return 1
        notice(f"Daemon tidak aktif ({settings.daemon_url}).")
        pid = _pid(settings)
        if pid:
            get_console().print(f"   [muted]pid file usang: {pid}[/]")
        return 1

    console = get_console()
    console.print(f"[report]Daemon aktif[/] [muted]{settings.daemon_url}[/]")
    console.print(f"   [muted]version[/] {data.get('version')}")
    console.print(f"   [muted]notes  [/] {data.get('notes')}")
    jobs = data.get("jobs") or {}
    console.print(
        f"   [muted]queue  [/] " + (", ".join(f"{k}={v}" for k, v in jobs.items()) or "kosong")
    )
    worker = data.get("worker") or {}
    console.print(
        f"   [muted]worker [/] processed={worker.get('processed')} failures={worker.get('failures')}"
    )
    console.print(f"   [muted]vectors[/] {'sqlite-vec' if data.get('vec') else 'numpy'}")
    console.print(f"   [muted]log    [/] {settings.daemon_log}")
    return 0


def logs(settings: Settings, follow: bool = False) -> int:
    path: Path = settings.daemon_log
    if not path.exists():
        notice(f"Belum ada log di {path}")
        return 0

    def _tail(lines: int = 40) -> None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            warning(str(exc))
            return
        for line in content[-lines:]:
            get_console().print(line, markup=False, highlight=False)

    _tail()
    if not follow:
        return 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, os.SEEK_END)
            while True:
                line = handle.readline()
                if line:
                    get_console().print(line.rstrip(), markup=False, highlight=False)
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        warning(str(exc))
        return 1


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(pid) in result.stdout
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
    time.sleep(0.4)


def daemon_command(action: str, *, follow: bool = False) -> int:
    settings = load_settings()
    action = (action or "status").lower()
    if action == "start":
        return start(settings)
    if action == "stop":
        return stop(settings)
    if action == "restart":
        return restart(settings)
    if action == "status":
        return status(settings)
    if action in {"logs", "log"}:
        return logs(settings, follow=follow)
    warning(f"aksi tidak dikenal: {action} (pakai start|stop|restart|status|logs)")
    return 2
