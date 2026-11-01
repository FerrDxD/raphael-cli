"""``rph`` / ``raphael`` — the terminal surface.

Zero-fluff output: every line is prefixed with a system label
(DESIGN.md §1). Commands stay flag-light: the two most common actions are
bare text (``rph "ide"``) and ``rph ask "..."``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.markdown import Markdown
from rich.prompt import Prompt
from rich.table import Table

from raphael import __version__
from raphael.config import load_settings
from raphael.console import (
    error,
    get_console,
    notice,
    notes_table,
    report,
    synthesis,
    thinking,
    warning,
)
from raphael.models import Note
from raphael.service import Raphael
from raphael.storage.parsers import ParserError
from raphael.utils import first_heading, parse_id_list, truncate

app = typer.Typer(
    name="raphael",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help=(
        "RAPHAEL — local-first AI second brain.\n\n"
        "Quick capture: [cyan]rph \"ide baru\"[/]\n"
        "Ask your notes: [cyan]rph ask \"apa aturan karakternya?\"[/]"
    ),
)

COMMANDS = {
    "add",
    "ask",
    "config",
    "daemon",
    "doctor",
    "evolve",
    "graph",
    "ingest",
    "list",
    "open",
    "reindex",
    "rm",
    "search",
    "show",
    "split",
    "status",
    "synth",
    "tui",
}


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _svc(offline: bool = False) -> Raphael:
    return Raphael(load_settings(), offline_only=offline)


_STDIN_CACHE: Optional[str] = None


def _read_stdin() -> str:
    """Read piped input once and remember it.

    ``main`` peeks at stdin to decide whether the bare invocation is a capture,
    then ``chant`` reads again; without the cache the second read would see an
    exhausted pipe and silently drop the text.
    """

    global _STDIN_CACHE
    if _STDIN_CACHE is not None:
        return _STDIN_CACHE
    try:
        if sys.stdin is None or sys.stdin.isatty():
            _STDIN_CACHE = ""
        else:
            _STDIN_CACHE = sys.stdin.read()
    except (OSError, ValueError):
        _STDIN_CACHE = ""
    return _STDIN_CACHE


def _tags_text(tags: list[str]) -> str:
    return " ".join(f"[tag]#{tag}[/]" for tag in tags)


def _print_links(links, service: Raphael) -> None:
    for link in links:
        target = service.db.get_note(link.target_note_id)
        if target is None:
            continue
        label = {
            "similar": "kemiripan",
            "contradicts": "potensi kontradiksi",
            "expands_on": "memperluas",
        }.get(link.relation_type, link.relation_type)
        pct = int(round(link.similarity_score * 100))
        notice(
            f"{label.capitalize()} ({pct}%) dengan '{target.title}' "
            f"[muted]({target.id})[/]"
        )
        if link.reasoning:
            get_console().print(f"   [muted]{link.reasoning}[/]")


def _summary_repeats_body(summary: str, body: str) -> bool:
    """True when the summary is just the note's own text again.

    Heuristic appraisal copies the first sentences, so for short notes the
    "summary" is the whole body; printing both is noise. Markdown markers are
    dropped before comparing so headings do not defeat the check.
    """

    def squash(text: str) -> str:
        return " ".join(text.replace("#", " ").split()).lower()

    squashed_summary = squash(summary)
    return bool(squashed_summary) and squashed_summary in squash(body)


def _note_rows(notes: list[Note]) -> list[dict]:
    return [
        {"id": note.id, "title": note.title, "tags": note.tags}
        for note in notes
    ]


def _fail(message: str) -> None:
    error(message)
    raise typer.Exit(code=1)


# ----------------------------------------------------------------------
# capture
# ----------------------------------------------------------------------
@app.command("add")
def add(
    text: str = typer.Argument(..., help="Note body. Use - to read stdin."),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Override title."),
    category: Optional[str] = typer.Option(None, "--category", "-c", help="inbox|projects|references|journal"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Attach to a project."),
    tag: list[str] = typer.Option([], "--tag", help="Add a tag (repeatable)."),
    now_analyze: bool = typer.Option(False, "--now", help="Run appraisal synchronously."),
) -> None:
    """Force-save text as a quick note (no intent guessing)."""

    if text.strip() == "-":
        text = _read_stdin()
    if not text.strip():
        _fail("nothing to save")

    with _svc() as service:
        try:
            result = service.capture(
                text,
                title=title,
                category=category,
                project=project,
                tags=tag,
                appraise=now_analyze,
                embed=now_analyze,
                source="cli",
            )
        except ValueError as exc:
            _fail(str(exc))
            return

        note = result.note
        report(
            f"Catatan disimpan [id]{note.id}[/] ke [tag]#{note.category}[/] "
            f"[muted]({result.elapsed_ms:.0f}ms)[/]"
        )
        get_console().print(f"   [title]{note.title}[/]")
        if note.tags:
            get_console().print(f"   {_tags_text(note.tags)}")
        if note.summary:
            get_console().print(f"   [muted]{note.summary}[/]")
        if not now_analyze:
            notice("Appraisal dijadwalkan ke background worker (daemon).")
        _print_links(result.links, service)


@app.command("ingest")
def ingest(
    paths: list[Path] = typer.Argument(..., help="Local files: PDF, DOCX, XLSX, MD, TXT, CSV, JSON."),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Attach to a project."),
    quick: bool = typer.Option(False, "--fast", help="Skip appraisal, queue it instead."),
) -> None:
    """Extract and index local documents (fully offline)."""

    # "-" means stdin, so `cat spec.md | rph ingest -` works like every other filter.
    from_stdin = [str(p) for p in paths if str(p) == "-"]
    file_paths = [p for p in paths if str(p) != "-"]
    if not paths:
        _fail("berikan setidaknya satu file, atau '-' untuk membaca stdin.")
        return

    missing = [str(p) for p in file_paths if not p.exists()]
    if missing:
        _fail(f"file not found: {', '.join(missing)}")

    piped = _read_stdin().strip() if from_stdin else ""

    with _svc() as service:
        results = []
        if file_paths:
            try:
                with thinking(f"Mengekstrak {len(file_paths)} file secara lokal"):
                    results.extend(
                        service.ingest(file_paths, project=project, appraise=not quick)
                    )
            except ParserError as exc:
                _fail(str(exc))
                return
        if from_stdin:
            if not piped:
                _fail("stdin kosong: tidak ada teks untuk di-ingest.")
                return
            results.append(
                service.capture(
                    piped,
                    title=first_heading(piped) or "Catatan pipa",
                    category="projects" if project else "references",
                    project=project,
                    source="stdin",
                    appraise=not quick,
                )
            )

        for result in results:
            note = result.note
            report(
                f"{note.source or 'file'} → [id]{note.id}[/] "
                f"[muted]({len(note.body.split())} kata, engine: {result.engine})[/]"
            )
            get_console().print(f"   [title]{note.title}[/]")
            if note.tags:
                get_console().print(f"   {_tags_text(note.tags)}")
            _print_links(result.links, service)
        report(f"Selesai: {len(results)} sumber diproses dan diindeks.")


@app.command("ingest-text", hidden=True)
def ingest_text(
    text: str = typer.Argument("", help="Raw text (stdin fallback)."),
    title: Optional[str] = typer.Option(None, "--title", "-t"),
) -> None:
    """Internal: capture piped text as a note."""

    body = text or _read_stdin()
    if not body.strip():
        _fail("nothing on stdin")
    with _svc() as service:
        result = service.capture(body, title=title, source="pipe")
        report(f"Catatan disimpan [id]{result.note.id}[/] [muted]({result.elapsed_ms:.0f}ms)[/]")
        get_console().print(f"   [title]{result.note.title}[/]")
        notice("Appraisal dijadwalkan ke background worker.")


# ----------------------------------------------------------------------
# query
# ----------------------------------------------------------------------
@app.command("ask")
def ask(
    question: str = typer.Argument(..., help="Question about your notes."),
    chat: bool = typer.Option(False, "--chat", "-c", help="Multi-turn session."),
    audit: bool = typer.Option(False, "--audit", help="Also look for contradictions."),
    limit: int = typer.Option(6, "--limit", "-n", help="How many notes to ground on."),
) -> None:
    """Answer a question using only local notes (hybrid RAG)."""

    with _svc() as service:
        history: list[dict[str, str]] = []
        current = question
        while True:
            with thinking("Menelusuri vault lokal"):
                result = service.ask(current, limit=limit, history=history, audit=audit)
            report(f"{result.answer}")
            if result.notes:
                ids = ", ".join(f"[id]{n.id}[/]" for n in result.notes)
                get_console().print(f"   [muted]Sumber: {ids} · {result.elapsed_ms:.0f}ms[/]")
            for item in result.contradictions:
                warning(f"Potensi kontradiksi: {item['detail']} ({item['a']} vs {item['b']})")
            if result.engine == "heuristic":
                warning(
                    "LLM lokal tidak aktif — jawaban dibangun dari catatan mentah. "
                    "Jalankan 'ollama serve' untuk analisis penuh."
                )

            if not chat:
                return

            history.append({"role": "user", "content": current})
            history.append({"role": "assistant", "content": result.answer})
            try:
                current = Prompt.ask("[muted]rph[/]")
            except (KeyboardInterrupt, EOFError):
                get_console().print()
                return
            if current.strip().lower() in {"/exit", "/quit", "exit", "quit"}:
                return
            if not current.strip():
                return


@app.command("search")
def search(
    query: str = typer.Argument(..., help="Keyword or phrase."),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag."),
    limit: int = typer.Option(15, "--limit", "-n"),
) -> None:
    """Instant search (FTS5 + vectors, no LLM reasoning)."""

    with _svc() as service:
        hits = service.search(query, limit=limit, tag=tag)
        if not hits:
            notice("Tidak ada hasil.")
            return
        table = Table(box=None, show_edge=False, pad_edge=False)
        table.add_column("ID", style="id", no_wrap=True)
        table.add_column("Title", style="title", overflow="fold")
        table.add_column("Match", style="muted", no_wrap=True)
        table.add_column("Score", style="score", justify="right", no_wrap=True)
        for hit in hits:
            table.add_row(hit.note_id, hit.title, hit.match, f"{hit.score:.2f}")
        get_console().print(table)
        report(f"{len(hits)} hasil.")


@app.command("list")
def list_notes(
    category: Optional[str] = typer.Option(None, "--category", "-c"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """List recent notes."""

    with _svc() as service:
        notes = service.db.list_notes(category=category, tag=tag, limit=limit)
        if not notes:
            notice("Vault masih kosong. Mulai dengan: rph \"ide pertama\"")
            return
        get_console().print(notes_table(_note_rows(notes)))
        report(f"{len(notes)} catatan.")


@app.command("show")
def show(note_id: str = typer.Argument(..., help="Full id, unique prefix, or title fragment.")) -> None:
    """Print one note."""

    with _svc() as service:
        note = service.get_note(note_id)
        if note is None:
            _fail(f"catatan tidak ditemukan: {note_id}")
            return
        get_console().print(
            f"[id]{note.id}[/] · [tag]#{note.category}[/] · "
            f"[muted]{note.updated_at.strftime('%d %b %Y %H:%M') if note.updated_at else '-'}[/]"
        )
        get_console().print(f"[title]{note.title}[/]")
        if note.tags:
            get_console().print(_tags_text(note.tags))
        get_console().print()
        get_console().print(Markdown(note.body or "_(kosong)_"))
        # Short notes get a heuristic summary that is just the body again; showing
        # it would duplicate the text, so compare after squashing markdown noise.
        if note.summary and not _summary_repeats_body(note.summary, note.body):
            get_console().print()
            get_console().print(f"[muted]> {note.summary}[/]")
        links = service.links_for(note.id)
        if links:
            get_console().print()
            _print_links(links, service)


@app.command("graph")
def graph(note_id: str = typer.Argument(..., help="Note id.")) -> None:
    """Show the knowledge-graph neighbourhood of a note."""

    with _svc() as service:
        note = service.get_note(note_id)
        if note is None:
            _fail(f"catatan tidak ditemukan: {note_id}")
            return
        links = service.links_for(note.id, limit=20)
        if not links:
            notice("Belum ada relasi. Jalankan 'rph daemon start' agar daemon menautkan catatan.")
            return
        get_console().print(f"[title]{note.title}[/] [muted]({note.id})[/]")
        _print_links(links, service)


@app.command("rm")
def remove(
    note_id: str = typer.Argument(..., help="Note id."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete a note and its index entries."""

    with _svc() as service:
        note = service.get_note(note_id)
        if note is None:
            _fail(f"catatan tidak ditemukan: {note_id}")
            return
        if not yes:
            confirm = Prompt.ask(
                f"Hapus '{truncate(note.title, 50)}' [id]({note.id})[/]?", choices=["y", "n"], default="n"
            )
            if confirm != "y":
                notice("Dibatalkan.")
                return
        service.delete_note(note.id)
        report(f"Dihapus: {note.id}")


@app.command("open")
def open_note(note_id: str = typer.Argument(..., help="Note id.")) -> None:
    """Open a note in $EDITOR."""

    import os
    import subprocess

    with _svc() as service:
        path = service.edit_note(note_id)
        if path is None:
            _fail(f"catatan tidak ditemukan: {note_id}")
            return
        editor = service.settings.editor or os.environ.get("EDITOR")
        if not editor:
            report(str(path))
            return
        try:
            subprocess.run([editor, str(path)], check=False)
        except OSError as exc:
            _fail(f"gagal membuka editor: {exc}")


# ----------------------------------------------------------------------
# synthesis
# ----------------------------------------------------------------------
@app.command("synth")
def synth(
    ids: list[str] = typer.Argument(..., help="Note ids to merge."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write to .md or .docx."),
    instruction: Optional[str] = typer.Option(None, "--instruction", "-i", help="Extra guidance."),
    title: Optional[str] = typer.Option(None, "--title", "-t"),
    save: bool = typer.Option(False, "--save", help="Also store the result in the vault."),
) -> None:
    """Synthesize several notes into one document."""

    tokens = parse_id_list(ids)
    if not tokens:
        _fail("berikan minimal satu id catatan")

    with _svc() as service:
        try:
            with thinking("Menyintesis catatan"):
                result = service.synth(tokens, instruction=instruction, title=title)
        except (LookupError, ValueError) as exc:
            _fail(str(exc))
            return

        if output:
            try:
                written = _write_output(output, result.title, result.markdown)
            except (OSError, ValueError) as exc:
                _fail(str(exc))
                return
            synthesis(f"Ditulis ke {written}")
        else:
            get_console().print(Markdown(result.markdown))

        synthesis(
            f"{len(result.sources)} sumber digabung [muted](engine: {result.engine})[/]"
        )

        if save:
            with thinking("Menyimpan hasil sintesis"):
                captured = service.capture(
                    result.markdown,
                    title=result.title,
                    category="synthesized",
                    source="synth",
                    appraise=False,
                )
            report(f"Tersimpan sebagai [id]{captured.note.id}[/]")


def _write_output(path: Path, title: str, markdown: str) -> Path:
    suffix = path.suffix.lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".docx":
        try:
            import docx  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ValueError("DOCX export membutuhkan 'python-docx'") from exc
        document = docx.Document()
        for line in markdown.splitlines():
            stripped = line.strip()
            if stripped.startswith("### "):
                document.add_heading(stripped[4:], level=3)
            elif stripped.startswith("## "):
                document.add_heading(stripped[3:], level=2)
            elif stripped.startswith("# "):
                document.add_heading(stripped[2:], level=1)
            elif stripped.startswith("- "):
                document.add_paragraph(stripped[2:], style="List Bullet")
            elif stripped:
                document.add_paragraph(stripped)
        document.save(str(path))
        return path
    if suffix == ".pdf":
        raise ValueError("ekspor PDF belum didukung; gunakan .md lalu konversi lokal")
    path.write_text(markdown.rstrip() + "\n", encoding="utf-8", newline="\n")
    return path


@app.command("split")
def split(
    target: str = typer.Argument(..., help="Note id/title, or a goal description."),
    goal: Optional[str] = typer.Option(None, "--goal", "-g"),
    save: bool = typer.Option(False, "--save", help="Store the task list as a journal note."),
) -> None:
    """Separate a big target into actionable terminal tasks."""

    with _svc() as service:
        try:
            with thinking("Memecah target menjadi tugas"):
                result = service.split(target, goal=goal)
        except ValueError as exc:
            _fail(str(exc))
            return

        if not result.tasks:
            notice("Tidak ada tugas yang bisa diekstrak.")
            return

        table = Table(box=None, show_edge=False, pad_edge=False, title=result.goal or None)
        table.add_column("#", style="muted", width=3)
        table.add_column("Task", style="title", overflow="fold")
        table.add_column("Priority", style="score", no_wrap=True)
        table.add_column("Est.", style="muted", no_wrap=True)
        for index, task in enumerate(result.tasks, start=1):
            table.add_row(str(index), task.title, task.priority, task.estimate)
        get_console().print(table)
        report(f"{len(result.tasks)} tugas [muted](engine: {result.engine})[/]")

        if save:
            lines = [f"# Tasks: {result.goal or target}", ""]
            for index, task in enumerate(result.tasks, start=1):
                detail = f" — {task.detail}" if task.detail else ""
                lines.append(f"- [ ] {index}. {task.title}{detail} ({task.priority})")
            captured = service.capture(
                "\n".join(lines),
                title=f"Tasks: {result.goal or target}",
                category="journal",
                source="split",
                appraise=False,
            )
            report(f"Tersimpan sebagai [id]{captured.note.id}[/]")


# ----------------------------------------------------------------------
# meta
# ----------------------------------------------------------------------
@app.command("evolve")
def evolve(
    rule: Optional[str] = typer.Argument(None, help="Permanent preference for Raphael."),
    show: bool = typer.Option(False, "--list", help="List stored rules instead."),
    forget: Optional[str] = typer.Option(None, "--forget", help="Remove a rule key."),
) -> None:
    """Teach Raphael a permanent rule (Skill: Alteration)."""

    with _svc() as service:
        if show:
            rules = service.db.list_rules()
            if not rules:
                notice("Belum ada aturan tersimpan.")
                return
            for key, value in rules:
                get_console().print(f"[muted]{key}[/]  {value}")
            return
        if forget:
            report("Dihapus." if service.forget_rule(forget) else "Key tidak ditemukan.")
            return
        if not rule:
            warning("Berikan aturan yang ingin diajarkan, atau pakai --list / --forget.")
            raise typer.Exit(code=1)
        key = service.evolve(rule)
        report(f"Aturan tersimpan [muted]({key})[/]: {rule}")
        notice("Aturan ini kini disuntikkan ke system prompt semua sub-agen.")


@app.command("status")
def status() -> None:
    """Daily briefing: vault size, queue, projects, fresh insights."""

    with _svc() as service:
        data = service.status()
        stats = data["stats"]
        jobs = stats.get("jobs", {})

        get_console().print(f"[title]RAPHAEL[/] [muted]v{__version__}[/]")
        get_console().print(
            f"[muted]vault[/] {data['vault']}\n"
            f"[muted]db   [/] {data['db']} [muted](vec: "
            f"{'sqlite-vec' if stats['vec_enabled'] else 'numpy'})[/]"
        )
        get_console().print()

        summary = Table(box=None, show_edge=False, pad_edge=False, show_header=False)
        summary.add_column(style="muted", no_wrap=True)
        summary.add_column(style="white")
        summary.add_row("Catatan", str(stats["notes"]))
        summary.add_row("Chunk / vektor", f"{stats['chunks']} / {stats['embedded']}")
        summary.add_row("Relasi", str(stats["links"]))
        summary.add_row(
            "Antrean",
            ", ".join(f"{k}={v}" for k, v in jobs.items()) if jobs else "kosong",
        )
        llm = data["llm"]
        summary.add_row(
            "LLM lokal",
            f"{'aktif' if llm['local_available'] else 'tidak aktif'} · {llm['chat_model']}"
            f" · embed {llm['embed_model']}",
        )
        get_console().print(summary)

        if data["today"]:
            get_console().print()
            get_console().print("[muted]Hari ini[/]")
            get_console().print(notes_table(_note_rows(data["today"])[:8]))

        if data["projects"]:
            get_console().print()
            get_console().print("[muted]Proyek aktif[/]")
            for name, count in sorted(data["projects"].items(), key=lambda x: -x[1])[:6]:
                get_console().print(f"  [tag]{name}[/] [muted]({count})[/]")

        if data["recent_links"]:
            get_console().print()
            get_console().print("[muted]Insight daemon[/]")
            for link in data["recent_links"]:
                source = service.db.get_note(link.source_note_id)
                target = service.db.get_note(link.target_note_id)
                if not source or not target:
                    continue
                get_console().print(
                    f"  [notice]{source.title}[/] ↔ [notice]{target.title}[/] "
                    f"[muted]{link.similarity_score:.0%} · {link.relation_type}[/]"
                )

        pending = data["pending_notes"]
        if pending:
            get_console().print()
            notice(
                f"{len(pending)} catatan menunggu appraisal. "
                "Jalankan 'rph daemon start'."
            )
        if not llm["local_available"]:
            warning("Ollama tidak terdeteksi — jalankan 'rph doctor' untuk detail.")


@app.command("reindex")
def reindex(
    embed: bool = typer.Option(
        True, "--embed/--no-embed", help="Hitung ulang vektor setelah chunking."
    ),
) -> None:
    """Rebuild the index from the vault (Markdown is the source of truth)."""

    with _svc() as service:
        with thinking("Membaca ulang vault..."):
            counts = service.repair() if embed else service.reindex(embed=False)
        report(
            f"{counts.get('indexed', 0)} catatan diindeks dari "
            f"{counts.get('scanned', 0)} file, {counts.get('removed', 0)} entri basi dibuang."
        )
        if embed:
            notice(f"{counts.get('embedded', 0)} chunk diberi vektor.")
        else:
            notice("Vektor dilewati (--no-embed).")


@app.command("doctor")
def doctor() -> None:
    """Diagnose the local stack."""

    import sqlite3

    settings = load_settings()
    settings.ensure_dirs()
    table = Table(box=None, show_edge=False, pad_edge=False)
    table.add_column("Check", style="muted", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Detail", style="muted", overflow="fold")

    def add(check: str, ok: bool, detail: str) -> None:
        table.add_row(check, "[score]ok[/]" if ok else "[warning]warn[/]", detail)

    add("Python", sys.version_info >= (3, 11), sys.version.split()[0])
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.close()
        add("SQLite FTS5", True, "available")
    except sqlite3.Error as exc:
        add("SQLite FTS5", False, str(exc))

    try:
        import sqlite_vec  # type: ignore  # noqa: F401

        add("sqlite-vec", True, "installed (accelerated KNN)")
    except ImportError:
        add("sqlite-vec", False, "not installed; numpy cosine fallback in use")

    with _svc() as service:
        add("Vault", service.settings.vault_dir.exists(), str(service.settings.vault_dir))
        add("Database", service.settings.db_path.exists(), str(service.settings.db_path))
        llm = service.llm.status()
        add(
            "Ollama",
            llm["local_available"],
            f"{llm['host']} · {len(llm['installed'])} model terpasang",
        )
        if llm["local_available"]:
            chat_ok = any(
                llm["chat_model"].split(":")[0] in name for name in llm["installed"]
            )
            embed_ok = any(
                llm["embed_model"].split(":")[0] in name for name in llm["installed"]
            )
            add("Chat model", chat_ok, llm["chat_model"])
            add("Embed model", embed_ok, llm["embed_model"])
        add("Daemon", service.daemon_running(), service.settings.daemon_url)
        add("Cloud fallback", service.settings.cloud.usable, "enabled" if service.settings.cloud.usable else "disabled")

    get_console().print(table)


@app.command("config")
def config(
    key: Optional[str] = typer.Argument(None, help="Optional key to print."),
) -> None:
    """Show resolved configuration and paths."""

    settings = load_settings()
    values = {
        "home": str(settings.home),
        "vault": str(settings.vault_dir),
        "db": str(settings.db_path),
        "config": str(settings.config_file),
        "llm_host": settings.llm_host,
        "chat_model": settings.chat_model,
        "embed_model": settings.embed_model,
        "daemon": settings.daemon_url,
        "link_threshold": str(settings.link_threshold),
        "editor": settings.editor or "(unset)",
    }
    if key:
        if key not in values:
            _fail(f"unknown key: {key}")
            return
        get_console().print(values[key])
        return
    # Plain aligned lines, not a table: a table would wrap long Windows paths
    # mid-directory ("C:\\Users\\Hype" / "GLK\\AppData\\...") and make them unreadable.
    width = max(len(name) for name in values)
    for name, value in values.items():
        get_console().print(f"[muted]{name:<{width}}[/]  {value}")
    get_console().print(
        f"[muted]Edit {settings.config_file} untuk mengubah default.[/]"
    )


@app.command("daemon")
def daemon(
    action: str = typer.Argument("status", help="start | stop | restart | status | logs"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow logs (with 'logs')."),
) -> None:
    """Control the background service."""

    from raphael.daemon.control import daemon_command

    raise typer.Exit(code=daemon_command(action, follow=follow))


@app.command("tui")
def tui() -> None:
    """Open the interactive dashboard (Thought Acceleration)."""

    from raphael.tui import run_tui

    run_tui()


# ----------------------------------------------------------------------
# Chant Annulment: bare text input
# ----------------------------------------------------------------------
def chant(argv: list[str]) -> int:
    """Handle ``rph "<free text>"`` by routing intent, not by flags."""

    force_ask = False
    force_note = False
    words: list[str] = []
    for token in argv:
        if token in {"--ask", "-q"}:
            force_ask = True
        elif token in {"--note", "-n"}:
            force_note = True
        elif token in {"--now", "--analyze"}:
            words.append(token)
        else:
            words.append(token)

    analyze_now = any(word in {"--now", "--analyze"} for word in words)
    words = [word for word in words if word not in {"--now", "--analyze"}]
    text = " ".join(words).strip()

    piped = _read_stdin()
    if piped.strip():
        text = f"{piped.strip()}\n\n{text}".strip() if text else piped.strip()

    if not text:
        notice("Tidak ada input. Coba: rph \"ide baru\" atau rph ask \"...\"")
        return 0

    with _svc() as service:
        intent = "note" if force_note else ("ask" if force_ask else service.route(text))

        if intent == "ask":
            with thinking("Menelusuri vault lokal"):
                result = service.ask(text)
            report(result.answer)
            if result.notes:
                ids = ", ".join(f"[id]{n.id}[/]" for n in result.notes)
                get_console().print(f"   [muted]Sumber: {ids} · {result.elapsed_ms:.0f}ms[/]")
            if result.engine == "heuristic":
                warning("LLM lokal tidak aktif — jawaban dibangun dari catatan mentah.")
            return 0

        try:
            result = service.capture(text, source="chant", appraise=analyze_now, embed=analyze_now)
        except ValueError as exc:
            error(str(exc))
            return 1
        note = result.note
        report(
            f"Catatan disimpan [id]{note.id}[/] ke [tag]#{note.category}[/] "
            f"[muted]({result.elapsed_ms:.0f}ms)[/]"
        )
        get_console().print(f"   [title]{note.title}[/]")
        if note.tags:
            get_console().print(f"   {_tags_text(note.tags)}")
        _print_links(result.links, service)
        if not analyze_now:
            notice("Appraisal dijadwalkan; daemon akan menautkan konteks terkait.")
        return 0


# ----------------------------------------------------------------------
# entrypoint
# ----------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    """Dispatch: known command → Typer, anything else → Chant Annulment."""

    _force_utf8()
    args = list(sys.argv[1:] if argv is None else argv)

    if args and args[0] in {"-h", "--help", "help"}:
        _print_help()
        return 0
    if args and args[0] in {"--version", "-V", "version"}:
        get_console().print(f"RAPHAEL v{__version__}")
        return 0

    if not args:
        piped = _read_stdin()
        if piped.strip():
            return chant([])
        _print_help()
        return 0

    if args[0] in COMMANDS:
        try:
            app(args=args, prog_name="rph", standalone_mode=False)
        except typer.Exit as exc:  # pragma: no cover - normal exit path
            return int(exc.exit_code or 0)
        except KeyboardInterrupt:
            get_console().print()
            return 130
        return 0

    return chant(args)


def _force_utf8() -> None:
    """Emit UTF-8 regardless of the Windows console code page.

    Notes are full of non-ASCII text and the layout uses '·' and '—'. Without
    this, a piped or legacy-codepage console renders them as '?' or '�'.
    """

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):  # pragma: no cover - exotic streams
            pass


def _print_help() -> None:
    console = get_console()
    console.print(f"[title]RAPHAEL[/] [muted]v{__version__}[/] — local-first AI second brain\n")
    console.print("[muted]Chant Annulment[/]")
    console.print('  rph "ide baru"                  simpan catatan (auto-tags oleh daemon)')
    console.print('  rph ask "apa aturan karakternya?"')
    console.print("  cat file.txt | rph              pipa teks langsung ke vault\n")
    console.print("[muted]Commands[/]")
    table = Table(box=None, show_edge=False, pad_edge=False, show_header=False)
    table.add_column(style="cyan", no_wrap=True)
    table.add_column(style="muted")
    for name, desc in [
        ("add <teks>", "paksa simpan ke inbox (--now untuk appraisal sinkron)"),
        ("ingest <file...>", "ekstrak PDF/DOCX/XLSX/MD/TXT (-p proyek)"),
        ("ask <pertanyaan>", "tanya lintas catatan (--chat, --audit)"),
        ("search <kata>", "pencarian instan FTS5 + vektor"),
        ("list / show / graph / rm / open", "kelola catatan"),
        ("synth <id...>", "gabungkan catatan (-o hasil.md|.docx, --save)"),
        ("split <id|tujuan>", "pecah target jadi daftar tugas"),
        ("evolve <aturan>", "tambah aturan permanen (--list, --forget)"),
        ("status", "daily briefing & antrean daemon"),
        ("reindex", "bangun ulang indeks dari vault"),
        ("tui", "dashboard interaktif"),
        ("daemon start|stop|status|logs", "kontrol servis latar belakang"),
        ("doctor / config", "diagnosa & konfigurasi"),
    ]:
        table.add_row(name, desc)
    console.print(table)


if __name__ == "__main__":
    raise SystemExit(main())
