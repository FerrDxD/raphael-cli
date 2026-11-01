"""Terminal output layer: the voice of Raphael.

Every message is prefixed with a system label — no chatbot pleasantries
(DESIGN.md §1, "Zero-Fluff Output").
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme

THEME = Theme(
    {
        "report": "bold cyan",
        "notice": "bold magenta",
        "warning": "bold yellow",
        "synthesis": "bold green",
        "error": "bold red",
        "muted": "dim white",
        "id": "cyan",
        "tag": "magenta",
        "title": "bold white",
        "score": "green",
        "label.report": "bold cyan",
        "label.notice": "bold magenta",
        "label.warning": "bold yellow",
        "label.synthesis": "bold green",
        "label.error": "bold red",
    }
)

LABELS: dict[str, str] = {
    "report": "Report",
    "notice": "Notice",
    "warning": "Warning",
    "synthesis": "Synthesis",
    "error": "Error",
}

_console: Console | None = None


def get_console() -> Console:
    """Return the shared Rich console (stdout, theme applied)."""

    global _console
    if _console is None:
        # legacy_windows=False keeps ANSI styling on Windows terminals instead of
        # the cp437 fallback, which would mangle box drawing and accents.
        _console = Console(
            theme=THEME, highlight=False, soft_wrap=True, legacy_windows=False
        )
    return _console


def get_stderr_console() -> Console:
    return Console(theme=THEME, highlight=False, stderr=True, legacy_windows=False)


def set_console(console: Console | None) -> None:
    """Allow the TUI/tests to swap in a different console."""

    global _console
    _console = console


def _emit(kind: str, message: str, console: Console | None = None) -> None:
    target = console or get_console()
    label = LABELS.get(kind, kind.capitalize())
    target.print(f"[label.{kind}]\\[{label}][/] {message}")


def report(message: str, console: Console | None = None) -> None:
    _emit("report", message, console)


def notice(message: str, console: Console | None = None) -> None:
    _emit("notice", message, console)


def warning(message: str, console: Console | None = None) -> None:
    _emit("warning", message, console)


def synthesis(message: str, console: Console | None = None) -> None:
    _emit("synthesis", message, console)


def error(message: str, console: Console | None = None) -> None:
    _emit("error", message, console)


@contextmanager
def thinking(label: str = "Processing locally") -> Iterator[None]:
    """Spinner shown only on a real terminal, silent when piped."""

    console = get_console()
    if not console.is_terminal:
        yield
        return
    with console.status(f"[muted]{label}...[/]", spinner="dots"):
        yield


def render_markdown(text: str) -> None:
    get_console().print(Markdown(text))


def kv_table(title: str, rows: list[tuple[str, str]]) -> Table:
    table = Table(title=title, show_header=False, box=None, pad_edge=False)
    table.add_column(style="muted", no_wrap=True)
    table.add_column(style="white")
    for key, value in rows:
        table.add_row(key, value)
    return table


def notes_table(rows: list[dict[str, Any]], *, show_score: bool = False) -> Table:
    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("ID", style="id", no_wrap=True)
    table.add_column("Title", style="title", overflow="fold")
    table.add_column("Tags", style="tag", overflow="fold")
    if show_score:
        table.add_column("Score", style="score", justify="right", no_wrap=True)
    for row in rows:
        cells = [
            str(row.get("id", "")),
            str(row.get("title", "")),
            ", ".join(row.get("tags", []) or []),
        ]
        if show_score:
            cells.append(f"{float(row.get('score', 0.0)):.2f}")
        table.add_row(*cells)
    return table


def panel(body: str, title: str = "Raphael", border: str = "cyan") -> None:
    get_console().print(Panel(body, title=title, border_style=border, expand=False))
