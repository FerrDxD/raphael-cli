# UI/UX & CLI Ergonomics Design: Project RAPHAEL (CLI Edition)
**Responsive Autonomous Personal Hub for Analytical & Executive Logic**

* **Versi Dokumen:** 1.0.0
* **Status:** Approved Design Specification
* **Fokus Antarmuka:** Command-Line Interface (CLI) & Terminal User Interface (TUI)
* **Pustaka UI Utama:** `Typer` (CLI Parser), `Rich` (Terminal Formatting), `Textual` (Interactive TUI)

---

## 1. Filosofi Desain Antarmuka ("The Voice of the World")

Dalam seri *TenSura*, Raphael berkomunikasi langsung di dalam pikiran Rimuru dengan nada yang tenang, terstruktur, tanpa basa-basi, dan sangat presisi. Antarmuka terminal **Project RAPHAEL** dirancang untuk menerjemahkan sensasi tersebut ke dalam layar konsol/terminal:

1. **Zero-Fluff Output:** Tidak ada kalimat pembuka khas chatbot biasa seperti *"Halo! Berikut adalah hasil pencarian Anda..."*. Semua output langsung diawali dengan label sistem: **`[Report]`**, **`[Notice]`**, **`[Synthesis]`**, atau **`[Warning]`**.
2. **Chant Annulment (Ergonomi Ketikan Minimal):** Perintah yang paling sering dipakai (mencatat ide & bertanya) tidak boleh membutuhkan banyak *flag* (`--flag`). Cukup ketik `rph "teks"` dan sistem yang akan menyesuaikan.
3. **Glanceable Hierarchy:** Menggunakan warna ANSI terminal yang kontras namun nyaman di mata (tema *Dark/Cyber-Arcane*) agar mata pengguna bisa langsung membedakan mana fakta utama, mana *tag*, dan mana saran proaktif dari AI.
4. **Keyboard-First Navigation:** Di dalam mode layar penuh (`rph tui`), seluruh navigasi dapat dilakukan 100% menggunakan *keyboard* (mendukung *keybinding* standar maupun gaya Vim `j/k/h/l`).

---

## 2. Desain Pohon Perintah CLI (Command Tree & Syntax)

Nama biner utama adalah `raphael`, dengan alias pendek bawaan **`rph`** untuk kecepatan mengetik maksimal.

```text
rph (atau raphael)
├── "<teks bebas>"          # [Chant Annulment] Auto-detect: Simpan catatan ATAU Jawab pertanyaan
├── add "<teks>"            # Paksa simpan sebagai catatan cepat ke Inbox
├── ingest <path_file...>   # Ekstrak & analisis file lokal (PDF, DOCX, XLSX, MD, TXT)
│   └── --project, -p       # Masukkan langsung ke kategori proyek tertentu
├── ask "<pertanyaan>"      # Tanya jawab lintas-catatan (Hybrid RAG)
│   └── --chat, -c          # Masuk ke sesi diskusi interaktif (multi-turn)
├── search "<kata_kunci>"   # Pencarian instan (<100ms) tanpa nalar LLM (murni FTS5 + Vektor)
│   └── --tag, -t           # Filter berdasarkan tag spesifik
├── synth <id_1> <id_2>...  # [Synthesize] Gabungkan beberapa catatan jadi 1 dokumen utuh
│   └── --output, -o        # Simpan hasil sintesis ke file baru (.md / .docx)
├── split <id_atau_teks>    # [Separate] Pecah dokumen/tujuan besar menjadi daftar To-Do
├── evolve "<aturan_baru>"  # [Alteration] Tambahkan aturan permanen ke memori Raphael
├── status                  # Tampilkan Daily Briefing, antrean background worker, & insight baru
├── tui                     # [Thought Acceleration] Buka dashboard interaktif layar penuh
└── daemon                  # Kontrol servis latar belakang (start | stop | restart | logs)