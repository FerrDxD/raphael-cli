# Project RAPHAEL (CLI Edition)

**Responsive Autonomous Personal Hub for Analytical & Executive Logic**

A local-first AI second brain that lives in your terminal. Notes are plain Markdown
files; the index is a single SQLite file; the brain is a local LLM (Ollama).
No account, no cloud, no browser — and it keeps working with the network unplugged.

```bash
rph "Ide mekanik baru: sistem reputasi faksi yang berubah berdasarkan cuaca malam"
# [Report] Catatan disimpan rph-20261017-001 ke #inbox (18ms)

rph ask "Apa saja aturan perilaku karakter untuk latar Tokyo?"
# [Report] Berdasarkan 4 catatan ... (catatan 1, catatan 3)
```

---

## Install

Requires **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone <repo> && cd raphael-cli
uv sync                 # creates .venv and installs rph / raphael / raphaeld
uv run rph doctor       # verify the local stack
```

Optional — enable accelerated in-database KNN:

```bash
uv sync --extra vec     # installs sqlite-vec; NumPy cosine fallback is used otherwise
```

For the offline brain (recommended):

```bash
ollama pull qwen2.5:7b        # reasoning, appraisal, synthesis
ollama pull bge-m3            # embeddings (768-dim)
ollama serve
```

Without Ollama everything still works: capture, search, list, show, synth, split
fall back to deterministic heuristics and are labelled as such.

## Command surface

| Command | Purpose |
| :--- | :--- |
| `rph "<text>"` | **Chant Annulment** — auto-detects note vs. question |
| `rph add "<text>"` | Force-save a note (`--now` to appraise synchronously) |
| `rph ingest <files...>` | Extract PDF / DOCX / XLSX / MD / TXT / CSV / JSON (`-p` project) |
| `rph ask "<q>"` | Hybrid-RAG answer over local notes (`--chat`, `--audit`) |
| `rph search "<q>"` | Instant FTS5 + vector search (`-t` tag filter) |
| `rph list` / `show` / `graph` / `rm` / `open` | Note management |
| `rph synth <id...>` | Merge notes into one document (`-o out.md|.docx`, `--save`) |
| `rph split <id|goal>` | Break a target into actionable tasks (`--save`) |
| `rph evolve "<rule>"` | Store a permanent preference (`--list`, `--forget`) |
| `rph status` | Daily briefing: vault, queue, projects, daemon insights |
| `rph reindex` | Rebuild the index from the vault (`--no-embed` skips vectors) |
| `rph tui` | Interactive three-panel dashboard |
| `rph daemon start\|stop\|restart\|status\|logs` | Background service control |
| `rph doctor` / `rph config` | Diagnostics and resolved configuration |

Piping works everywhere a note or question is expected:

```bash
cat spec.md | rph ingest -            # or: git diff | rph "ringkas progres hari ini"
```

One daemon serves one vault, and the port is the lock: if a daemon for another
`RAPHAEL_HOME` already owns it, `rph daemon start` says so — stop that daemon or
give this vault its own `daemon.port` in `config.toml`.

## How it works

```
rph (thin client) ──► raphaeld (daemon: HTTP + worker + watcher)
                          │
                          ├── vault/*.md        Markdown + YAML frontmatter (source of truth)
                          ├── raphael.db        SQLite: metadata, FTS5, chunk vectors, graph, queue
                          └── Ollama :11434     chat model + embedding model
```

* **Capture is instant.** Raw text is written to disk and indexed immediately;
  appraisal, embedding, and link discovery are queued for the daemon.
* **Files win.** Edit a note in Neovim/VSCode and the watcher reconciles the
  index from the file, not the other way around.
* **Hybrid recall.** FTS5 (BM25) and vector similarity are fused with Reciprocal
  Rank Fusion, so exact terms and paraphrases both hit.
* **Structured agents.** Appraisal, linking, splitting, and synthesis each run as
  a narrow sub-agent returning validated JSON (Pydantic), with deterministic
  fallbacks.
* **Degrades honestly.** Output is labelled `(engine: local)`, `(engine: heuristic)`,
  or `[Warning]` when the model is unreachable — never a silent hallucination.

## Storage layout

```
~/.raphael/
├── config.toml            optional overrides
├── raphael.db             SQLite (notes, FTS5, chunks, links, jobs, rules)
├── daemon.pid
├── logs/daemon.log
└── vault/
    ├── inbox/  projects/  references/  synthesized/  journal/
```

Copy the whole `~/.raphael` folder to another machine and your second brain
travels with it. Point `RAPHAEL_HOME` somewhere else to use a different vault.

### config.toml

```toml
[llm]
host = "http://127.0.0.1:11434"
chat_model = "qwen2.5:7b"
embed_model = "bge-m3"
embedding_dim = 768

[daemon]
port = 8787
auto_start = true

[memory]
link_threshold = 0.78
chunk_size = 900

[cloud]          # optional P2, disabled by default
enabled = false
base_url = "https://api.example.com/v1"
model = "large-model"
api_key_env = "RAPHAEL_CLOUD_API_KEY"
```

## Development

```bash
uv sync --group dev
uv run pytest              # full suite, offline-safe (LLM is stubbed)
uv run rph status
```

```
src/raphael/
├── cli.py          Typer command tree + Chant Annulment dispatch
├── service.py      use cases shared by CLI, daemon, and TUI
├── tui.py          Textual dashboard
├── daemon/         server.py · worker.py · watcher.py · control.py
├── storage/        db.py · vault.py · parsers.py
├── brain/          llm.py · memory.py · agents.py
└── sync/           cloud.py (optional git/cloud backup)
```

Design documents: `PRD.md`, `ARCHITECT.md`, `DESIGN.md`, `AGENTS.md`.
