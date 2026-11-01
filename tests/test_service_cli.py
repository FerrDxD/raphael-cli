"""End-to-end service flows and the CLI surface."""

from __future__ import annotations

import io
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from raphael import cli
from raphael.models import Note
from raphael.utils import content_hash, now


def test_capture_creates_markdown_and_index(service, settings) -> None:
    result = service.capture("Ide mekanik baru: sistem reputasi faksi Tokyo.", appraise=False)

    note = result.note
    assert note.id.startswith("rph-")
    assert note.appraisal_status == "pending"
    assert result.elapsed_ms >= 0

    path = settings.vault_dir / note.file_path
    assert path.exists()
    assert "sistem reputasi" in path.read_text(encoding="utf-8")

    stored = service.db.get_note(note.id)
    assert stored is not None and stored.content_hash == content_hash(note.body)

    # Jobs are queued for the daemon rather than blocking capture.
    counts = service.db.job_counts()
    assert counts.get("PENDING", 0) >= 2


def test_capture_rejects_empty_text(service) -> None:
    with pytest.raises(ValueError):
        service.capture("   \n  ")


def test_capture_with_project_tags(service) -> None:
    result = service.capture("Rencana riset", project="Riset Utama", appraise=False)
    assert result.note.category == "projects"
    assert "project:riset-utama" in result.note.tags


def test_appraise_now_persists_metadata(service, monkeypatch) -> None:
    monkeypatch.setattr(service.llm, "local_available", lambda refresh=False: True)
    monkeypatch.setattr(service.llm, "engine", "local", raising=False)
    monkeypatch.setattr(
        service.llm,
        "json_chat",
        lambda messages, schema=None, **kw: {
            "title": "Aturan Shibuya",
            "summary": "Batasan waktu.",
            "tags": ["worldbuilding", "tokyo"],
            "entities": ["Shibuya"],
            "category": "projects",
        },
    )
    result = service.capture("Karakter dilarang keluar malam.", appraise=True)

    assert result.note.title == "Aturan Shibuya"
    assert result.note.appraisal_status == "completed"
    assert "worldbuilding" in result.note.tags

    reread = service.vault.read_by_id(result.note.id)
    assert reread is not None and reread.summary == "Batasan waktu."


def test_search_and_ask_flow(service) -> None:
    service.capture("Aturan A: karakter tidak keluar setelah jam 22.00.", appraise=False)
    service.capture("Aturan B: latihan pedang setiap pagi.", appraise=False)

    hits = service.search("karakter")
    assert hits and hits[0].note_id

    result = service.ask("apa aturannya?")
    assert result.notes
    assert result.answer


def test_ask_with_no_notes(service) -> None:
    result = service.ask("apa saja?")
    assert "Tidak ada catatan" in result.answer
    assert result.notes == []


def test_synth_merges_and_reports_sources(service) -> None:
    first = service.capture("Isi bagian satu.", appraise=False).note
    second = service.capture("Isi bagian dua.", appraise=False).note

    result = service.synth([first.id, second.id])
    assert set(result.sources) == {first.id, second.id}
    assert "Isi bagian satu." in result.markdown
    assert "Isi bagian dua." in result.markdown


def test_synth_missing_note_raises(service) -> None:
    with pytest.raises(LookupError):
        service.synth(["tidak-ada"])


def test_split_by_note_and_free_text(service) -> None:
    note = service.capture(
        "Target\n- Menulis bab satu\n- Merancang mekanik\n- Uji coba", appraise=False
    ).note
    by_note = service.split(note.id)
    assert by_note.tasks

    by_text = service.split("Rilis fitur baru minggu depan dengan dokumentasi lengkap")
    assert by_text.tasks


def test_resolve_notes_reports_missing(service) -> None:
    note = service.capture("catatan", appraise=False).note
    found, missing = service.resolve_notes([note.id, "hantu"])
    assert [n.id for n in found] == [note.id]
    assert missing == ["hantu"]


def test_reindex_reconciles_vault_files(service, settings) -> None:
    note = service.capture("Isi awal catatan.", appraise=False).note
    path = settings.vault_dir / note.file_path

    # Simulate an external edit in an editor (file changes, DB does not).
    path.write_text(
        path.read_text(encoding="utf-8").replace("Isi awal catatan.", "Isi yang diedit."),
        encoding="utf-8",
    )

    report = service.reindex(embed=False)
    assert report["indexed"] >= 1
    refreshed = service.db.get_note(note.id)
    assert "Isi yang diedit." in refreshed.body


def test_reindex_removes_deleted_files(service, settings) -> None:
    note = service.capture("akan dihapus", appraise=False).note
    (settings.vault_dir / note.file_path).unlink()

    report = service.reindex(embed=False)
    assert report["removed"] == 1
    assert service.db.get_note(note.id) is None


def test_process_job_appraise_then_embed(service, monkeypatch) -> None:
    monkeypatch.setattr(service.llm, "local_available", lambda refresh=False: True)
    monkeypatch.setattr(service.llm, "embed", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])

    note = service.capture("Catatan untuk antrean.", appraise=False).note
    service.process_job("APPRAISE", {"note_id": note.id})
    service.process_job("EMBED", {"note_id": note.id})

    refreshed = service.db.get_note(note.id)
    assert refreshed.appraisal_status in {"heuristic", "completed"}
    assert service.db.embedded_count() >= 1


def test_evolve_rules_are_injected(service, monkeypatch) -> None:
    service.evolve("Selalu jawab dalam Bahasa Indonesia.")

    captured: dict[str, str] = {}

    def fake_json_chat(messages, schema=None, **kwargs):
        captured["system"] = messages[0]["content"]
        return {"title": "T", "summary": "S", "tags": [], "entities": [], "category": "inbox"}

    monkeypatch.setattr(service.llm, "local_available", lambda refresh=False: True)
    monkeypatch.setattr(service.llm, "json_chat", fake_json_chat)

    service.appraiser.appraise("teks")
    assert "Bahasa Indonesia" in captured["system"]


def test_ingest_extracts_text_files(service, tmp_path: Path) -> None:
    doc = tmp_path / "catatan.md"
    doc.write_text("# Riset\n\nIsi dokumen riset lokal.", encoding="utf-8")
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("nama,nilai\nA,1\nB,2\n", encoding="utf-8")

    results = service.ingest([doc, csv_file])
    assert len(results) == 2
    assert all(r.note.source for r in results)
    assert any("Isi dokumen riset lokal." in r.note.body for r in results)
    assert any("A | 1" in r.note.body for r in results)


def test_ingest_strips_bom_and_heading(service, tmp_path: Path) -> None:
    doc = tmp_path / "protokol.md"
    doc.write_bytes("\ufeff# Protokol Faksi\n\nIsi protokol lokal.".encode("utf-8"))

    result = service.ingest([doc], appraise=False)[0]

    assert result.note.title == "Protokol Faksi"
    assert "\ufeff" not in result.note.title


def test_appraised_capture_still_indexes_chunks(service) -> None:
    # Appraisal rewrites the note; its chunks must be rebuilt from the new copy,
    # otherwise the note is invisible to search.
    note = service.capture("Catatan appraisal tetap harus bisa dicari.", appraise=True).note
    assert service.db.count_chunks_for(note.id) >= 1


def test_status_reports_projects(service) -> None:
    service.capture("satu", project="Riset Utama", appraise=False)
    data = service.status()
    assert data["stats"]["notes"] == 1
    assert data["projects"]["Riset Utama"] == 1
    assert data["vault"]


def test_daemon_running_false_without_server(service) -> None:
    assert service.daemon_running(timeout=0.2) is False


# ----------------------------------------------------------------------
# CLI surface
# ----------------------------------------------------------------------
@pytest.fixture()
def runner(monkeypatch, service):
    """Drive the Typer app against the fixture service.

    The real ``_svc`` is a closing context manager (a CLI invocation owns its
    connection), but a test reuses one service across several invocations, so
    the stub must not close it.
    """

    @contextmanager
    def _svc(offline: bool = False):
        yield service

    monkeypatch.setattr(cli, "_svc", _svc)
    return CliRunner()


def test_cli_add_and_list(runner) -> None:
    result = runner.invoke(cli.app, ["add", "Ide dari CLI", "--now"])
    assert result.exit_code == 0, result.output
    assert "Catatan disimpan" in result.output

    listed = runner.invoke(cli.app, ["list"])
    assert listed.exit_code == 0
    assert "Ide dari CLI" in listed.output


def test_cli_search_and_show(runner, service) -> None:
    note = service.capture("Sistem reputasi faksi Tokyo.", appraise=False).note

    found = runner.invoke(cli.app, ["search", "reputasi"])
    assert found.exit_code == 0
    assert note.id in found.output

    shown = runner.invoke(cli.app, ["show", note.id])
    assert shown.exit_code == 0
    assert "Sistem reputasi" in shown.output


def test_cli_ask(runner, service) -> None:
    service.capture("Aturan A berlaku malam hari.", appraise=False)
    result = runner.invoke(cli.app, ["ask", "apa aturannya"])
    assert result.exit_code == 0
    assert "Report" in result.output


def test_cli_synth_writes_file(runner, service, tmp_path: Path) -> None:
    note = service.capture("Isi untuk sintesis.", appraise=False).note
    out = tmp_path / "hasil.md"
    result = runner.invoke(cli.app, ["synth", note.id, "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "Isi untuk sintesis." in out.read_text(encoding="utf-8")


def test_cli_split(runner) -> None:
    result = runner.invoke(cli.app, ["split", "Menulis bab satu dan merancang mekanik baru"])
    assert result.exit_code == 0
    assert "tugas" in result.output.lower()


def test_cli_evolve_list(runner, service) -> None:
    service.evolve("Pakai nada ringkas.")
    result = runner.invoke(cli.app, ["evolve", "--list"])
    assert result.exit_code == 0
    assert "Pakai nada ringkas." in result.output


def test_cli_rm_requires_confirmation(runner, service) -> None:
    note = service.capture("hapus aku", appraise=False).note
    result = runner.invoke(cli.app, ["rm", note.id], input="n\n")
    assert result.exit_code == 0
    assert service.db.get_note(note.id) is not None

    forced = runner.invoke(cli.app, ["rm", note.id, "--yes"])
    assert forced.exit_code == 0
    assert service.db.get_note(note.id) is None


def test_cli_missing_note_exits_nonzero(runner) -> None:
    result = runner.invoke(cli.app, ["show", "tidak-ada"])
    assert result.exit_code == 1
    assert "tidak ditemukan" in result.output


def test_cli_ingest_dash_reads_stdin(monkeypatch, runner, service) -> None:
    # CliRunner owns sys.stdin, so the pipe content goes through `input=`.
    monkeypatch.setattr(cli, "_STDIN_CACHE", None)

    result = runner.invoke(
        cli.app, ["ingest", "-"], input="# Dokumen Pipa\n\nIsi dari stdin."
    )
    assert result.exit_code == 0, result.output
    assert "Dokumen Pipa" in result.output

    notes = service.db.list_notes(limit=10)
    assert any(n.source == "stdin" for n in notes)


def test_cli_reindex_command(runner, service) -> None:
    service.capture("Isi untuk reindex.", appraise=False)
    result = runner.invoke(cli.app, ["reindex", "--no-embed"])
    assert result.exit_code == 0, result.output
    assert "diindeks" in result.output


def test_cli_status_and_config(runner) -> None:
    status = runner.invoke(cli.app, ["status"])
    assert status.exit_code == 0
    assert "RAPHAEL" in status.output

    config = runner.invoke(cli.app, ["config"])
    assert config.exit_code == 0
    assert "vault" in config.output


def test_chant_routes_note_vs_question(runner, service) -> None:
    note_rc = cli.chant(["Ide mekanik baru tentang reputasi faksi"])
    assert note_rc == 0
    assert service.db.count_notes() == 1

    ask_rc = cli.chant(["apa aturannya?"])
    assert ask_rc == 0


def test_cli_pipes_stdin_into_capture(monkeypatch, runner, service) -> None:
    # main() peeks at stdin, then chant() reads it again; the cache must keep the
    # text alive so a pipe is not silently dropped.
    monkeypatch.setattr(cli, "_STDIN_CACHE", None)
    monkeypatch.setattr("sys.stdin", io.StringIO("catatan dari pipa"))

    assert cli.main([]) == 0

    bodies = [note.body for note in service.db.list_notes(limit=10)]
    assert any("catatan dari pipa" in body for body in bodies)


def test_chant_force_flags(runner, service) -> None:
    cli.chant(["--ask", "Ide yang mirip pertanyaan"])
    assert service.db.count_notes() == 0

    cli.chant(["--note", "apa ini?"])
    assert service.db.count_notes() == 1
