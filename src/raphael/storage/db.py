"""SQLite persistence: metadata, FTS5 keyword search, vector memory, graph, jobs.

Design notes
------------
* One file (``~/.raphael/raphael.db``), no server to install.
* Keyword search uses SQLite FTS5 with the ``notes`` table as external content,
  so the index always mirrors the relational rows.
* Vector memory stores float32 blobs in ``note_chunks`` and *additionally*
  mirrors them into a ``vec0`` virtual table when ``sqlite-vec`` loads. If the
  extension is unavailable (common on Windows), search transparently falls back
  to a NumPy cosine scan. Same results, no hard dependency.
* A single connection guarded by a re-entrant lock keeps the daemon's worker
  threads and the HTTP handlers from stepping on each other.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import threading
from array import array
from pathlib import Path
from typing import Any, Iterable, Sequence

from raphael.models import Chunk, Job, Link, Note, SearchHit
from raphael.utils import content_hash, now, now_iso

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    file_path TEXT UNIQUE NOT NULL,
    title TEXT,
    raw_content TEXT,
    summary TEXT,
    category TEXT,
    tags_json TEXT,
    entities_json TEXT,
    content_hash TEXT,
    appraisal_status TEXT DEFAULT 'pending',
    source TEXT,
    project TEXT,
    created_at DATETIME,
    updated_at DATETIME
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    id UNINDEXED,
    title,
    raw_content,
    summary,
    tags_json,
    content='notes',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, id, title, raw_content, summary, tags_json)
    VALUES (new.rowid, new.id, new.title, new.raw_content, new.summary, new.tags_json);
END;

CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, id, title, raw_content, summary, tags_json)
    VALUES ('delete', old.rowid, old.id, old.title, old.raw_content, old.summary, old.tags_json);
END;

CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, id, title, raw_content, summary, tags_json)
    VALUES ('delete', old.rowid, old.id, old.title, old.raw_content, old.summary, old.tags_json);
    INSERT INTO notes_fts(rowid, id, title, raw_content, summary, tags_json)
    VALUES (new.rowid, new.id, new.title, new.raw_content, new.summary, new.tags_json);
END;

CREATE TABLE IF NOT EXISTS note_chunks (
    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    dim INTEGER,
    vector BLOB,
    UNIQUE(note_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_note_chunks_note ON note_chunks(note_id);

CREATE TABLE IF NOT EXISTS note_links (
    source_note_id TEXT,
    target_note_id TEXT,
    relation_type TEXT,
    similarity_score REAL,
    reasoning TEXT,
    PRIMARY KEY (source_note_id, target_note_id)
);

CREATE INDEX IF NOT EXISTS idx_note_links_target ON note_links(target_note_id);

CREATE TABLE IF NOT EXISTS background_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT,
    payload_json TEXT,
    status TEXT DEFAULT 'PENDING',
    attempts INTEGER DEFAULT 0,
    error TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME,
    finished_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON background_jobs(status, job_id);

CREATE TABLE IF NOT EXISTS config_kv (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at DATETIME
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _pack(vector: Sequence[float]) -> bytes:
    return array("f", vector).tobytes()


def _unpack(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return list(values)


# FTS5's unicode61 tokenizer does not stem, and Indonesian is agglutinative:
# "aturannya" and "aturan" are different tokens. Stripping the common enclitics
# and searching the root as a prefix ("aturan"*) recovers those matches without
# shipping a real stemmer.
_ENCLITICS = ("nya", "kah", "lah", "pun", "ku", "mu")


def _roots(token: str) -> list[str]:
    """Return the token plus its clitic-stripped root (when long enough)."""

    roots = [token]
    for suffix in _ENCLITICS:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            roots.append(token[: -len(suffix)])
            break
    return roots


def _fts_query(text: str) -> str:
    """Build a forgiving FTS5 query: quoted tokens joined with OR.

    Raw user input can contain characters FTS5 treats as operators, which would
    raise ``OperationalError``. Quoting every token makes search never crash.
    """

    tokens = [t for t in "".join(c if c.isalnum() else " " for c in text).split() if t]
    if not tokens:
        return ""
    terms: list[str] = []
    for token in tokens:
        for root in _roots(token):
            term = f'"{root}"' if root == token else f'"{root}"*'
            if term not in terms:
                terms.append(term)
    return " OR ".join(terms)


class Database:
    """Thin, explicit wrapper around the RAPHAEL SQLite file."""

    def __init__(self, path: Path, embedding_dim: int = 768) -> None:
        self.path = Path(path)
        self.embedding_dim = embedding_dim
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self.vec_enabled = False
        self._configure()
        self._migrate()

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------
    def _configure(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=15000")
            self._conn.commit()
        self._load_vec_extension()

    def _load_vec_extension(self) -> None:
        try:  # pragma: no cover - depends on host platform
            import sqlite_vec  # type: ignore

            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            self.vec_enabled = True
        except Exception:
            self.vec_enabled = False

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            self.set_meta("embedding_dim", str(self.embedding_dim))
        if self.vec_enabled:
            self._ensure_vec_table()

    def _ensure_vec_table(self) -> None:
        """Create the sqlite-vec index mirroring ``note_chunks`` vectors."""

        with self._lock:
            try:
                self._conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS note_embeddings USING vec0("
                    f"chunk_id INTEGER PRIMARY KEY, embedding FLOAT[{self.embedding_dim}])"
                )
                self._conn.commit()
            except sqlite3.Error:
                self.vec_enabled = False

    # ------------------------------------------------------------------
    # low-level helpers
    # ------------------------------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # ------------------------------------------------------------------
    # notes
    # ------------------------------------------------------------------
    def upsert_note(self, note: Note) -> None:
        stamp = now_iso()
        created = note.created_at.isoformat(timespec="seconds") if note.created_at else stamp
        updated = note.updated_at.isoformat(timespec="seconds") if note.updated_at else stamp
        # file_path is NOT NULL UNIQUE. Callers normally hand us the vault path,
        # but a note built straight from Markdown may arrive without one; derive
        # a stable placeholder from the id so the insert cannot collide.
        if not note.file_path:
            note.file_path = f"{note.category}/{note.id}.md"
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO notes (
                    id, file_path, title, raw_content, summary, category, tags_json,
                    entities_json, content_hash, appraisal_status, source, project,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    file_path=excluded.file_path,
                    title=excluded.title,
                    raw_content=excluded.raw_content,
                    summary=excluded.summary,
                    category=excluded.category,
                    tags_json=excluded.tags_json,
                    entities_json=excluded.entities_json,
                    content_hash=excluded.content_hash,
                    appraisal_status=excluded.appraisal_status,
                    source=excluded.source,
                    project=excluded.project,
                    updated_at=excluded.updated_at
                """,
                (
                    note.id,
                    note.file_path,
                    note.title,
                    note.body,
                    note.summary,
                    note.category,
                    json.dumps(note.tags, ensure_ascii=False),
                    json.dumps(note.entities, ensure_ascii=False),
                    note.content_hash or content_hash(note.body),
                    note.appraisal_status,
                    note.source,
                    note.project,
                    created,
                    updated,
                ),
            )
            self._conn.commit()

    def _row_to_note(self, row: sqlite3.Row) -> Note:
        from datetime import datetime

        def _dt(value: str | None):
            if not value:
                return None
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None

        return Note(
            id=row["id"],
            title=row["title"] or "",
            body=row["raw_content"] or "",
            category=row["category"] or "inbox",
            tags=json.loads(row["tags_json"] or "[]"),
            entities=json.loads(row["entities_json"] or "[]"),
            summary=row["summary"] or "",
            appraisal_status=row["appraisal_status"] or "pending",
            file_path=row["file_path"] or "",
            content_hash=row["content_hash"] or "",
            source=row["source"],
            project=row["project"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    def get_note(self, note_id: str) -> Note | None:
        row = self._conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        return self._row_to_note(row) if row else None

    def get_note_by_path(self, file_path: str) -> Note | None:
        row = self._conn.execute(
            "SELECT * FROM notes WHERE file_path=?", (file_path,)
        ).fetchone()
        return self._row_to_note(row) if row else None

    def resolve_note(self, token: str) -> Note | None:
        """Resolve a full ID, a unique ID prefix, or a title fragment."""

        token = (token or "").strip()
        if not token:
            return None
        note = self.get_note(token)
        if note:
            return note
        rows = self._conn.execute(
            "SELECT * FROM notes WHERE id LIKE ? ORDER BY id LIMIT 2", (f"{token}%",)
        ).fetchall()
        if len(rows) == 1:
            return self._row_to_note(rows[0])
        rows = self._conn.execute(
            "SELECT * FROM notes WHERE lower(title) LIKE ? ORDER BY updated_at DESC LIMIT 2",
            (f"%{token.lower()}%",),
        ).fetchall()
        if len(rows) == 1:
            return self._row_to_note(rows[0])
        return None

    def list_notes(
        self,
        *,
        category: str | None = None,
        tag: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Note]:
        sql = "SELECT * FROM notes"
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if tag:
            clauses.append("lower(tags_json) LIKE ?")
            params.append(f'%"{tag.lower()}"%')
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [self._row_to_note(r) for r in self._conn.execute(sql, params).fetchall()]

    def all_notes(self) -> list[Note]:
        return [self._row_to_note(r) for r in self._conn.execute("SELECT * FROM notes")]

    def delete_note(self, note_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM note_chunks WHERE note_id=?", (note_id,))
            self._conn.execute(
                "DELETE FROM note_links WHERE source_note_id=? OR target_note_id=?",
                (note_id, note_id),
            )
            self._conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
            self._conn.commit()

    def count_notes(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM notes").fetchone()
        return int(row["c"]) if row else 0

    def note_hashes(self) -> dict[str, tuple[str, str]]:
        """Map ``file_path -> (note_id, content_hash)`` for vault reconciliation."""

        rows = self._conn.execute("SELECT file_path, id, content_hash FROM notes").fetchall()
        return {r["file_path"]: (r["id"], r["content_hash"] or "") for r in rows}

    def next_note_id(self, day: str | None = None) -> str:
        """Generate ``rph-YYYYMMDD-NNN`` (ARCHITECT.md §5)."""

        day = day or now().strftime("%Y%m%d")
        prefix = f"rph-{day}-"
        row = self._conn.execute(
            "SELECT id FROM notes WHERE id LIKE ? ORDER BY id DESC LIMIT 1", (f"{prefix}%",)
        ).fetchone()
        if row:
            try:
                seq = int(str(row["id"]).rsplit("-", 1)[1]) + 1
            except (ValueError, IndexError):
                seq = 1
        else:
            seq = 1
        return f"{prefix}{seq:03d}"

    # ------------------------------------------------------------------
    # keyword search (FTS5)
    # ------------------------------------------------------------------
    def search_keyword(self, query: str, limit: int = 20) -> list[SearchHit]:
        match = _fts_query(query)
        if not match:
            return []
        sql = """
            SELECT n.id, n.title, n.tags_json,
                   snippet(notes_fts, 2, '', '', '\u2026', 14) AS snip,
                   bm25(notes_fts, 0.0, 5.0, 1.0, 2.0, 3.0) AS rank
            FROM notes_fts
            JOIN notes n ON n.rowid = notes_fts.rowid
            WHERE notes_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        try:
            rows = self._conn.execute(sql, (match, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        hits: list[SearchHit] = []
        for row in rows:
            rank = float(row["rank"] or 0.0)
            hits.append(
                SearchHit(
                    note_id=row["id"],
                    title=row["title"] or "",
                    snippet=(row["snip"] or "").strip(),
                    score=1.0 / (1.0 + abs(rank)),
                    match="keyword",
                )
            )
        return hits

    # ------------------------------------------------------------------
    # chunks + vectors
    # ------------------------------------------------------------------
    def replace_chunks(self, note_id: str, texts: Sequence[str]) -> list[int]:
        with self._lock:
            existing = [
                r["chunk_id"]
                for r in self._conn.execute(
                    "SELECT chunk_id FROM note_chunks WHERE note_id=?", (note_id,)
                ).fetchall()
            ]
            for chunk_id in existing:
                self._conn.execute(
                    "DELETE FROM note_chunks WHERE chunk_id=?", (chunk_id,)
                )
                if self.vec_enabled:
                    try:
                        self._conn.execute(
                            "DELETE FROM note_embeddings WHERE chunk_id=?", (chunk_id,)
                        )
                    except sqlite3.Error:
                        pass
            ids: list[int] = []
            for index, text in enumerate(texts):
                cur = self._conn.execute(
                    "INSERT INTO note_chunks(note_id, chunk_index, text) VALUES (?,?,?)",
                    (note_id, index, text),
                )
                ids.append(int(cur.lastrowid))
            self._conn.commit()
            return ids

    def pending_embeddings(self, limit: int = 200) -> list[Chunk]:
        rows = self._conn.execute(
            "SELECT chunk_id, note_id, chunk_index, text FROM note_chunks "
            "WHERE vector IS NULL ORDER BY chunk_id LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            Chunk(
                chunk_id=r["chunk_id"],
                note_id=r["note_id"],
                chunk_index=r["chunk_index"],
                text=r["text"],
            )
            for r in rows
        ]

    def set_embedding(self, chunk_id: int, vector: Sequence[float]) -> None:
        blob = _pack(vector)
        with self._lock:
            self._conn.execute(
                "UPDATE note_chunks SET vector=?, dim=? WHERE chunk_id=?",
                (blob, len(vector), chunk_id),
            )
            if self.vec_enabled:
                try:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO note_embeddings(chunk_id, embedding) VALUES (?,?)",
                        (chunk_id, blob),
                    )
                except sqlite3.Error:
                    self.vec_enabled = False
            self._conn.commit()

    def chunk_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM note_chunks").fetchone()
        return int(row["c"]) if row else 0

    def count_chunks_for(self, note_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM note_chunks WHERE note_id=?", (note_id,)
        ).fetchone()
        return int(row["c"]) if row else 0

    def embedded_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM note_chunks WHERE vector IS NOT NULL"
        ).fetchone()
        return int(row["c"]) if row else 0

    def search_vectors(
        self, vector: Sequence[float], limit: int = 10, *, exclude: str | None = None
    ) -> list[tuple[str, int, float]]:
        """Return ``(note_id, chunk_id, similarity)`` best-first."""

        if not vector:
            return []
        if self.vec_enabled:
            try:
                rows = self._conn.execute(
                    "SELECT chunk_id, distance FROM note_embeddings "
                    "WHERE embedding MATCH ? AND k = ?",
                    (_pack(vector), limit * 3),
                ).fetchall()
                scored: list[tuple[str, int, float]] = []
                for row in rows:
                    chunk = self._conn.execute(
                        "SELECT note_id FROM note_chunks WHERE chunk_id=?",
                        (row["chunk_id"],),
                    ).fetchone()
                    if not chunk:
                        continue
                    if exclude and chunk["note_id"] == exclude:
                        continue
                    sim = 1.0 - float(row["distance"])
                    scored.append((chunk["note_id"], int(row["chunk_id"]), sim))
                    if len(scored) >= limit:
                        break
                if scored:
                    return scored
            except sqlite3.Error:
                self.vec_enabled = False

        rows = self._conn.execute(
            "SELECT chunk_id, note_id, vector FROM note_chunks WHERE vector IS NOT NULL"
        ).fetchall()
        candidates = [
            (r["note_id"], int(r["chunk_id"]), _unpack(r["vector"]))
            for r in rows
            if not (exclude and r["note_id"] == exclude)
        ]
        if not candidates:
            return []
        return self._numpy_scan(vector, candidates, limit)

    @staticmethod
    def _numpy_scan(
        query: Sequence[float],
        candidates: list[tuple[str, int, list[float]]],
        limit: int,
    ) -> list[tuple[str, int, float]]:
        try:
            import numpy as np
        except ImportError:  # pragma: no cover - numpy is a declared dependency
            from raphael.utils import cosine_similarity

            scored = [
                (note_id, chunk_id, cosine_similarity(list(query), vec))
                for note_id, chunk_id, vec in candidates
            ]
            scored.sort(key=lambda item: item[2], reverse=True)
            return scored[:limit]

        q = np.asarray(query, dtype=np.float32)
        q_norm = float(np.linalg.norm(q)) or 1.0
        scored: list[tuple[str, int, float]] = []
        for note_id, chunk_id, vec in candidates:
            if len(vec) != len(query):
                continue
            v = np.asarray(vec, dtype=np.float32)
            denom = (float(np.linalg.norm(v)) or 1.0) * q_norm
            scored.append((note_id, chunk_id, float(np.dot(q, v) / denom)))
        scored.sort(key=lambda item: item[2], reverse=True)
        return scored[:limit]

    # ------------------------------------------------------------------
    # knowledge graph
    # ------------------------------------------------------------------
    def upsert_link(self, link: Link) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO note_links (source_note_id, target_note_id, relation_type,
                                        similarity_score, reasoning)
                VALUES (?,?,?,?,?)
                ON CONFLICT(source_note_id, target_note_id) DO UPDATE SET
                    relation_type=excluded.relation_type,
                    similarity_score=excluded.similarity_score,
                    reasoning=excluded.reasoning
                """,
                (
                    link.source_note_id,
                    link.target_note_id,
                    link.relation_type,
                    float(link.similarity_score),
                    link.reasoning,
                ),
            )
            self._conn.commit()

    def links_for(self, note_id: str, limit: int = 10) -> list[Link]:
        rows = self._conn.execute(
            """
            SELECT * FROM note_links
            WHERE source_note_id=? OR target_note_id=?
            ORDER BY similarity_score DESC LIMIT ?
            """,
            (note_id, note_id, limit),
        ).fetchall()
        return [
            Link(
                source_note_id=r["source_note_id"],
                target_note_id=r["target_note_id"],
                relation_type=r["relation_type"] or "similar",
                similarity_score=float(r["similarity_score"] or 0.0),
                reasoning=r["reasoning"] or "",
            )
            for r in rows
        ]

    def recent_links(self, limit: int = 5) -> list[Link]:
        rows = self._conn.execute(
            "SELECT * FROM note_links ORDER BY similarity_score DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            Link(
                source_note_id=r["source_note_id"],
                target_note_id=r["target_note_id"],
                relation_type=r["relation_type"] or "similar",
                similarity_score=float(r["similarity_score"] or 0.0),
                reasoning=r["reasoning"] or "",
            )
            for r in rows
        ]

    def delete_links_for(self, note_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM note_links WHERE source_note_id=? OR target_note_id=?",
                (note_id, note_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # background jobs
    # ------------------------------------------------------------------
    def enqueue(self, job_type: str, payload: dict[str, Any] | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO background_jobs(job_type, payload_json, status) "
                "VALUES (?,?,'PENDING')",
                (job_type, json.dumps(payload or {}, ensure_ascii=False)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def claim_job(self) -> Job | None:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT * FROM background_jobs WHERE status='PENDING' "
                    "ORDER BY job_id LIMIT 1"
                ).fetchone()
                if not row:
                    self._conn.commit()
                    return None
                self._conn.execute(
                    "UPDATE background_jobs SET status='RUNNING', started_at=?, "
                    "attempts=attempts+1 WHERE job_id=?",
                    (now_iso(), row["job_id"]),
                )
                self._conn.commit()
            except sqlite3.Error:
                self._conn.rollback()
                return None
        return Job(
            job_id=row["job_id"],
            job_type=row["job_type"],
            payload=json.loads(row["payload_json"] or "{}"),
            status="RUNNING",
        )

    def finish_job(self, job_id: int, *, ok: bool, error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE background_jobs SET status=?, finished_at=?, error=? WHERE job_id=?",
                ("DONE" if ok else "FAILED", now_iso(), error, job_id),
            )
            self._conn.commit()

    def retry_job(self, job_id: int, error: str) -> None:
        """Requeue a job unless it has already failed three times."""

        with self._lock:
            row = self._conn.execute(
                "SELECT attempts FROM background_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            attempts = int(row["attempts"]) if row else 0
            status = "PENDING" if attempts < 3 else "FAILED"
            self._conn.execute(
                "UPDATE background_jobs SET status=?, error=? WHERE job_id=?",
                (status, error, job_id),
            )
            self._conn.commit()

    def job_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS c FROM background_jobs GROUP BY status"
        ).fetchall()
        return {r["status"]: int(r["c"]) for r in rows}

    def recent_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT job_id, job_type, status, created_at, error FROM background_jobs "
            "ORDER BY job_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # config / evolved rules
    # ------------------------------------------------------------------
    def set_rule(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO config_kv(key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, value, now_iso()),
            )
            self._conn.commit()

    def get_rule(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM config_kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def list_rules(self) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            "SELECT key, value FROM config_kv ORDER BY updated_at DESC"
        ).fetchall()
        return [(r["key"], r["value"]) for r in rows]

    def delete_rule(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM config_kv WHERE key=?", (key,))
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # stats
    # ------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        def _scalar(sql: str) -> int:
            row = self._conn.execute(sql).fetchone()
            return int(row[0]) if row else 0

        return {
            "notes": _scalar("SELECT COUNT(*) FROM notes"),
            "chunks": _scalar("SELECT COUNT(*) FROM note_chunks"),
            "embedded": _scalar("SELECT COUNT(*) FROM note_chunks WHERE vector IS NOT NULL"),
            "links": _scalar("SELECT COUNT(*) FROM note_links"),
            "rules": _scalar("SELECT COUNT(*) FROM config_kv"),
            "jobs": self.job_counts(),
            "vec_enabled": self.vec_enabled,
            "db_path": str(self.path),
        }

    def iter_note_ids(self) -> Iterable[str]:
        for row in self._conn.execute("SELECT id FROM notes").fetchall():
            yield row["id"]
