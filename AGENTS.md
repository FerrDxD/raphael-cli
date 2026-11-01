# Agent & Cognitive Architecture: Project RAPHAEL (CLI Edition)
**Responsive Autonomous Personal Hub for Analytical & Executive Logic**

* **Versi Dokumen:** 1.0.0
* **Status:** Approved Cognitive Blueprint
* **Pola Orkestrasi:** Deterministic Router + Specialized Micro-Agents (Local LLM Optimized)
* **Mesin Utama (Offline):** TipRouter (`gpt-6-luna` / `Deepseek-v4.1 flash`) + `bge-m3` (Embedding)

---

## 1. Filosofi Desain Agen (Mengapa Sub-Agent?)

Membuat satu AI melakukan segalanya dalam satu *prompt* panjang akan menurunkan akurasi, terutama pada model lokal (7B/8B parameter) yang memiliki keterbatasan *working memory*. 

Project RAPHAEL menerapkan prinsip **"Satu Sub-Skill = Satu Sub-Agen Spesialis"**:
1. **Context Window Hygiene:** Setiap sub-agen hanya menerima data yang relevan dengan tugasnya.
2. **Structured Output First:** Sub-agen pekerja latar belakang (*background workers*) wajib mengeluarkan output berupa **JSON murni** (divalidasi dengan `Pydantic`), bukan teks percakapan. Hanya lapisan terakhir (*Persona Layer*) yang berbicara dengan gaya bahasa Raphael.
3. **Zero-Hallucination Grounding:** Agen dilarang mengarang fakta di luar catatan yang ada di `~/.raphael/vault/`, kecuali pengguna secara eksplisit meminta ide kreatif (*brainstorming*).

---

## 2. Peta Orkestrasi Sub-Agen (The Cognitive Pipeline)

```text
                       [ Input Terminal Pengguna (`rph ...`) ]
                                          |
                                          v
                        +-----------------------------------+
                        |   0. CHANT ANNULMENT (Router)     |
                        |  Mengklasifikasikan Niat Pengguna |
                        +-----------------+-----------------+
                                          |
        +---------------------------------+---------------------------------+
        | (Input Catatan / File)          | (Pertanyaan / Diskusi)          | (Perintah Gabung/Pecah)
        v                                 v                                 v
+-----------------------+       +-----------------------+       +-----------------------+
| 1. APPRAISAL AGENT    |       | 3. ALL OF CREATION    |       | 4. SYNTHESIZER &      |
| (Analytical Appraisal)|       |    (RAG & Recall)     |       |    SEPARATOR AGENT    |
| - Ekstrak Judul & Tag |       | - Hybrid Search DB    |       | - Gabung draf (.md)   |
| - Ekstrak Entitas     |       | - Deteksi Kontradiksi |       | - Pecah Task (To-Do)  |
| - Buat Ringkasan      |       +-----------+-----------+       +-----------+-----------+
+-----------+-----------+                   |                               |
            |                               +---------------+---------------+
            v (Background Queue)                            |
+-----------------------+                                   v
| 2. LINKER AGENT       |                       +-----------------------+
| (Parallel Processing) |---------------------->|   5. RAPHAEL PERSONA  |
| - Cari benang merah   |   (Saran Proaktif)    |      FORMATTER        |
| - Buat relasi Graph   |                       | Output: [Report] &    |
+-----------------------+                       |         [Notice]      |
                                                +-----------------------+

                                                