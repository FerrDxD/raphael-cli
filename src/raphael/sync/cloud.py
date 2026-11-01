"""Optional hybrid cloud sync (P2, off by default).

Two deliberately boring mechanisms:

* **Git sync** — commit the vault to a local git repo and, when a remote is
  configured, push. This gives encrypted-at-rest-by-you versioned backups with
  no vendor lock-in (PRD §3.3).
* **Network probe** — a cheap connectivity check so callers can decide whether
  cloud fallbacks are even worth attempting.

Nothing here ever blocks core functionality: every method returns a result
object instead of raising.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from raphael.config import Settings


@dataclass(slots=True)
class SyncResult:
    ok: bool
    message: str
    committed: bool = False
    pushed: bool = False


def online(timeout: float = 1.5) -> bool:
    """Best-effort connectivity probe (1.1.1.1 DNS-over-HTTPS endpoint)."""

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get("https://1.1.1.1/dns-query", params={"name": "example.com", "type": "A"}, headers={"accept": "application/dns-json"})
            return response.status_code < 500
    except (httpx.HTTPError, OSError):
        return False


class GitSync:
    """Commit (and optionally push) the vault to a git repository."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repo = settings.home

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def available(self) -> bool:
        return shutil.which("git") is not None

    def ensure_repo(self) -> SyncResult:
        if not self.available():
            return SyncResult(False, "git tidak terpasang")
        if (self.repo / ".git").exists():
            return SyncResult(True, "repository sudah ada")
        result = self._git("init")
        if result.returncode != 0:
            return SyncResult(False, result.stderr.strip() or "git init gagal")
        ignore = self.repo / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*.db\n*.db-wal\n*.db-shm\nlogs/\ndaemon.pid\n", encoding="utf-8")
        return SyncResult(True, "repository dibuat")

    def commit(self, message: str = "raphael: vault snapshot") -> SyncResult:
        if not self.available():
            return SyncResult(False, "git tidak terpasang")
        if not (self.repo / ".git").exists():
            prepared = self.ensure_repo()
            if not prepared.ok:
                return prepared
        self._git("add", "-A")
        status = self._git("status", "--porcelain")
        if status.returncode != 0:
            return SyncResult(False, status.stderr.strip() or "git status gagal")
        if not status.stdout.strip():
            return SyncResult(True, "tidak ada perubahan")
        result = self._git("commit", "-m", message)
        if result.returncode != 0:
            return SyncResult(False, result.stderr.strip() or "git commit gagal")
        return SyncResult(True, "snapshot di-commit", committed=True)

    def push(self) -> SyncResult:
        remotes = self._git("remote")
        if remotes.returncode != 0 or not remotes.stdout.strip():
            return SyncResult(False, "tidak ada remote git terkonfigurasi")
        result = self._git("push")
        if result.returncode != 0:
            return SyncResult(False, result.stderr.strip() or "git push gagal")
        return SyncResult(True, "snapshot didorong ke remote", pushed=True)

    def backup(self, message: str = "raphael: vault snapshot") -> SyncResult:
        """Commit, then push only if online and a remote exists."""

        committed = self.commit(message)
        if not committed.ok:
            return committed
        if not online():
            return SyncResult(True, "commit lokal selesai (offline, push dilewati)", committed=committed.committed)
        pushed = self.push()
        if not pushed.ok:
            return SyncResult(
                True,
                f"{committed.message}; {pushed.message}",
                committed=committed.committed,
            )
        return SyncResult(True, f"{committed.message}; {pushed.message}", True, True)


class CloudSync:
    """Facade used by the CLI; disabled unless ``cloud.enabled`` is true."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.git = GitSync(settings)

    @property
    def enabled(self) -> bool:
        return self.settings.cloud.enabled

    def backup(self, message: str = "raphael: vault snapshot") -> SyncResult:
        if not self.enabled:
            return SyncResult(False, "cloud sync dinonaktifkan (lihat config.toml)")
        return self.git.backup(message)

    def snapshot_path(self) -> Path:
        return self.settings.home
