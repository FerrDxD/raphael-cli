"""Hybrid memory: BM25 keyword search fused with vector recall.

``All of Creation`` (PRD §4) needs both halves:
* SQLite FTS5 answers "where does this exact word appear?" in milliseconds.
* Vector memory answers "what else means the same thing?" even with no shared
  vocabulary.

Results are merged with Reciprocal Rank Fusion so neither side dominates, and
the whole thing still works when the embedding engine is offline (keyword-only,
clearly labelled).
"""

from __future__ import annotations

from typing import Any, Sequence

from raphael.brain.llm import LLM, LLMError
from raphael.config import Settings
from raphael.models import Note, SearchHit
from raphael.storage.db import Database
from raphael.storage.vault import Vault
from raphael.utils import chunk_text, truncate

RRF_K = 60


class HybridRetriever:
    """Owns the chunk/vector lifecycle and the hybrid query path."""

    def __init__(self, db: Database, vault: Vault, settings: Settings, llm: LLM) -> None:
        self.db = db
        self.vault = vault
        self.settings = settings
        self.llm = llm

    # ------------------------------------------------------------------
    # indexing
    # ------------------------------------------------------------------
    def index_note(self, note: Note, *, embed: bool = True) -> int:
        """(Re)chunk a note and embed it. Returns the number of chunks."""

        texts = chunk_text(
            f"{note.title}\n\n{note.body}",
            size=self.settings.chunk_size,
            overlap=self.settings.chunk_overlap,
        )
        if not texts and note.summary:
            texts = [note.summary]
        self.db.replace_chunks(note.id, texts)
        if embed and texts:
            self.embed_pending(note_id=note.id)
        return len(texts)

    def embed_pending(self, *, note_id: str | None = None, limit: int = 400) -> int:
        """Embed chunks that have no vector yet. Returns how many were filled."""

        if not self.llm.local_available():
            return 0
        chunks = self.db.pending_embeddings(limit=limit)
        if note_id:
            chunks = [c for c in chunks if c.note_id == note_id]
        if not chunks:
            return 0

        done = 0
        batch_size = 16
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            try:
                vectors = self.llm.embed([c.text for c in batch])
            except LLMError:
                break
            for chunk, vector in zip(batch, vectors):
                if chunk.chunk_id is None:
                    continue
                self.db.set_embedding(chunk.chunk_id, vector)
                done += 1
        return done

    def needs_reindex(self, note: Note) -> bool:
        stored = self.db.get_note(note.id)
        if stored is None:
            return True
        if stored.content_hash != note.content_hash:
            return True
        return self.db.count_chunks_for(note.id) == 0

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------
    def search(self, query: str, limit: int = 10, *, tag: str | None = None) -> list[SearchHit]:
        query = (query or "").strip()
        if not query:
            return []

        keyword_hits = self.db.search_keyword(query, limit=max(limit * 3, 20))
        vector_hits = self._vector_hits(query, limit=max(limit * 3, 20))

        merged: dict[str, SearchHit] = {}
        scores: dict[str, float] = {}

        for rank, hit in enumerate(keyword_hits, start=1):
            merged.setdefault(hit.note_id, hit)
            scores[hit.note_id] = scores.get(hit.note_id, 0.0) + 1.0 / (RRF_K + rank)

        for rank, (note_id, chunk_id, similarity) in enumerate(vector_hits, start=1):
            scores[note_id] = scores.get(note_id, 0.0) + 1.0 / (RRF_K + rank)
            if note_id not in merged:
                chunk_text_value = self._chunk_text(chunk_id)
                note = self.db.get_note(note_id)
                merged[note_id] = SearchHit(
                    note_id=note_id,
                    title=note.title if note else note_id,
                    snippet=truncate(chunk_text_value, 240),
                    score=similarity,
                    match="vector",
                )
            else:
                merged[note_id].score = max(merged[note_id].score, similarity)
                merged[note_id].match = "hybrid"

        if not scores and tag:
            # Tag-only browse: no text query matched anything.
            return [
                SearchHit(
                    note_id=note.id,
                    title=note.title,
                    snippet=truncate(note.summary or note.body, 200),
                    score=0.5,
                    match="keyword",
                )
                for note in self.db.list_notes(tag=tag, limit=limit)
            ]

        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        results: list[SearchHit] = []
        for note_id, fused in ordered:
            hit = merged[note_id]
            if tag and tag.lower() not in [t.lower() for t in (self._tags(note_id))]:
                continue
            hit.score = round(fused * RRF_K, 4)
            results.append(hit)
            if len(results) >= limit:
                break
        return results

    def _tags(self, note_id: str) -> list[str]:
        note = self.db.get_note(note_id)
        return note.tags if note else []

    def _vector_hits(self, query: str, limit: int) -> list[tuple[str, int, float]]:
        if self.db.embedded_count() == 0:
            return []
        try:
            vector = self.llm.embed_one(query)
        except (LLMError, Exception):  # noqa: BLE001 - degrade to keyword-only
            return []
        if not vector:
            return []
        return self.db.search_vectors(vector, limit=limit)

    def _chunk_text(self, chunk_id: int) -> str:
        row = self.db.connection.execute(
            "SELECT text FROM note_chunks WHERE chunk_id=?", (chunk_id,)
        ).fetchone()
        return str(row["text"]) if row else ""

    # ------------------------------------------------------------------
    # relations
    # ------------------------------------------------------------------
    def related_notes(
        self, note: Note, *, limit: int = 5, threshold: float | None = None
    ) -> list[tuple[Note, float]]:
        """Vector-nearest neighbours of a note, excluding itself."""

        threshold = self.settings.link_threshold if threshold is None else threshold
        if self.db.embedded_count() == 0:
            return []

        seed = self._note_seed_vector(note)
        if not seed:
            return []

        raw = self.db.search_vectors(seed, limit=limit * 4, exclude=note.id)
        best: dict[str, float] = {}
        for note_id, _chunk_id, similarity in raw:
            if similarity < threshold:
                continue
            best[note_id] = max(best.get(note_id, 0.0), similarity)

        ordered = sorted(best.items(), key=lambda item: item[1], reverse=True)[:limit]
        out: list[tuple[Note, float]] = []
        for note_id, similarity in ordered:
            candidate = self.db.get_note(note_id)
            if candidate is not None:
                out.append((candidate, round(similarity, 4)))
        return out

    def _note_seed_vector(self, note: Note) -> list[float]:
        row = self.db.connection.execute(
            "SELECT vector FROM note_chunks WHERE note_id=? AND vector IS NOT NULL "
            "ORDER BY chunk_index LIMIT 1",
            (note.id,),
        ).fetchone()
        if row and row["vector"]:
            from raphael.storage.db import _unpack

            return _unpack(row["vector"])
        try:
            return self.llm.embed_one(f"{note.title}\n\n{note.body}"[:2000])
        except Exception:  # noqa: BLE001
            return []

    # ------------------------------------------------------------------
    # RAG context
    # ------------------------------------------------------------------
    def build_context(
        self, hits: Sequence[SearchHit], *, max_chars: int = 6000, per_note: int = 1200
    ) -> tuple[str, list[Note]]:
        """Render retrieved notes into a compact prompt block with citations."""

        blocks: list[str] = []
        used: list[Note] = []
        total = 0
        for index, hit in enumerate(hits, start=1):
            note = self.db.get_note(hit.note_id)
            if note is None:
                continue
            body = truncate(note.body, per_note)
            block = (
                f"[{index}] id={note.id} | title={note.title} | tags={','.join(note.tags)}\n"
                f"{body}"
            )
            if total + len(block) > max_chars:
                break
            blocks.append(block)
            used.append(note)
            total += len(block)
        return "\n\n---\n\n".join(blocks), used

    # ------------------------------------------------------------------
    # maintenance
    # ------------------------------------------------------------------
    def reindex_all(self, *, embed: bool = True) -> dict[str, int]:
        """Rebuild the index from the vault (the files always win)."""

        files = self.vault.all_files()
        known = {path: info for path, info in self.db.note_hashes().items()}
        seen: set[str] = set()
        indexed = 0
        for path in files:
            note = self.vault.parse(path)
            if note is None:
                continue
            seen.add(note.file_path)
            previous = known.get(note.file_path)
            if previous and previous[1] == note.content_hash:
                seen.add(note.id)
                continue
            self.db.upsert_note(note)
            self.index_note(note, embed=embed)
            indexed += 1

        removed = 0
        for file_path, (note_id, _hash) in known.items():
            if file_path not in seen and not (self.vault.root / file_path).exists():
                self.db.delete_note(note_id)
                removed += 1

        return {"scanned": len(files), "indexed": indexed, "removed": removed}

    def stats(self) -> dict[str, Any]:
        return self.db.stats()
