"""Configuration and path resolution for RAPHAEL.

Resolution order (highest wins):
1. ``RAPHAEL_HOME`` environment variable
2. ``~/.raphael/config.toml`` (``home`` key, then individual keys)
3. Built-in defaults

The vault is a single folder. Copy it to another machine and the whole
second brain travels with it (PRD §7, Data Portability).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _default_home() -> Path:
    env = os.environ.get("RAPHAEL_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".raphael"


DEFAULT_DAEMON_PORT = 8787


@dataclass(slots=True)
class CloudConfig:
    """Optional hybrid-cloud fallback (P2). Disabled unless explicitly enabled."""

    enabled: bool = False
    base_url: str = ""
    api_key_env: str = "RAPHAEL_CLOUD_API_KEY"
    model: str = ""
    timeout: float = 60.0

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")

    @property
    def usable(self) -> bool:
        return bool(self.enabled and self.base_url and self.api_key)


@dataclass(slots=True)
class Settings:
    """Runtime settings for the CLI, the daemon, and the TUI."""

    home: Path = field(default_factory=_default_home)
    llm_host: str = "http://127.0.0.1:11434"
    chat_model: str = "qwen2.5:7b"
    embed_model: str = "bge-m3"
    embedding_dim: int = 768
    request_timeout: float = 120.0
    embed_timeout: float = 60.0
    num_ctx: int = 8192
    temperature: float = 0.1
    daemon_host: str = "127.0.0.1"
    daemon_port: int = DEFAULT_DAEMON_PORT
    auto_start_daemon: bool = True
    link_threshold: float = 0.78
    link_limit: int = 5
    chunk_size: int = 900
    chunk_overlap: int = 120
    editor: str | None = None
    cloud: CloudConfig = field(default_factory=CloudConfig)

    # ---- derived paths -------------------------------------------------
    @property
    def vault_dir(self) -> Path:
        return self.home / "vault"

    @property
    def db_path(self) -> Path:
        return self.home / "raphael.db"

    @property
    def log_dir(self) -> Path:
        return self.home / "logs"

    @property
    def daemon_log(self) -> Path:
        return self.log_dir / "daemon.log"

    @property
    def daemon_pid_file(self) -> Path:
        return self.home / "daemon.pid"

    @property
    def config_file(self) -> Path:
        return self.home / "config.toml"

    @property
    def daemon_url(self) -> str:
        return f"http://{self.daemon_host}:{self.daemon_port}"

    # ---- lifecycle -----------------------------------------------------
    def ensure_dirs(self) -> None:
        for directory in (self.home, self.vault_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)
        for category in ("inbox", "projects", "references", "synthesized", "journal"):
            (self.vault_dir / category).mkdir(parents=True, exist_ok=True)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def load_settings(home: Path | None = None) -> Settings:
    """Load settings from disk, applying environment overrides."""

    settings = Settings()
    candidate_home = home or _default_home()
    settings.home = Path(candidate_home).expanduser()

    config_path = settings.config_file
    data: dict[str, Any] = {}
    if config_path.exists():
        try:
            with config_path.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            data = {}

    if data.get("home"):
        settings.home = Path(str(data["home"])).expanduser()

    llm = data.get("llm", {})
    settings.llm_host = str(llm.get("host", settings.llm_host))
    settings.chat_model = str(llm.get("chat_model", settings.chat_model))
    settings.embed_model = str(llm.get("embed_model", settings.embed_model))
    settings.embedding_dim = int(llm.get("embedding_dim", settings.embedding_dim))
    settings.num_ctx = int(llm.get("num_ctx", settings.num_ctx))
    settings.temperature = float(llm.get("temperature", settings.temperature))
    settings.request_timeout = float(llm.get("timeout", settings.request_timeout))

    daemon = data.get("daemon", {})
    settings.daemon_host = str(daemon.get("host", settings.daemon_host))
    settings.daemon_port = int(daemon.get("port", settings.daemon_port))
    settings.auto_start_daemon = _as_bool(
        daemon.get("auto_start", settings.auto_start_daemon), settings.auto_start_daemon
    )

    memory = data.get("memory", {})
    settings.link_threshold = float(memory.get("link_threshold", settings.link_threshold))
    settings.link_limit = int(memory.get("link_limit", settings.link_limit))
    settings.chunk_size = int(memory.get("chunk_size", settings.chunk_size))
    settings.chunk_overlap = int(memory.get("chunk_overlap", settings.chunk_overlap))

    cloud = data.get("cloud", {})
    settings.cloud = CloudConfig(
        enabled=_as_bool(cloud.get("enabled", False)),
        base_url=str(cloud.get("base_url", "")),
        api_key_env=str(cloud.get("api_key_env", "RAPHAEL_CLOUD_API_KEY")),
        model=str(cloud.get("model", "")),
        timeout=float(cloud.get("timeout", 60.0)),
    )

    settings.editor = data.get("editor") or os.environ.get("EDITOR") or os.environ.get("VISUAL")

    # Environment overrides for the most common knobs.
    if os.environ.get("RAPHAEL_LLM_HOST"):
        settings.llm_host = os.environ["RAPHAEL_LLM_HOST"].rstrip("/")
    if os.environ.get("RAPHAEL_CHAT_MODEL"):
        settings.chat_model = os.environ["RAPHAEL_CHAT_MODEL"]
    if os.environ.get("RAPHAEL_EMBED_MODEL"):
        settings.embed_model = os.environ["RAPHAEL_EMBED_MODEL"]
    if os.environ.get("RAPHAEL_DAEMON_PORT"):
        try:
            settings.daemon_port = int(os.environ["RAPHAEL_DAEMON_PORT"])
        except ValueError:
            pass

    return settings
