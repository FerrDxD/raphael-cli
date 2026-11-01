"""Specialist sub-agents (AGENTS.md §2).

One skill = one agent, each with a narrow prompt, structured output, and a
deterministic fallback so the product still works with no model installed.

| Agent      | Skill               | Output                         |
|------------|---------------------|--------------------------------|
| Appraiser  | Analytical Appraisal| ``AppraisalResult`` (JSON)     |
| Linker     | All of Creation     | ``list[Link]``                 |
| Synthesizer| Synthesize          | ``SynthesisResult``            |
| Separator  | Separate            | ``SplitResult`` (JSON)         |
| Answerer   | Recall / Q&A        | cited prose                    |

Every agent receives *only* the material it needs (context hygiene) and is
told to reply in the user's language.
"""

from __future__ import annotations

import json
import re
from typing import Iterable, Sequence

from raphael.brain.llm import LLM, LLMError
from raphael.models import (
    AppraisalResult,
    Link,
    Note,
    SplitResult,
    SplitTask,
    SynthesisResult,
)
from raphael.storage.db import Database
from raphael.utils import (
    capitalize_entities,
    derive_title,
    first_sentences,
    keywords,
    normalize_whitespace,
    truncate,
)

VALID_CATEGORIES = ("inbox", "projects", "references", "synthesized", "journal")

APPRAISAL_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "tags", "entities", "category"],
}

SPLIT_SCHEMA = {
    "type": "object",
    "properties": {
        "goal": {"type": "string"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "priority": {"type": "string"},
                    "estimate": {"type": "string"},
                },
                "required": ["title"],
            },
        },
    },
    "required": ["tasks"],
}

LINK_SCHEMA = {
    "type": "object",
    "properties": {
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "relation_type": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
                "required": ["index", "relation_type", "reasoning"],
            },
        }
    },
    "required": ["links"],
}

CONTRADICTION_SCHEMA = {
    "type": "object",
    "properties": {
        "contradictions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "a": {"type": "string"},
                    "b": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["a", "b", "detail"],
            },
        }
    },
    "required": ["contradictions"],
}

BASE_PERSONA = (
    "You are a sub-agent of RAPHAEL, a local-first personal knowledge system. "
    "Be precise, terse, and factual. Never invent facts that are not present in "
    "the provided material. Reply in the same language as the user's material."
)


def _rules_block(db: Database | None) -> str:
    """User preferences captured by ``rph evolve`` (Skill: Alteration)."""

    if db is None:
        return ""
    rules = [value for key, value in db.list_rules() if key.startswith("rule:")]
    if not rules:
        return ""
    joined = "\n".join(f"- {rule}" for rule in rules[:20])
    return f"\n\nPermanent user preferences (always obey):\n{joined}"


def _coerce_tags(raw: object) -> list[str]:
    if isinstance(raw, str):
        raw = [part for part in raw.replace("#", " ").split(",")]
    if not isinstance(raw, (list, tuple, set)):
        return []
    tags: list[str] = []
    for item in raw:
        tag = str(item).strip().lower().lstrip("#").replace(" ", "-")
        if tag and tag not in tags:
            tags.append(tag[:40])
    return tags[:6]


def _coerce_entities(raw: object) -> list[str]:
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, (list, tuple, set)):
        return []
    out: list[str] = []
    for item in raw:
        value = str(item).strip()
        if value and value.lower() not in [o.lower() for o in out]:
            out.append(value[:60])
    return out[:10]


def _coerce_category(raw: object, fallback: str = "inbox") -> str:
    value = str(raw or "").strip().lower()
    if value in VALID_CATEGORIES:
        return value
    return fallback


class Appraiser:
    """Analytical Appraisal: title, summary, tags, entities, category."""

    def __init__(self, llm: LLM, db: Database | None = None) -> None:
        self.llm = llm
        self.db = db

    def appraise(
        self, text: str, *, hint: str | None = None, default_category: str = "inbox"
    ) -> AppraisalResult:
        text = normalize_whitespace(text)
        if not text:
            return AppraisalResult(title="Untitled note", engine="heuristic")

        if not self.llm.local_available():
            return self._heuristic(text, default_category)

        system = (
            f"{BASE_PERSONA}{_rules_block(self.db)}\n\n"
            "Task: analyse the note and extract structured metadata.\n"
            "Rules:\n"
            "- title: max 80 characters, concrete, no quotes.\n"
            "- summary: 1-2 sentences capturing the essence.\n"
            "- tags: 2-5 lowercase kebab-case topical tags (no '#' symbol).\n"
            "- entities: proper nouns, people, places, systems, or key terms.\n"
            "- category: one of inbox, projects, references, synthesized, journal.\n"
            "- key_points: up to 3 short factual bullets.\n"
            "Return JSON only."
        )
        user = f"Note to analyse:\n\n{truncate(text, 6000)}"
        if hint:
            user += f"\n\nUser hint: {hint}"

        try:
            data = self.llm.json_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                APPRAISAL_SCHEMA,
            )
        except LLMError:
            return self._heuristic(text, default_category)

        title = str(data.get("title") or "").strip() or derive_title(text)
        summary = str(data.get("summary") or "").strip() or first_sentences(text)
        tags = _coerce_tags(data.get("tags")) or keywords(text, 4)
        entities = _coerce_entities(data.get("entities")) or capitalize_entities(text)
        category = _coerce_category(data.get("category"), default_category)
        points = [str(p).strip() for p in (data.get("key_points") or []) if str(p).strip()]

        return AppraisalResult(
            title=truncate(title, 90),
            summary=truncate(summary, 400),
            tags=tags,
            entities=entities,
            category=category,  # type: ignore[arg-type]
            key_points=points[:3],
            engine=f"{self.llm.engine}",
        )

    def _heuristic(self, text: str, category: str) -> AppraisalResult:
        return AppraisalResult(
            title=truncate(derive_title(text), 90),
            summary=first_sentences(text, 2, 280),
            tags=keywords(text, 4),
            entities=capitalize_entities(text),
            category=_coerce_category(category),  # type: ignore[arg-type]
            key_points=[],
            engine="heuristic",
        )


class Linker:
    """All of Creation: propose relations between notes."""

    def __init__(self, llm: LLM, db: Database | None = None) -> None:
        self.llm = llm
        self.db = db

    def link(
        self, note: Note, candidates: Sequence[tuple[Note, float]]
    ) -> list[Link]:
        """Turn similarity candidates into typed, explained graph edges."""

        if not candidates:
            return []

        links = [
            Link(
                source_note_id=note.id,
                target_note_id=candidate.id,
                relation_type="similar",
                similarity_score=round(score, 4),
                reasoning="Kemiripan semantik vektor.",
            )
            for candidate, score in candidates
        ]

        if not self.llm.local_available():
            return links

        listing = "\n\n".join(
            f"[{index}] {candidate.title}\n{truncate(candidate.summary or candidate.body, 400)}"
            for index, (candidate, _score) in enumerate(candidates, start=1)
        )
        system = (
            f"{BASE_PERSONA}{_rules_block(self.db)}\n\n"
            "Task: for each candidate, classify how it relates to the SOURCE note.\n"
            "relation_type must be one of: similar, contradicts, expands_on.\n"
            "reasoning: one short sentence, max 140 characters.\n"
            "Return JSON only."
        )
        user = (
            f"SOURCE note:\n{note.title}\n{truncate(note.summary or note.body, 800)}\n\n"
            f"CANDIDATES:\n{listing}"
        )
        try:
            data = self.llm.json_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                LINK_SCHEMA,
            )
        except LLMError:
            return links

        by_index = {index: link for index, link in enumerate(links, start=1)}
        for item in data.get("links") or []:
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            link = by_index.get(index)
            if link is None:
                continue
            relation = str(item.get("relation_type") or "similar").strip().lower()
            if relation in {"similar", "contradicts", "expands_on"}:
                link.relation_type = relation  # type: ignore[assignment]
            reasoning = str(item.get("reasoning") or "").strip()
            if reasoning:
                link.reasoning = truncate(reasoning, 160)
        return links


class Answerer:
    """Grounded Q&A over the local vault (zero hallucination)."""

    def __init__(self, llm: LLM, db: Database | None = None) -> None:
        self.llm = llm
        self.db = db

    def answer(
        self,
        question: str,
        context: str,
        notes: Sequence[Note],
        *,
        history: Sequence[dict[str, str]] | None = None,
    ) -> str:
        if not context.strip():
            return (
                "Tidak ada catatan lokal yang cocok dengan pertanyaan ini. "
                "Gunakan 'rph add' atau 'rph ingest' untuk menambah materi."
            )

        system = (
            f"{BASE_PERSONA}{_rules_block(self.db)}\n\n"
            "Task: answer the question using ONLY the numbered notes below.\n"
            "Rules:\n"
            "- Cite every claim with its note number, e.g. (catatan 2).\n"
            "- If the notes do not contain the answer, say so explicitly.\n"
            "- Never use outside knowledge. Never speculate.\n"
            "- Be compact: no preamble, no restating the question.\n\n"
            f"NOTES:\n{context}"
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        for turn in history or []:
            if turn.get("role") in {"user", "assistant"}:
                messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": question})

        try:
            return self.llm.chat(messages, temperature=0.2).strip()
        except LLMError:
            return self._fallback(question, notes)

    @staticmethod
    def _fallback(question: str, notes: Sequence[Note]) -> str:
        if not notes:
            return "Mesin lokal tidak aktif dan tidak ada catatan yang cocok."
        lines = [
            "Mesin LLM lokal tidak aktif; menampilkan catatan sumber yang paling relevan:",
        ]
        for index, note in enumerate(notes, start=1):
            lines.append(f"{index}. {note.title} ({note.id})")
            if note.summary:
                lines.append(f"   {note.summary}")
        return "\n".join(lines)

    def find_contradictions(self, notes: Sequence[Note]) -> list[dict[str, str]]:
        """Optional audit pass used by ``rph ask --audit`` and the daemon."""

        if len(notes) < 2 or not self.llm.local_available():
            return []
        listing = "\n\n".join(
            f"[{index}] {note.title} ({note.id})\n{truncate(note.summary or note.body, 700)}"
            for index, note in enumerate(notes, start=1)
        )
        system = (
            f"{BASE_PERSONA}\n\n"
            "Task: find factual contradictions between the numbered notes.\n"
            "Only report a contradiction when two notes state incompatible facts.\n"
            "If there are none, return an empty list. Return JSON only."
        )
        try:
            data = self.llm.json_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": listing},
                ],
                CONTRADICTION_SCHEMA,
            )
        except LLMError:
            return []

        out: list[dict[str, str]] = []
        for item in data.get("contradictions") or []:
            detail = str(item.get("detail") or "").strip()
            if not detail:
                continue
            out.append(
                {
                    "a": str(item.get("a") or ""),
                    "b": str(item.get("b") or ""),
                    "detail": truncate(detail, 300),
                }
            )
        return out[:3]


class Synthesizer:
    """Synthesize: merge notes into one coherent document."""

    def __init__(self, llm: LLM, db: Database | None = None) -> None:
        self.llm = llm
        self.db = db

    def synthesize(
        self,
        notes: Sequence[Note],
        *,
        instruction: str | None = None,
        title: str | None = None,
    ) -> SynthesisResult:
        if not notes:
            raise ValueError("no notes to synthesize")

        default_title = title or f"Sintesis: {notes[0].title}"
        if not self.llm.local_available():
            return self._fallback(notes, default_title, instruction)

        material = "\n\n---\n\n".join(
            f"### Sumber {index}: {note.title} (id: {note.id})\n{truncate(note.body, 2500)}"
            for index, note in enumerate(notes, start=1)
        )
        system = (
            f"{BASE_PERSONA}{_rules_block(self.db)}\n\n"
            "Task: merge the source notes into ONE coherent Markdown document.\n"
            "Rules:\n"
            "- Output Markdown only (no code fences around the whole answer).\n"
            "- Start with a single H1 title.\n"
            "- Use H2 sections with a logical structure, not note-by-note dumping.\n"
            "- Merge duplicates; keep every distinct fact.\n"
            "- Add a final '## Sumber' section listing the source ids.\n"
            "- Do not invent information."
        )
        user = f"Sources:\n\n{material}"
        if instruction:
            user += f"\n\nAdditional instruction: {instruction}"

        try:
            markdown = self.llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.3,
            ).strip()
        except LLMError:
            return self._fallback(notes, default_title, instruction)

        markdown = _strip_fences(markdown)
        derived = _h1_title(markdown) or default_title
        return SynthesisResult(
            title=derived,
            markdown=markdown,
            sources=[note.id for note in notes],
            engine=self.llm.engine,
        )

    @staticmethod
    def _fallback(
        notes: Sequence[Note], title: str, instruction: str | None
    ) -> SynthesisResult:
        parts = [f"# {title}", ""]
        if instruction:
            parts.extend([f"> Instruksi: {instruction}", ""])
        for note in notes:
            parts.extend([f"## {note.title}", "", note.body.strip(), ""])
        parts.append("## Sumber")
        parts.extend(f"- `{note.id}` — {note.title}" for note in notes)
        return SynthesisResult(
            title=title,
            markdown="\n".join(parts).strip() + "\n",
            sources=[note.id for note in notes],
            engine="heuristic",
        )


class Separator:
    """Separate: break a large goal into actionable terminal tasks."""

    def __init__(self, llm: LLM, db: Database | None = None) -> None:
        self.llm = llm
        self.db = db

    def split(self, text: str, *, goal: str | None = None) -> SplitResult:
        text = normalize_whitespace(text)
        if not text:
            return SplitResult(goal=goal or "", tasks=[], engine="heuristic")

        if not self.llm.local_available():
            return self._heuristic(text, goal)

        system = (
            f"{BASE_PERSONA}{_rules_block(self.db)}\n\n"
            "Task: decompose the material into 3-8 concrete, actionable tasks.\n"
            "Rules:\n"
            "- Each task starts with an imperative verb and is independently doable.\n"
            "- priority: high, medium, or low.\n"
            "- estimate: rough effort (e.g. '30m', '2h', '1d').\n"
            "- detail: one short sentence of context, may be empty.\n"
            "Return JSON only."
        )
        user = f"Goal: {goal or 'derive from material'}\n\nMaterial:\n{truncate(text, 6000)}"
        try:
            data = self.llm.json_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                SPLIT_SCHEMA,
            )
        except LLMError:
            return self._heuristic(text, goal)

        tasks: list[SplitTask] = []
        for item in data.get("tasks") or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            priority = str(item.get("priority") or "medium").strip().lower()
            if priority not in {"high", "medium", "low"}:
                priority = "medium"
            tasks.append(
                SplitTask(
                    title=truncate(title, 160),
                    detail=truncate(str(item.get("detail") or ""), 240),
                    priority=priority,  # type: ignore[arg-type]
                    estimate=truncate(str(item.get("estimate") or ""), 20),
                )
            )
        if not tasks:
            return self._heuristic(text, goal)
        return SplitResult(
            goal=str(data.get("goal") or goal or "").strip() or (goal or ""),
            tasks=tasks[:10],
            engine=self.llm.engine,
        )

    @staticmethod
    def _heuristic(text: str, goal: str | None) -> SplitResult:
        """No model? Bullets, numbering, and conjunctions still give a plan."""

        lines = [line.strip() for line in text.split("\n") if line.strip()]
        candidates: list[str] = []
        for line in lines:
            cleaned = line.lstrip("-*•0123456789.) ").strip()
            if len(cleaned.split()) < 2:
                continue
            # A prose line often chains several actions: "tulis draf, review,
            # lalu rilis". Split on list separators and coordinating words.
            for part in re.split(r"\s*(?:;|\b(?:lalu|kemudian|setelah itu|serta)\b|,\s*|\bdan\b)\s*", cleaned):
                part = part.strip(" .")
                if len(part.split()) >= 2:
                    candidates.append(part)

        if not candidates:
            candidates = [first_sentences(text, 1, 120)]

        tasks = [
            SplitTask(title=truncate(item, 140), priority="medium")
            for item in dict.fromkeys(candidates)
            if item
        ][:8]
        return SplitResult(goal=goal or derive_title(text), tasks=tasks, engine="heuristic")


class IntentRouter:
    """Chant Annulment: decide whether free text is a note or a question.

    Deliberately heuristic — routing must cost microseconds, not a model call.
    """

    QUESTION_STARTS = (
        "apa", "apakah", "bagaimana", "gimana", "kenapa", "mengapa", "siapa",
        "kapan", "dimana", "di mana", "berapa", "bisakah", "bolehkah", "jelaskan",
        "what", "how", "why", "who", "when", "where", "which", "can", "could",
        "should", "is ", "are ", "do ", "does ", "did ", "explain", "summarize",
        "summarise", "list ", "find ", "show ",
    )
    NOTE_STARTS = (
        "ide", "idea", "todo", "catat", "note", "ingat", "remember", "rencana",
        "plan", "log", "bug", "fix",
    )

    @classmethod
    def route(cls, text: str) -> str:
        clean = normalize_whitespace(text)
        lowered = clean.lower()
        if not clean:
            return "note"
        if lowered.endswith("?"):
            return "ask"
        if lowered.startswith(cls.NOTE_STARTS):
            return "note"
        if lowered.startswith(cls.QUESTION_STARTS):
            return "ask"
        # A bare imperative question without punctuation, e.g. "apa isi vault saya"
        words = lowered.split()
        if len(words) <= 12 and words and words[0] in {
            "apa", "siapa", "kapan", "kenapa", "mengapa", "bagaimana",
        }:
            return "ask"
        return "note"


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def _h1_title(markdown: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def parse_tags(value: Iterable[str]) -> list[str]:
    return _coerce_tags(list(value))
