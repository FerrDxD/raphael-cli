"""Shared fixtures: every test runs against a throwaway vault in tmp_path."""

from __future__ import annotations

from pathlib import Path

import pytest

from raphael.brain.llm import LLMError
from raphael.config import Settings
from raphael.service import Raphael


class OfflineLLM:
    """Stub engine: reports itself unavailable, so agents use fallbacks."""

    engine = "heuristic"

    def __init__(self, *args, **kwargs) -> None:
        pass

    def close(self) -> None:
        pass

    def local_available(self, refresh: bool = False) -> bool:
        return False

    def status(self) -> dict:
        return {
            "local_available": False,
            "host": "stub",
            "chat_model": "stub",
            "embed_model": "stub",
            "installed": [],
            "cloud_usable": False,
        }

    # The real engine wraps every transport failure in LLMError; the stub must
    # honour the same contract so fallback paths are exercised honestly.
    def chat(self, *args, **kwargs):
        raise LLMError("offline")

    def json_chat(self, *args, **kwargs):
        raise LLMError("offline")

    def embed(self, texts):
        raise LLMError("offline")

    def embed_one(self, text):
        raise LLMError("offline")


def _free_port() -> int:
    """Bind an ephemeral port so tests never talk to a developer's real daemon."""

    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    cfg = Settings(home=tmp_path / "raphael")
    cfg.auto_start_daemon = False
    cfg.daemon_port = _free_port()
    cfg.ensure_dirs()
    return cfg


@pytest.fixture()
def service(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Raphael:
    monkeypatch.setattr("raphael.service.LLM", OfflineLLM)
    svc = Raphael(settings)
    yield svc
    svc.close()


@pytest.fixture()
def fake_llm():
    """Deterministic online engine for agent tests."""

    class FakeLLM:
        engine = "local"

        def __init__(self, appraisal=None, split=None, answer="jawaban"):
            self.appraisal = appraisal or {}
            self.split = split or {}
            self.answer = answer
            self.calls: list[str] = []

        def local_available(self, refresh: bool = False) -> bool:
            return True

        def status(self):
            return {
                "local_available": True,
                "host": "fake",
                "chat_model": "fake",
                "embed_model": "fake",
                "installed": ["fake"],
                "cloud_usable": False,
            }

        def chat(self, messages, **kwargs):
            self.calls.append("chat")
            return self.answer

        def json_chat(self, messages, schema=None, **kwargs):
            self.calls.append("json_chat")
            text = " ".join(m.get("content", "") for m in messages)
            if "decompose" in text or "tasks" in text:
                return self.split or {"goal": "g", "tasks": [{"title": "Langkah satu"}]}
            if "contradictions" in text:
                return {"contradictions": []}
            return self.appraisal or {
                "title": "Judul Uji",
                "summary": "Ringkasan uji.",
                "tags": ["Tag Satu", "#tag-dua"],
                "entities": ["Shibuya"],
                "category": "projects",
                "key_points": ["poin"],
            }

        def embed(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

        def embed_one(self, text):
            return [1.0, 0.0, 0.0]

    return FakeLLM
