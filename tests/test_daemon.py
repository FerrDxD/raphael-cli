"""Daemon contracts: HTTP surface, worker drain, watcher reconciliation."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from raphael.daemon.server import create_app
from raphael.daemon.worker import Worker


@pytest.fixture()
def client(service):
    app = create_app(service)
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_status(client) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    status = client.get("/status")
    assert status.status_code == 200
    assert "stats" in status.json()


def test_capture_and_search_over_http(client, service) -> None:
    response = client.post(
        "/capture",
        json={"text": "Catatan lewat daemon tentang Tokyo.", "appraise": False},
    )
    assert response.status_code == 200
    note = response.json()["note"]
    assert note["id"].startswith("rph-")
    assert service.db.get_note(note["id"]) is not None

    found = client.post("/search", json={"query": "Tokyo"})
    assert found.status_code == 200
    assert found.json()["hits"]


def test_ask_over_http(client, service) -> None:
    service.capture("Aturan malam hari berlaku.", appraise=False)
    response = client.post("/ask", json={"question": "apa aturannya"})
    assert response.status_code == 200
    assert response.json()["answer"]


def test_drain_processes_queue(client, service) -> None:
    client.post("/capture", json={"text": "Pekerjaan latar belakang.", "appraise": False})
    assert service.db.job_counts().get("PENDING", 0) > 0

    drained = client.post("/jobs/drain")
    assert drained.status_code == 200
    assert drained.json()["processed"] > 0


def test_reindex_endpoint(client, service, settings) -> None:
    note = service.capture("Isi awal.", appraise=False).note
    path = settings.vault_dir / note.file_path
    path.write_text(path.read_text(encoding="utf-8") + "\nTambahan manual.\n", encoding="utf-8")

    response = client.post("/reindex")
    assert response.status_code == 200
    assert response.json()["indexed"] >= 1


def test_worker_drain_synchronously(service) -> None:
    service.capture("Antrean sinkron.", appraise=False)
    worker = Worker(service, interval=0.05, idle_every=0)
    processed = worker.drain(limit=10)
    assert processed >= 2
    assert service.db.job_counts().get("PENDING", 0) == 0


def test_watcher_reconciles_external_edit(service, settings) -> None:
    from raphael.daemon.watcher import VaultWatcher

    note = service.capture("Isi sebelum edit.", appraise=False).note
    path = settings.vault_dir / note.file_path

    watcher = VaultWatcher(service, interval=0.05)
    watcher.start()
    try:
        time.sleep(0.4)
        path.write_text(
            path.read_text(encoding="utf-8").replace("Isi sebelum edit.", "Isi sesudah edit."),
            encoding="utf-8",
        )
        deadline = time.time() + 12
        while time.time() < deadline:
            fresh = service.db.get_note(note.id)
            if fresh and "Isi sesudah edit." in fresh.body:
                break
            time.sleep(0.2)
        else:
            pytest.fail("watcher did not reconcile the external edit")
    finally:
        watcher.stop()


def test_watcher_detects_new_file(service, settings) -> None:
    from raphael.daemon.watcher import VaultWatcher

    watcher = VaultWatcher(service, interval=0.05)
    watcher.start()
    try:
        time.sleep(0.4)
        new_file = settings.vault_dir / "inbox" / "rph-manual-001-catatan-luar.md"
        new_file.write_text(
            "---\n"
            "id: rph-manual-001\n"
            "title: Catatan Luar\n"
            "created_at: '2026-10-17T07:15:00+07:00'\n"
            "updated_at: '2026-10-17T07:15:00+07:00'\n"
            "category: inbox\n"
            "tags: [manual]\n"
            "entities: []\n"
            "appraisal_status: pending\n"
            "---\n\n"
            "Dibuat langsung oleh editor.\n",
            encoding="utf-8",
        )
        deadline = time.time() + 12
        while time.time() < deadline:
            if service.db.get_note("rph-manual-001") is not None:
                break
            time.sleep(0.2)
        else:
            pytest.fail("watcher did not index the new file")
    finally:
        watcher.stop()
