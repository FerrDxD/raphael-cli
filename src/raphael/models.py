"""Pydantic models shared across RAPHAEL's storage, brain, and CLI layers.

Everything that crosses a module boundary is typed here, so the background
workers always exchange structured data instead of free-form text
(see AGENTS.md, "Structured Output First").
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Category = Literal["inbox", "projects", "references", "synthesized", "journal"]
JobStatus = Literal["PENDING", "RUNNING", "DONE", "FAILED"]
JobType = Literal["APPRAISE", "EMBED", "FIND_LINKS", "SYNTHESIZE", "REINDEX"]
RelationType = Literal["similar", "contradicts", "expands_on"]


class NoteFrontmatter(BaseModel):
    """YAML frontmatter written at the top of every vault file."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    category: Category = "inbox"
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    appraisal_status: Literal["pending", "completed", "heuristic", "failed"] = "pending"
    source: str | None = None
    project: str | None = None


class Note(BaseModel):
    """A note as stored in the vault and mirrored into SQLite."""

    id: str
    title: str
    body: str
    category: Category = "inbox"
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    summary: str = ""
    appraisal_status: Literal["pending", "completed", "heuristic", "failed"] = "pending"
    file_path: str = ""
    content_hash: str = ""
    source: str | None = None
    project: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def short_id(self) -> str:
        return self.id


class Chunk(BaseModel):
    """A paragraph-sized slice of a note, used for vector memory."""

    chunk_id: int | None = None
    note_id: str
    chunk_index: int
    text: str


class Link(BaseModel):
    """An edge in the knowledge graph (Skill: All of Creation)."""

    source_note_id: str
    target_note_id: str
    relation_type: RelationType = "similar"
    similarity_score: float = 0.0
    reasoning: str = ""


class Job(BaseModel):
    job_id: int | None = None
    job_type: JobType
    payload: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = "PENDING"
    created_at: datetime | None = None


class AppraisalResult(BaseModel):
    """Structured output of the Appraisal sub-agent."""

    title: str = ""
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    category: Category = "inbox"
    key_points: list[str] = Field(default_factory=list)
    engine: str = "heuristic"


class SearchHit(BaseModel):
    note_id: str
    title: str
    snippet: str
    score: float
    match: Literal["keyword", "vector", "hybrid"] = "keyword"


class ChatTurn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class SplitTask(BaseModel):
    title: str
    detail: str = ""
    priority: Literal["high", "medium", "low"] = "medium"
    estimate: str = ""


class SplitResult(BaseModel):
    goal: str = ""
    tasks: list[SplitTask] = Field(default_factory=list)
    engine: str = "heuristic"


class SynthesisResult(BaseModel):
    title: str
    markdown: str
    sources: list[str] = Field(default_factory=list)
    engine: str = "heuristic"


class AppraisalPayload(BaseModel):
    """Schema handed to the local LLM as a JSON format hint."""

    title: str = ""
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    category: str = "inbox"
    key_points: list[str] = Field(default_factory=list)
