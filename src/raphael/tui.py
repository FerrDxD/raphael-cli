"""``rph tui`` — Thought Acceleration dashboard.

Three panels (PRD §5.D):
1. Left   — projects, tags, and the note list.
2. Centre — the selected Markdown note.
3. Right  — the Raphael stream: ``[Report]`` / ``[Notice]`` in real time.

Keyboard-first, Vim-flavoured (DESIGN.md §1.4): j/k or arrows to move, Enter to
open, / to filter, r to reload, q to quit.
"""

from __future__ import annotations

from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, RichLog, Static

from raphael.console import THEME
from raphael.config import load_settings
from raphael.models import Note
from raphael.service import Raphael


class RaphaelTUI(App):
    """Full-screen terminal dashboard."""

    CSS = """
    Screen { background: $surface; }
    #body { height: 1fr; }
    #sidebar { width: 32; border-right: solid $primary-darken-2; }
    #center { width: 1fr; border-right: solid $primary-darken-2; }
    #stream { width: 42; }
    .panel-title {
        text-style: bold;
        color: $accent;
        padding: 0 1;
        background: $panel;
    }
    #notes { height: 1fr; background: $surface; }
    #filter { dock: bottom; display: none; }
    #filter.visible { display: block; }
    #note-view { padding: 0 1; height: 1fr; overflow-y: auto; }
    #meta { color: $text-muted; padding: 0 1; height: auto; }
    #stream-log { height: 1fr; padding: 0 1; }
    ListItem { padding: 0 1; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("enter", "open_note", "Open"),
        ("slash", "search", "Filter"),
        ("r", "reload", "Reload"),
        ("g", "focus_stream", "Stream"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.service: Raphael | None = None
        self._notes: list[Note] = []
        self._current: Note | None = None

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("Notes", classes="panel-title")
                yield ListView(id="notes")
                yield Input(placeholder="filter...", id="filter")
            with Vertical(id="center"):
                yield Static("Note", classes="panel-title")
                yield Static(id="meta")
                yield Static(id="note-view")
            with Vertical(id="stream"):
                yield Static("Raphael Stream", classes="panel-title")
                yield RichLog(id="stream-log", wrap=True, markup=True, highlight=False)
        yield Footer()

    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        try:
            self.service = Raphael(load_settings())
        except Exception as exc:  # noqa: BLE001
            self.exit(message=f"cannot open vault: {exc}")
            return

        self._stream("[report]RAPHAEL[/] siap. j/k navigasi · enter buka · / filter · r reload")
        self.action_reload()
        notes = self.query_one("#notes", ListView)
        notes.focus()

    def on_unmount(self) -> None:
        if self.service is not None:
            self.service.close()

    # ------------------------------------------------------------------
    # data
    # ------------------------------------------------------------------
    def action_reload(self) -> None:
        if self.service is None:
            return
        try:
            self._notes = self.service.recent_notes(limit=100)
        except Exception as exc:  # noqa: BLE001
            self._stream(f"[error]gagal memuat catatan:[/] {exc}")
            return
        self._render_list(self._notes)
        self._stream(
            f"[report]Vault[/] {len(self._notes)} catatan · "
            f"{self.service.db.count_notes()} terindeks"
        )
        if self._notes:
            self.query_one("#notes", ListView).index = 0
            self._show_note(self._notes[0])

    def _render_list(self, notes: list[Note]) -> None:
        view = self.query_one("#notes", ListView)
        view.clear()
        for note in notes:
            label = Text()
            label.append(note.id, style="cyan")
            label.append("  ")
            label.append(note.title or "(untitled)", style="bold")
            view.append(ListItem(Label(label)))

    def _filter(self, query: str) -> None:
        if self.service is None:
            return
        query = query.strip()
        if not query:
            self._render_list(self._notes)
            return
        try:
            hits = self.service.search(query, limit=40)
        except Exception as exc:  # noqa: BLE001
            self._stream(f"[error]pencarian gagal:[/] {exc}")
            return
        notes = [n for n in (self.service.db.get_note(h.note_id) for h in hits) if n is not None]
        self._render_list(notes)
        self._stream(f"[report]Filter[/] '{query}' → {len(notes)} hasil")

    # ------------------------------------------------------------------
    # note rendering
    # ------------------------------------------------------------------
    def _show_note(self, note: Note) -> None:
        if self.service is None:
            return
        self._current = note
        meta = self.query_one("#meta", Static)
        stamp = note.updated_at.strftime("%d %b %Y %H:%M") if note.updated_at else "-"
        tags = " ".join(f"#{tag}" for tag in note.tags)
        meta.update(Text.from_markup(f"[cyan]{note.id}[/]  [dim]{stamp}[/]  [magenta]{tags}[/]"))

        body = self.query_one("#note-view", Static)
        body.update(RichMarkdown(note.body or "_(kosong)_"))

        self._stream(f"[report]Buka[/] {note.id} — {note.title}")
        links = self.service.links_for(note.id, limit=3)
        for link in links:
            other_id = (
                link.target_note_id if link.source_note_id == note.id else link.source_note_id
            )
            other = self.service.db.get_note(other_id)
            if other is None:
                continue
            self._stream(
                f"[notice]Terkait[/] {other.title} "
                f"[dim]{link.similarity_score:.0%} · {link.relation_type}[/]"
            )
        if note.appraisal_status == "pending":
            self._stream("[warning]Menunggu appraisal[/] jalankan `rph daemon start`.")

    def _stream(self, markup: str) -> None:
        try:
            self.query_one("#stream-log", RichLog).write(Text.from_markup(markup))
        except Exception:  # noqa: BLE001 - stream must never break the UI
            pass

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        index = event.item.list_view.index if event.item.list_view else None
        visible = self._visible_notes()
        if index is not None and 0 <= index < len(visible):
            self._show_note(visible[index])

    def _visible_notes(self) -> list[Note]:
        view = self.query_one("#notes", ListView)
        notes: list[Note] = []
        for item in view.children:
            label = item.query_one(Label)
            text = label.renderable
            raw = text.plain if isinstance(text, Text) else str(text)
            note_id = raw.split("  ")[0].strip()
            note = next((n for n in self._notes if n.id == note_id), None)
            if note is not None:
                notes.append(note)
        return notes

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_open_note()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self._filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter":
            event.input.remove_class("visible")
            self.query_one("#notes", ListView).focus()

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def action_cursor_down(self) -> None:
        view = self.query_one("#notes", ListView)
        if view.index is None:
            view.index = 0
        elif view.index < len(view.children) - 1:
            view.index += 1

    def action_cursor_up(self) -> None:
        view = self.query_one("#notes", ListView)
        if view.index is None:
            view.index = 0
        elif view.index > 0:
            view.index -= 1

    def action_open_note(self) -> None:
        if self._current is None:
            return
        self._show_note(self._current)

    def action_search(self) -> None:
        box = self.query_one("#filter", Input)
        box.add_class("visible")
        box.focus()

    def action_focus_stream(self) -> None:
        self.query_one("#stream-log", RichLog).focus()


def run_tui() -> None:
    """Entry point used by ``rph tui``."""

    app = RaphaelTUI()
    app.run()
