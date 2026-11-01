"""Storage contracts: Markdown round-trip, FTS5, ids, jobs, links."""

from __future__ import annotations

from pathlib import Path

from raphael.models import Link, Note
from raphael.storage.db import Database, _fts_query
from raphael.storage.vault import SUMMARY_HEADING, Vault
from raphael.utils import content_hash, now


def _note(note_id: str = "rph-20261017-001", **kwargs) -> Note:
    base = dict(
        id=note_id,
        title="Aturan Pergerakan Karakter di Distrik Shibuya",
        body="Karakter tidak boleh keluar setelah jam 22.00 di area publik.",
        category="projects",
        tags=["worldbuilding", "tokyo-setting"],
        entities=["Shibuya"],
        summary="Menetapkan batasan waktu interaksi karakter.",
        created_at=now(),
        updated_at=now(),
    )
    base.update(kwargs)
    return Note(**base)


def test_vault_round_trip(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    note = _note()
    path = vault.write(note)

    assert path.exists()
    assert path.suffix == ".md"
    assert note.file_path == "projects/" + path.name

    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("---\n")
    assert "appraisal_status:" in raw
    assert SUMMARY_HEADING in raw

    parsed = vault.parse(path)
    assert parsed is not None
    assert parsed.id == note.id
    assert parsed.title == note.title
    assert parsed.tags == note.tags
    assert parsed.entities == note.entities
    assert parsed.summary == note.summary
    assert parsed.content_hash == content_hash(note.body)
    assert SUMMARY_HEADING not in parsed.body


def test_vault_moves_file_when_category_changes(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    note = _note()
    first = vault.write(note)
    note.category = "references"
    second = vault.write(note)

    assert second.exists()
    assert not first.exists()
    assert "references" in note.file_path


def test_vault_handles_broken_frontmatter(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    path = tmp_path / "vault" / "inbox" / "manual.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\ntitle: [unclosed\n---\n\nTeks manual.", encoding="utf-8")

    parsed = vault.parse(path)
    assert parsed is not None
    assert "Teks manual." in parsed.body


def test_fts_query_quotes_unsafe_input() -> None:
    assert _fts_query("hello world") == '"hello" OR "world"'
    assert _fts_query("NEAR(a b)") == '"NEAR" OR "a" OR "b"'
    assert _fts_query("!!!") == ""


def test_fts_query_expands_indonesian_enclitics() -> None:
    # "aturannya" must still find the token "aturan": the root is searched as a
    # prefix, so both the inflected and the bare form match.
    query = _fts_query("aturannya")
    assert '"aturannya"' in query
    assert '"aturan"*' in query
    # Short words are left alone so "buku" does not degrade into "bu".
    assert _fts_query("buku") == '"buku"'


def test_keyword_search_matches_inflected_form(settings) -> None:
    db = Database(settings.db_path)
    try:
        db.upsert_note(_note(body="Aturan A: karakter tidak keluar setelah jam 22.00."))
        hits = db.search_keyword("aturannya?")
        assert [h.note_id for h in hits] == ["rph-20261017-001"]
    finally:
        db.close()


def test_database_crud_and_keyword_search(settings) -> None:
    db = Database(settings.db_path)
    try:
        note = _note()
        db.upsert_note(note)

        assert db.count_notes() == 1
        stored = db.get_note(note.id)
        assert stored is not None and stored.title == note.title

        hits = db.search_keyword("Shibuya")
        assert [h.note_id for h in hits] == [note.id]
        assert hits[0].match == "keyword"

        # Operator characters must not raise.
        assert db.search_keyword("NEAR(") == []

        db.delete_note(note.id)
        assert db.count_notes() == 0
        assert db.search_keyword("Shibuya") == []
    finally:
        db.close()


def test_note_id_sequence(settings) -> None:
    db = Database(settings.db_path)
    try:
        first = db.next_note_id("20261017")
        assert first == "rph-20261017-001"
        db.upsert_note(_note(first))
        assert db.next_note_id("20261017") == "rph-20261017-002"
    finally:
        db.close()


def test_resolve_note_by_prefix_and_title(settings) -> None:
    db = Database(settings.db_path)
    try:
        db.upsert_note(_note("rph-20261017-001", title="Riset Tokyo"))
        db.upsert_note(_note("rph-20261017-002", title="Riset Osaka"))
        assert db.resolve_note("rph-20261017-001").id == "rph-20261017-001"
        assert db.resolve_note("Tokyo").id == "rph-20261017-001"
        assert db.resolve_note("tidak-ada") is None
    finally:
        db.close()


def test_jobs_lifecycle(settings) -> None:
    db = Database(settings.db_path)
    try:
        job_id = db.enqueue("APPRAISE", {"note_id": "x"})
        job = db.claim_job()
        assert job is not None and job.job_id == job_id and job.status == "RUNNING"
        assert db.claim_job() is None

        db.finish_job(job_id, ok=True)
        assert db.job_counts() == {"DONE": 1}

        retry_id = db.enqueue("EMBED", {})
        db.claim_job()
        db.retry_job(retry_id, "boom")
        assert db.job_counts().get("PENDING") == 1
    finally:
        db.close()


def test_links_and_rules(settings) -> None:
    db = Database(settings.db_path)
    try:
        db.upsert_note(_note("a", title="A"))
        db.upsert_note(_note("b", title="B"))
        db.upsert_link(
            Link(source_note_id="a", target_note_id="b", relation_type="contradicts",
                 similarity_score=0.9, reasoning="jadwal bentrok")
        )
        links = db.links_for("a")
        assert len(links) == 1 and links[0].relation_type == "contradicts"
        assert db.links_for("b")[0].source_note_id == "a"

        db.set_rule("rule:1", "Selalu pakai Bahasa Indonesia")
        assert db.get_rule("rule:1") == "Selalu pakai Bahasa Indonesia"
        assert db.delete_rule("rule:1") is True
        assert db.get_rule("rule:1") is None
    finally:
        db.close()
