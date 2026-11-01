"""Local LLM access (Ollama) with an optional cloud fallback.

The offline engine is always tried first: RAPHAEL must work with no network
(PRD §3.1). Cloud is a P2 convenience, used only when explicitly enabled and
the local model is unreachable.
"""

from __future__ import annotations

import json
import re
import socket
from typing import Any, Sequence
from urllib.parse import urlsplit

import httpx

from raphael.config import Settings

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class LLMError(RuntimeError):
    """Raised when no engine can answer the request."""


def _extract_json(text: str) -> dict[str, Any]:
    """Parse JSON out of an LLM reply, tolerating prose and code fences."""

    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(text)
    if not match:
        return {}
    try:
        loaded = json.loads(match.group(0))
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return {}


class LocalEngine:
    """Ollama HTTP client (``http://127.0.0.1:11434`` by default)."""

    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.llm_host.rstrip("/")
        self._http: httpx.Client | None = None

    def _client(self) -> httpx.Client:
        """One pooled client for the whole process.

        Constructing an ``httpx.Client`` loads a TLS trust store, which costs
        seconds on Windows. Ollama lives on loopback over plain HTTP, so we pay
        that cost at most once (and skip it entirely with ``verify=False``).
        Timeouts are passed per request, not per client.
        """

        if self._http is None:
            self._http = httpx.Client(
                base_url=self.base_url,
                timeout=self.settings.request_timeout,
                verify=False,
                trust_env=False,  # a loopback daemon must never use a proxy
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            )
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    # ------------------------------------------------------------------
    def port_open(self, timeout: float = 0.25) -> bool:
        """Cheap liveness probe before any HTTP work.

        On Windows a refused connect to a closed loopback port can sit in SYN
        retransmit for seconds (firewall/AV interception), which would make
        every 'is Ollama up?' check feel like a hang. A raw socket with a short
        timeout answers the same question immediately.
        """

        parsed = urlsplit(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (OSError, ValueError):
            return False

    def available(self) -> bool:
        if not self.port_open():
            return False
        try:
            response = self._client().get("/api/tags", timeout=2.5)
            return response.status_code == 200
        except (httpx.HTTPError, OSError):
            return False

    def installed_models(self) -> list[str]:
        try:
            payload = self._client().get("/api/tags", timeout=5.0).json()
        except (httpx.HTTPError, OSError, ValueError):
            return []
        return [str(m.get("name", "")) for m in payload.get("models", []) if m.get("name")]

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str | None = None,
        fmt: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        num_ctx: int | None = None,
        timeout: float | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "model": model or self.settings.chat_model,
            "messages": list(messages),
            "stream": False,
            "options": {
                "temperature": (
                    self.settings.temperature if temperature is None else temperature
                ),
                "num_ctx": num_ctx or self.settings.num_ctx,
            },
        }
        if fmt is not None:
            body["format"] = fmt
        try:
            response = self._client().post(
                "/api/chat", json=body, timeout=timeout or self.settings.request_timeout
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:200]
            raise LLMError(f"local model error ({exc.response.status_code}): {detail}") from exc
        except (httpx.HTTPError, OSError) as exc:
            raise LLMError(f"cannot reach local engine at {self.base_url}: {exc}") from exc

        message = payload.get("message") or {}
        content = str(message.get("content", "")).strip()
        if not content:
            raise LLMError("local model returned an empty response")
        return content

    def embed(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        body = {"model": model or self.settings.embed_model, "input": list(texts)}
        try:
            response = self._client().post(
                "/api/embed", json=body, timeout=self.settings.embed_timeout
            )
            if response.status_code == 404:
                # Older Ollama builds only expose /api/embeddings (single input).
                return [self._embed_legacy(text, model) for text in texts]
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"embedding error ({exc.response.status_code}): "
                f"{exc.response.text[:160]}"
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise LLMError(f"cannot reach embedding engine: {exc}") from exc

        vectors = payload.get("embeddings") or []
        if not vectors:
            raise LLMError("embedding model returned no vectors")
        return [[float(x) for x in vector] for vector in vectors]

    def _embed_legacy(self, text: str, model: str | None) -> list[float]:
        body = {"model": model or self.settings.embed_model, "prompt": text}
        response = self._client().post(
            "/api/embeddings", json=body, timeout=self.settings.embed_timeout
        )
        response.raise_for_status()
        payload = response.json()
        vector = payload.get("embedding") or []
        if not vector:
            raise LLMError("embedding model returned no vector")
        return [float(x) for x in vector]


class CloudEngine:
    """OpenAI-compatible cloud endpoint (optional, P2)."""

    name = "cloud"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cloud = settings.cloud

    @property
    def usable(self) -> bool:
        return self.cloud.usable

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        fmt: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        **_ignored: Any,
    ) -> str:
        if not self.usable:
            raise LLMError("cloud fallback is not configured")
        body: dict[str, Any] = {
            "model": self.cloud.model or self.settings.chat_model,
            "messages": list(messages),
            "temperature": self.settings.temperature if temperature is None else temperature,
        }
        if isinstance(fmt, dict):
            body["response_format"] = {"type": "json_object"}
        elif fmt == "json":
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.cloud.api_key}"}
        try:
            with httpx.Client(timeout=self.cloud.timeout) as client:
                response = client.post(
                    f"{self.cloud.base_url.rstrip('/')}/chat/completions",
                    json=body,
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, OSError) as exc:
            raise LLMError(f"cloud request failed: {exc}") from exc
        try:
            return str(payload["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("cloud returned an unexpected payload") from exc

    def embed(self, texts: Sequence[str], **_ignored: Any) -> list[list[float]]:
        raise LLMError("cloud embeddings are not enabled")


class LLM:
    """Facade that prefers the local engine and degrades gracefully.

    ``self.engine`` records which backend produced the last answer, so the CLI
    can label output honestly (``[Report] ... (local)`` vs ``(heuristic)``).
    """

    def __init__(self, settings: Settings, *, offline_only: bool = False) -> None:
        self.settings = settings
        self.local = LocalEngine(settings)
        self.cloud = CloudEngine(settings)
        self.offline_only = offline_only
        self.engine = "local"
        self._available: bool | None = None

    def close(self) -> None:
        self.local.close()

    # ------------------------------------------------------------------
    def local_available(self, refresh: bool = False) -> bool:
        if refresh or self._available is None:
            self._available = self.local.available()
        return self._available

    def status(self) -> dict[str, Any]:
        available = self.local_available(refresh=True)
        return {
            "local_available": available,
            "host": self.settings.llm_host,
            "chat_model": self.settings.chat_model,
            "embed_model": self.settings.embed_model,
            "installed": self.local.installed_models() if available else [],
            "cloud_usable": self.cloud.usable and not self.offline_only,
        }

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        fmt: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        model: str | None = None,
    ) -> str:
        """Try local first, then cloud. Raises ``LLMError`` if both fail."""

        if self.local_available():
            try:
                text = self.local.chat(
                    messages, fmt=fmt, temperature=temperature, model=model
                )
                self.engine = "local"
                return text
            except LLMError:
                self._available = False
        if not self.offline_only and self.cloud.usable:
            text = self.cloud.chat(messages, fmt=fmt, temperature=temperature)
            self.engine = "cloud"
            return text
        raise LLMError(
            f"no LLM engine reachable (local: {self.settings.llm_host}). "
            "Start Ollama with 'ollama serve' or run 'rph doctor'."
        )

    def json_chat(
        self,
        messages: Sequence[dict[str, str]],
        schema: dict[str, Any] | None = None,
        *,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Ask for strict JSON; retry once with a repair hint on garbage."""

        fmt: str | dict[str, Any] = schema if schema else "json"
        raw = self.chat(messages, fmt=fmt, temperature=temperature)
        parsed = _extract_json(raw)
        if parsed:
            return parsed

        repair = list(messages) + [
            {"role": "assistant", "content": raw[:2000]},
            {
                "role": "user",
                "content": (
                    "Output is invalid JSON. Reply with ONLY a single valid JSON "
                    "object and nothing else. No prose, no markdown fences."
                ),
            },
        ]
        raw_retry = self.chat(repair, fmt="json", temperature=0.0)
        parsed = _extract_json(raw_retry)
        if not parsed:
            raise LLMError("model did not return valid JSON")
        return parsed

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.local_available():
            raise LLMError("local embedding engine is unavailable")
        return self.local.embed(texts)

    def embed_one(self, text: str) -> list[float]:
        vectors = self.embed([text])
        return vectors[0] if vectors else []
