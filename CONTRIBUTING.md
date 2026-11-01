# Contributing to Project RAPHAEL

Thanks for your interest. RAPHAEL is a local-first, offline-capable second brain
CLI; keep that ethos in mind: no cloud dependency, no silent hallucination, files
win.

## Before you start

- Read `README.md`, `AGENTS.md`, `ARCHITECT.md`, and `DESIGN.md` for the cognitive
  architecture and design constraints.
- Search existing issues/PRs to avoid duplicates.
- For large changes, open an issue first to discuss scope.

## Development setup

```bash
git clone https://github.com/FerrDxD/raphael-cli.git
cd raphael-cli
uv sync --group dev
uv run pytest              # full suite, offline-safe (LLM is stubbed)
uv run rph doctor
```

Optional accelerated KNN:

```bash
uv sync --extra vec
```

## Project layout

```
src/raphael/
├── cli.py          Typer command tree + Chant Annulment dispatch
├── service.py      use cases shared by CLI, daemon, TUI
├── tui.py          Textual dashboard
├── daemon/         server.py · worker.py · watcher.py · control.py
├── storage/        db.py · vault.py · parsers.py
├── brain/          llm.py · memory.py · agents.py
└── sync/           cloud.py (optional)
```

## Coding conventions

- Python 3.12+. Use type hints. Match existing style (ruff-friendly).
- Keep sub-agents narrow: each returns validated JSON (Pydantic), not prose.
- Structured output first; only the persona layer speaks in Raphael's voice.
- Never invent facts outside the vault; label heuristic/degraded output honestly.
- Preserve offline fallbacks — every feature must work without Ollama.
- Files are source of truth; index is derived.

## Tests

- Add/adjust tests under `tests/`. Run `uv run pytest` before submitting.
- LLM calls are stubbed in tests; do not require a running Ollama.
- Prefer focused tests for the changed behavior.

## Commit messages

Use Conventional Commits:

```
feat: add ...
fix: ...
docs: ...
refactor: ...
test: ...
```

## Pull requests

- Keep PRs focused and small.
- Describe what changed and why; link the issue.
- Ensure CI/tests pass and `uv run rph doctor` works.
- Update docs when behavior changes.

## Reporting bugs

Open an issue with reproduction steps, expected vs. actual behavior, and version.
For security issues, see `SECURITY.md` (do not file publicly).

## Contact

Maintainer: FerrDxD — ferdi@nawasena.site
Repository: https://github.com/FerrDxD/raphael-cli