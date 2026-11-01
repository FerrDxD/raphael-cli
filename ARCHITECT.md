# System Architecture Document: Project RAPHAEL (CLI Edition)
**Responsive Autonomous Personal Hub for Analytical & Executive Logic**

* **Versi Dokumen:** 1.0.0
* **Status:** Approved Blueprint
* **Pola Arsitektur:** Local-First, Event-Driven Daemon + Thin CLI Client
* **Target Lingkungan:** Single-User Local Machine (Linux / macOS / Windows WSL2)

---

## 1. Analisis & Keputusan Pemilihan Bahasa (Tech Stack Justification)

Bagi pengembang yang baru masuk ke ranah *AI Engineering*, memilih bahasa pemrograman adalah keputusan paling krusial. Berikut perbandingan tiga kandidat utama untuk *CLI AI Second Brain*:

| Kriteria | Python (3.11+) | Go (Golang) | Rust | TypeScript (Node/Bun) |
| :--- | :--- | :--- | :--- | :--- |
| **Ekosistem AI & LLM** | **Sempurna (Standar Industri)** | Cukup | Sedang berkembang | Sangat Baik |
| **Ekstraksi Dokumen (PDF/XLSX/DOCX)** | **Sangat Mudah & Lengkap** | Terbatas | Sulit / Verbose | Cukup |
| **Kurva Pembelajaran (Pemula AI)** | **Sangat Ramah** | Menengah | Sangat Curam | Ramah |
| **Kecepatan Startup CLI** | Lambat jika *cold-start* (diatasi dengan Daemon) | Sangat Cepat | Instan | Cepat (dengan Bun) |

### Keputusan Final: **Python 3.12+ (Dikelola dengan `uv`)**
1. **Mengapa Python?** Seluruh logika *AI Engineering* (komunikasi dengan Ollama, struktur output JSON memakai *Pydantic*, pembacaan file PDF/Excel/Docx, dan *vector search*) dapat ditulis hanya dalam beberapa baris kode yang bersih dan mudah dipahami.
2. **Bagaimana Mengatasi Kelemahan Kecepatan Python di CLI?** 
   Secara tradisional, CLI Python terasa agak lambat (jeda 0.5–1 detik saat dipanggil) karena harus memuat pustaka AI yang berat setiap kali perintah diketik. Kita memecahkan ini dengan pola **Client-Daemon Architecture**:
   * **`rph` (CLI Client):** Skrip sangat ringan tanpa *library* berat. Tugasnya hanya mengirim teks perintah ke *background daemon* (waktu muat < 30ms).
   * **`raphaeld` (Background Daemon):** Proses Python yang sudah berjalan di latar belakang dan memegang koneksi ke database serta AI lokal.

---

## 2. Daftar Teknologi Utama (The Raphael Stack)

Untuk menjaga agar sistem 100% bisa berjalan *offline* dan tidak rumit:

* **Package & Environment Manager:** `uv` (menggantikan `pip`/`venv`, sangat cepat dan bisa mengemas CLI menjadi perintah global di terminal).
* **CLI & Terminal Formatting:** `Typer` (untuk membuat struktur perintah CLI) + `Rich` (untuk warna terminal `[Report]` / `[Notice]`, tabel, dan *loading spinner*).
* **Interactive TUI (Terminal UI):** `Textual` (framework Python modern untuk membuat tampilan *dashboard* interaktif di dalam terminal tanpa butuh browser).
* **Local Storage & Memory (Dual-Layer):**
  * **File System:** File Markdown (`.md`) dengan *YAML Frontmatter* (disimpan di `~/.raphael/vault/`).
  * **Database:** **SQLite 3** (1 file tunggal `raphael.db`, tanpa perlu *install* server database).
    * **Pencarian Kata Kunci:** Ekstensi bawaan **SQLite FTS5** (*Full-Text Search*).
    * **Pencarian Semantik (Vektor):** Ekstensi **`sqlite-vec`** (menyimpan *vector embeddings* langsung di dalam file SQLite yang sama).
* **Local AI Engine (Offline Brain):** **Ollama**
  * **Model Utama (Reasoning & Appraisal):** `qwen2.5:7b` atau `llama3.1:8b` (sangat pintar untuk ukuran laptop/PC standar, mendukung Bahasa Indonesia dan Inggris dengan baik).
  * **Model Vektor (Embeddings):** `nomic-embed-text` atau `bge-m3` (sangat ringan, khusus untuk mengubah teks catatan menjadi koordinat angka/vektor).
* **Structured AI Output:** `Pydantic` + `Instructor` (memaksa LLM lokal selalu menjawab dalam format data yang rapi, bukan teks berantakan, saat melakukan *auto-tagging*).
* **File Watcher:** `Watchdog` (memantau perubahan file di folder catatan secara *real-time*).
* **Optional Hybrid Cloud Sync (Fase P2):** Klien sinkronisasi pasif ke **Supabase** (PostgreSQL + `pgvector`) atau **Git** ketika terdeteksi ada koneksi internet.

---

## 3. Diagram Arsitektur Sistem Tingkat Tinggi

```text
+-------------------------------------------------------------------------+
|                        USER TERMINAL ENVIRONMENT                        |
|                                                                         |
|   [ $ rph "ide baru" ]      [ $ rph ask "..." ]       [ $ rph tui ]     |
|     (Thin CLI Client)        (Query Interface)      (Textual Dashboard) |
+-------------+------------------------+-----------------------+----------+
              |                        |                       |
              +------------------------+-----------------------+
                                       | (Local IPC / HTTP FastAPI Lokal)
                                       v
+-------------------------------------------------------------------------+
|                    RAPHAEL DAEMON (`raphaeld` Service)                  |
|                                                                         |
|  +----------------------+  +--------------------+  +-----------------+  |
|  | Command Router       |  | Background Queue   |  | Vault Watcher   |  |
|  | (Chant Annulment)    |  | (Parallel Process) |  | (Watchdog FS)   |  |
|  +----------+-----------+  +---------+----------+  +--------+--------+  |
|             |                        |                      |           |
|             v                        v                      v           |
|  +-------------------------------------------------------------------+  |
|  |                  CORE ORCHESTRATOR & SUB-AGENTS                   |  |
|  |   [Appraisal Engine]   [Hybrid Retriever]   [Synthesizer Agent]   |  |
|  +------------------+------------------------+-----------------------+  |
+---------------------|------------------------|--------------------------+
                      |                        |
        +-------------+                        +--------------+
        | (SQL / File IO)                                     | (Local API: 11434)
        v                                                     v
+-------------------------------------+       +---------------------------+
|      LOCAL STORAGE (`~/.raphael`)   |       |    LOCAL AI (OLLAMA)      |
|                                     |       |                           |
|  1. Markdown Vault (`/vault/*.md`)  |       |  * LLM: Qwen2.5 / Llama3  |
|  2. SQLite DB (`raphael.db`)        |       |  * Embed: bge-m3 / nomic  |
|     - Tabel Relasional (Metadata)   |       |                           |
|     - FTS5 (Pencarian Kata Kunci)   |       |  (Opsional: Fallback ke   |
|     - sqlite-vec (Memori Vektor)    |       |   Cloud API saat Online)  |
+-------------------------------------+       +---------------------------+

---

## 4. Struktur Folder
raphael-cli/
├── pyproject.toml           # Konfigurasi proyek Python & dependencies (via uv)
├── README.md
├── docs/
│   ├── PRD.md
│   ├── ARCHITECT.md
│   ├── DESIGN.md
│   └── AGENT.md
└── src/
    └── raphael/
        ├── __init__.py
        ├── cli.py           # Entrypoint perintah terminal (`rph ...`)
        ├── tui.py           # Antarmuka visual terminal (`rph tui` dengan Textual)
        ├── daemon/
        │   ├── server.py    # Background service (`raphaeld`)
        │   ├── watcher.py   # Pemantau perubahan file Markdown (Watchdog)
        │   └── worker.py    # Eksekutor antrean tugas (Parallel Processing)
        ├── storage/
        │   ├── db.py        # Koneksi SQLite, FTS5, dan sqlite-vec
        │   ├── vault.py     # Pembaca & penulis file Markdown + YAML Frontmatter
        │   └── parsers.py   # Ekstraktor file PDF, DOCX, dan XLSX
        ├── brain/
        │   ├── llm.py       # Klien koneksi ke Ollama lokal (& Cloud fallback)
        │   ├── memory.py    # Mesin Hybrid Search (BM25 + Vector + Graph)
        │   └── agents.py    # Logika Analytical Appraisal, Synthesize, & Separate
        └── sync/
            └── cloud.py     # (Opsional P2) Modul sinkronisasi ke Cloud / Git

## 5. Format File Markdown (vault/*.md)
Setiap catatan yang dibuat via rph akan disimpan sebagai file .md dengan YAML Frontmatter yang diisi otomatis oleh Analytical Appraisal:

---
id: "rph-20261017-001"
title: "Aturan Pergerakan Karakter di Distrik Shibuya"
created_at: "2026-10-17T07:15:00+07:00"
updated_at: "2026-10-17T07:15:02+07:00"
category: "projects"
tags: ["worldbuilding", "tokyo-setting", "character-rules"]
entities: ["Shibuya", "Protagonis"]
appraisal_status: "completed"
---

Isi catatan asli dari pengguna ditulis di sini...

## [Raphael Analytical Summary]
> Intisari otomatis: Menetapkan batasan waktu interaksi karakter di area publik Tokyo saat malam hari.

## 6. Skema Database SQLite (raphael.db)
Database SQLite memiliki 5 tabel utama:

-- 1. Tabel Utama Catatan (Metadata & Isi)
CREATE TABLE notes (
    id TEXT PRIMARY KEY,               -- Sesuai dengan ID di Frontmatter Markdown
    file_path TEXT UNIQUE NOT NULL,    -- Lokasi file .md relatif terhadap /vault
    title TEXT,
    raw_content TEXT,                  -- Isi teks murni
    summary TEXT,                      -- Ringkasan hasil Analytical Appraisal
    category TEXT,                     -- inbox, projects, references, synthesized
    tags_json TEXT,                    -- Array JSON: ["tag1", "tag2"]
    entities_json TEXT,                -- Array JSON entitas penting (Nama tokoh, istilah, dll)
    content_hash TEXT,                 -- SHA-256 untuk mendeteksi jika file diedit dari luar
    created_at DATETIME,
    updated_at DATETIME
);

-- 2. Tabel Pencarian Teks Cepat (SQLite FTS5 Virtual Table)
CREATE VIRTUAL TABLE notes_fts USING fts5(
    id UNINDEXED,
    title,
    raw_content,
    summary,
    tags_json,
    content='notes',
    content_rowid='rowid'
);

-- 3. Tabel Memori Vektor (Menggunakan ekstensi sqlite-vec)
-- Menyimpan potongan paragraf (chunks) dalam bentuk koordinat vektor (768 dimensi)
CREATE VIRTUAL TABLE note_embeddings USING vec0(
    chunk_id INTEGER PRIMARY KEY,
    note_id TEXT,
    embedding FLOAT[768]
);

-- 4. Tabel Relasi / Knowledge Graph (Skill: All of Creation)
-- Menyimpan hubungan antar-catatan yang ditemukan otomatis oleh Raphael
CREATE TABLE note_links (
    source_note_id TEXT,
    target_note_id TEXT,
    relation_type TEXT,                -- 'similar', 'contradicts', 'expands_on'
    similarity_score REAL,             -- 0.0 s/d 1.0
    reasoning TEXT,                    -- Alasan singkat dari AI mengapa kedua catatan ini terhubung
    PRIMARY KEY (source_note_id, target_note_id)
);

-- 5. Tabel Antrean Tugas Latar Belakang (Skill: Parallel Processing)
CREATE TABLE background_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT,                     -- 'APPRAISE', 'EMBED', 'FIND_LINKS', 'SYNTHESIZE'
    payload_json TEXT,
    status TEXT DEFAULT 'PENDING',     -- 'PENDING', 'RUNNING', 'DONE', 'FAILED'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);