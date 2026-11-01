# Product Requirements Document (PRD): Project RAPHAEL (CLI Edition)
**Responsive Autonomous Personal Hub for Analytical & Executive Logic**

* **Versi Dokumen:** 2.0.0 (Local-First CLI & Single-User Edition)
* **Status:** Approved / Ready for Architecture
* **Kategori Produk:** Local-First AI Second Brain, CLI & TUI Cognitive Co-Processor
* **Target Pengguna:** Single-User (Personal Use Only)
* **Mode Operasional:** 100% Offline-Capable (Local Storage & Local LLM) dengan Opsi Hybrid Cloud Sync
* **Referensi Konseptual:** Ultimate Skill *Raphael (Lord of Wisdom)* — TenSura

---

## 1. Executive Summary & Visi Produk
Sebagian besar aplikasi *Second Brain* modern bergantung penuh pada koneksi internet (*cloud-dependent*), memiliki antarmuka grafis (GUI) yang berat, dan bersifat pasif. Ketika pengguna berada di lingkungan tanpa internet atau membutuhkan kecepatan eksekusi tingkat tinggi langsung dari terminal, sistem berbasis web menjadi penghambat alur pikir (*bottleneck*).

**Project RAPHAEL (CLI Edition)** adalah *AI Second Brain* berbasis **Command-Line Interface (CLI) & Terminal User Interface (TUI)** yang dirancang khusus untuk **penggunaan pribadi (*single-user*)** dengan filosofi **Local-First & Offline-Ready**. 

Seluruh basis pengetahuan disimpan secara lokal di perangkat pengguna (menggunakan kombinasi file Markdown murni, database SQLite lokal + ekstensi vektor, serta mesin LLM lokal seperti Ollama/llama.cpp). Layaknya Raphael yang menyatu langsung di dalam jiwa Rimuru tanpa jeda jaringan, sistem ini berjalan sebagai *daemon* ringan di latar belakang sistem operasi—menganalisis catatan secara mandiri (*Analytical Appraisal*), menghubungkan konsep (*All of Creation*), menyintesis dokumen (*Synthesize*), dan siap dipanggil kapan saja melalui satu baris perintah di terminal tanpa latensi jaringan.

---

## 2. Masalah Utama (Problem Statement)
1. **Ketergantungan Internet (Cloud Dependency):** Banyak asisten AI dan *Second Brain* lumpuh total saat tidak ada koneksi internet, padahal ide dan pekerjaan sering dilakukan secara *offline* atau saat mobilitas tinggi.
2. **Context Switching & Overhead GUI:** Membuka browser atau aplikasi berat hanya untuk mencatat satu ide cepat (*brain dump*) atau mencari potongan referensi sering merusak fokus (*flow state*).
3. **Beban Organisasi Manual:** Merapikan folder, membuat *tag*, dan menghubungkan catatan secara manual memakan waktu yang seharusnya dipakai untuk berkarya atau menyelesaikan masalah.
4. **Kompleksitas Multi-User yang Tidak Perlu:** Sistem komersial membawa beban autentikasi, manajemen akun, dan pembatasan kuota API yang tidak dibutuhkan oleh sistem penggunaan pribadi (*single-user*).

---

## 3. Prinsip Desain Utama (Core Tenets)
1. **100% Offline-Capable (Local-First):** Seluruh fitur inti (pencatatan, pencarian semantik, analisis AI, dan sintesis) wajib berfungsi penuh tanpa koneksi internet menggunakan *Local LLM* dan *Local Vector Database*.
2. **Zero-Friction CLI Ergonomics:** Segala aksi harus bisa dieksekusi dalam hitungan milidetik dari terminal (misal: `raphael "ide baru..."` atau `rph ask "..."`).
3. **Plain-Text & Local DB Longevity:** Data utama tetap disimpan dalam format yang transparan dan mudah diakses langsung melalui *file system* (Markdown + SQLite), tanpa *vendor lock-in* atau enkripsi cloud pihak ketiga yang menyulitkan.
4. **Single-User Optimized:** Tidak ada sistem login, *multi-tenancy*, atau *role-based access control*. Seluruh sumber daya sistem didedikasikan untuk mempelajari pola pikir, gaya bahasa, dan proyek milik satu pengguna.
5. **Hybrid Intelligence (Opsional):** Menggunakan LLM lokal sebagai mesin utama saat *offline*, namun memiliki fitur *smart routing* untuk memanggil Cloud API kelas berat atau melakukan *cloud backup* otomatis ketika koneksi internet tersedia.

---

## 4. Pemetaan Konsep: Dari "Ultimate Skill" ke Fitur Teknis CLI

| Sub-Skill Raphael | Modul CLI / Sistem Lokal | Implementasi Teknis pada CLI Second Brain |
| :--- | :--- | :--- |
| **Analytical Appraisal** | `rph ingest` / *Local Parsing Engine* | Menerima input teks terminal, *piping* (`cat file.txt \| rph`), file lokal (PDF, DOCX, XLSX, MD), lalu mengekstrak intisari, entitas, dan *auto-tag* menggunakan Local LLM. |
| **All of Creation** | `rph recall` / *Local Vector & Graph DB* | Pencarian hibrida (*Full-Text Search* SQLite FTS5 + *Local Vector Search* `sqlite-vec`) yang memahami konteks dan hubungan antar catatan secara instan tanpa internet. |
| **Synthesize & Separate** | `rph synth` & `rph split` | **Synthesize:** Menggabungkan beberapa catatan/file lokal menjadi satu dokumen utuh (misal: menyusun bab *story bible* atau rangkuman materi).<br>**Separate:** Memecah satu target proyek besar menjadi daftar *actionable tasks* di terminal. |
| **Parallel Processing** | `raphaeld` (*Background Daemon*) | Proses latar belakang (sistem *daemon* / *local worker*) yang otomatis mengindeks ulang file Markdown yang diedit di Neovim/VSCode dan mencari benang merah antar catatan saat CPU *idle*. |
| **Chant Annulment** | *Natural CLI Alias & Pipe Support* | Eksekusi maksud pengguna tanpa perintah kaku. Mendukung *flag* minimal dan membaca konteks direktori kerja saat ini (`pwd`) secara otomatis. |
| **Thought Acceleration** | `rph tui` (*Interactive TUI Dashboard*) | Antarmuka terminal interaktif yang menampilkan draf tulisan di satu sisi dan secara *real-time* memunculkan referensi catatan terkait di panel samping. |
| **Alteration** | `rph evolve` / *Self-Updating Rules* | Kemampuan sistem untuk memperbarui fakta yang usang di database lokal dan menyesuaikan *system prompt* lokal berdasarkan koreksi langsung dari pengguna. |

---

## 5. Skenario Penggunaan di Terminal (CLI User Experience)

### A. Quick Capture & Brain Dump (`Chant Annulment`)
Tanpa perlu membuka editor atau menentukan folder, pengguna cukup mengetik di terminal:
```bash
$ rph "Ide mekanik baru: sistem reputasi faksi di Tokyo yang berubah berdasarkan waktu malam dan cuaca"
[Report] Catatan berhasil disimpan ke kategori: #Worldbuilding #GameDesign.
[Notice] Terdeteksi kemiripan konteks (89%) dengan catatan 'Profil Karakter & Aturan Lokasi Tokyo' (12 Okt). Tautan otomatis telah dibuat.
```

### B. Piping & Ekstraksi Dokumen Lokal (`Analytical Appraisal`)
Menganalisis file lokal atau output dari perintah terminal lain secara langsung:
```bash
$ rph ingest ./dokumen_referensi.pdf ./data_rekap.xlsx --project "Riset Utama"
[Report] Memproses 2 file secara lokal... Selesai (1.4s).
[Report] 14 entitas kunci diekstrak dan diindeks ke database lokal.

$ git diff | rph "buatkan catatan log progres hari ini dari perubahan kode ini"
[Report] Log progres pengembangan berhasil dirangkum dan disimpan ke jurnal harian.
```

### C. Kueri & Sintesis Lintas-Catatan (`All of Creation` & `Synthesize`)
```bash
$ rph ask "Apa saja aturan perilaku karakter yang sudah kubuat untuk latar Tokyo, dan apakah ada yang kontradiktif?"
[Report] Berdasarkan 4 catatan di penyimpanan lokal:
1. Aturan A (dari story_bible_v1.md)...
2. Aturan B (dari quick_note_042.md)...
[Notice] Ditemukan potensi kontradiksi pada jadwal pergerakan karakter di malam hari antara Catatan #1 dan Catatan #2. Ingin menjalankan 'rph synth --resolve' untuk menyelaraskannya?
```

### D. Mode TUI Interaktif (`Thought Acceleration`)
```bash
$ rph tui
```
Membuka antarmuka *full-screen terminal* (seperti `lazygit` atau `htop`) dengan tiga panel:
1. **Panel Kiri:** Daftar proyek, *knowledge graph tags*, dan antrean tugas.
2. **Panel Tengah:** Editor/pembaca catatan Markdown terintegrasi (atau terhubung langsung ke `$EDITOR` seperti Neovim/Nano).
3. **Panel Kanan (Raphael Stream):** Panel proaktif yang menampilkan `[Report]` dan `[Notice]` secara *real-time* seiring pengguna mengetik atau berpindah catatan.

---

## 6. Spesifikasi Fitur Berdasarkan Prioritas (Roadmap)

### P0 — Core Local CLI ("Great Sage" Offline Engine)
* **Single-Binary / Fast CLI Runner:** Eksekusi perintah dasar (`rph add`, `rph ask`, `rph search`, `rph list`) dengan waktu muat (*startup time*) di bawah 100ms.
* **Dual-Layer Local Storage:**
  * File `.md` (Markdown) dengan *YAML Frontmatter* sebagai sumber kebenaran utama (*human-readable* & bisa diedit pakai teks editor apa pun).
  * Database **SQLite lokal** (dengan FTS5 untuk pencarian kata kunci cepat dan `sqlite-vec` untuk *vector embeddings* lokal).
* **Local AI Integration (Offline):** Integrasi langsung dengan *engine* lokal (seperti **Ollama** atau **llama.cpp**) untuk menghasilkan *embedding* teks dan menjalankan *Analytical Appraisal* tanpa internet.
* **Raphael Persona Output:** Format output terminal berwarna (*syntax highlighted*) yang konsisten menggunakan gaya bahasa analitis `[Report]` dan `[Notice]`.

### P1 — Autonomous Daemon & Synthesis ("Raphael" Proactive Mode)
* **Background File Watcher & Daemon (`raphaeld`):** Memantau folder catatan lokal. Jika pengguna mengedit file Markdown secara langsung lewat teks editor luar, *daemon* otomatis memperbarui indeks vektor dan mencari tautan baru di latar belakang.
* **Synthesis & Separate Commands (`rph synth`, `rph split`):** Menggabungkan beberapa catatan menjadi satu draf dokumen baru (Markdown/DOCX/PDF) atau memecah catatan panjang menjadi daftar *to-do* terstruktur.
* **Daily Terminal Briefing (`rph status` / MOTD):** Menampilkan ringkasan proyek aktif, tugas yang belum selesai, dan *insight* baru yang ditemukan oleh *daemon* semalam saat pengguna pertama kali membuka terminal.

### P2 — Interactive TUI & Hybrid Cloud Sync ("Manas: Ciel" Evolution)
* **Full Terminal UI (`rph tui`):** Antarmuka visual di dalam terminal dengan navigasi *keyboard-driven* (Vim-like keybindings).
* **Optional Hybrid Cloud Sync & LLM Fallback:**
  * Jika terdeteksi ada internet (dan diizinkan pengguna), sistem dapat menyinkronkan cadangan database terenkripsi/terstruktur ke cloud pribadi (misal: ke proyek Supabase pribadi atau Git repository) serta mendelegasikan tugas analisis super berat ke Cloud LLM API.
* **Self-Evolving Rules (`rph evolve`):** Menyimpan memori preferensi pengguna secara permanen di tabel konfigurasi lokal sehingga gaya rangkuman dan struktur proyek makin presisi seiring waktu.

---

## 7. Kebutuhan Non-Fungsional (Non-Functional Requirements)
* **Offline Reliability:** 0% ketergantungan pada koneksi eksternal untuk seluruh fitur P0 dan P1. Jika jaringan terputus di tengah jalan, sistem tidak boleh *crash* atau kehilangan data.
* **Resource Footprint (Hemat RAM & CPU):**
  * CLI utama harus sangat ringan.
  * Pemrosesan LLM lokal hanya memakan VRAM/RAM saat ada tugas analisis (*on-demand* atau dijadwalkan saat sistem tidak sibuk) agar tidak membuat laptop/PC melambat saat digunakan untuk tugas lain.
* **Data Portability & Transparency:** Seluruh catatan disimpan di satu direktori lokal (misal: `~/.raphael/vault/`). Jika pengguna menyalin satu folder tersebut ke flashdisk atau komputer lain, seluruh memori dan *Second Brain* langsung ikut berpindah.
* **Kecepatan Eksekusi:**
  * Pencarian kata kunci & vektor lokal (`rph search`): < 200 milidetik.
  * Penyimpanan catatan mentah (`rph add`): Instan (< 50 milidetik), sedangkan proses *Appraisal* (pemberian *tag* & koneksi oleh LLM lokal) dilakukan secara asinkron oleh *background worker*.

---

## 8. Metrik Keberhasilan Pribadi (Personal Success Metrics)
1. **Zero-Browser Capture:** 100% ide mendadak, potongan kode, dan catatan tugas bisa langsung masuk ke sistem dari terminal tanpa perlu membuka browser atau aplikasi berat.
2. **Offline Continuity:** Pengguna tetap bisa melakukan riset lintas-catatan, mencari referensi cerita/materi, dan menyintesis dokumen saat berada di tempat tanpa sinyal sekalipun.
3. **High Proactive Relevance:** Minimal 8 dari 10 notifikasi `[Notice]` (tautan antar-catatan yang ditemukan otomatis oleh sistem) benar-benar relevan dan membantu pekerjaan pengguna.

---

## 9. Jembatan Menuju Dokumen Selanjutnya
Perubahan ke arsitektur **Local-First CLI (Single-User)** ini akan menjadi fondasi utama untuk tiga dokumen berikutnya:
1. **`ARCHITECT.md`:** Akan merancang arsitektur *local-first* (pemilihan bahasa pemrograman CLI seperti Python/Typer, Go, atau Rust; skema SQLite + `sqlite-vec`; arsitektur *daemon* latar belakang; manajemen model lokal via Ollama; serta opsi modul sinkronisasi pasif ke cloud bila diperlukan).
2. **`DESIGN.md`:** Akan merancang ergonomi perintah CLI (*command tree*, *flags*, *piping*), skema warna output terminal (`[Report]` vs `[Notice]`), serta tata letak antarmuka **TUI (Terminal User Interface)**.
3. **`AGENT.md`:** Akan merancang cara mengoptimalkan *System Prompt* dan pembagian peran sub-agen (*Appraisal*, *Synthesizer*, *Linker*) agar tetap cerdas, akurat, dan hemat *context window* meskipun dijalankan menggunakan model LLM lokal berukuran kecil/menengah (seperti Llama-3 8B, Qwen 2.5 7B, atau Gemma 2).
