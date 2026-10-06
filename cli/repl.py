from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import Float, FloatContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.table import Table

from agents.assistant import generate_conversational_response
from agents.llm_factory import get_active_provider
from agents.router import route_intent
from config.settings import settings

console = Console()

SLASH_COMMANDS = {
    "/test": "Switch to Test mode (Plan & run tests)",
    "/debug": "Switch to Debug mode (RCA & bug reproduction)",
    "/fix": "Switch to Fix mode (Autonomous code patching)",
    "/model": "Inspect or switch AI model",
    "/login": "Sign in to Google Antigravity / Gemini",
    "/status": "Inspect active provider & model",
    "/help": "View available commands and shortcuts",
    "/clear": "Clear screen",
    "/exit": "Exit QC Agent",
}

REPL_STYLE = Style.from_dict({
    "border": "#38bdf8",
    "prompt": "bold #38bdf8",
    "completion-menu.completion": "bg:#1e1e2e #cdd6f4",
    "completion-menu.completion.current": "bg:#313244 #ffffff bold",
    "completion-menu.meta.completion": "bg:#1e1e2e #6c7086",
    "completion-menu.meta.completion.current": "bg:#313244 #a6adc8",
})

current_mode = "test"


def _format_top_left(mode: str) -> str:
    """Format the top-left status bar dynamically to fit current terminal width."""
    cols = shutil.get_terminal_size().columns
    branch = _get_git_branch()
    cwd = Path.cwd().name
    model = _get_active_model_name()
    status = f"π {model} · ~/{cwd} · {branch}"
    prefix = f"╭─ [{mode.upper()}] ── "
    avail = cols - len(prefix) - 4
    if avail < 8:
        return f"╭─ [{mode.upper()}] "
    if len(status) > avail:
        status = status[:avail - 1] + "…"
    return f"{prefix}{status} "


def _physical_rows(used_columns: int, columns: int) -> int:
    """How many terminal rows a line of `used_columns` occupies after wrapping."""
    if columns <= 0:
        return 1
    if used_columns <= 0:
        return 1
    return -(-used_columns // columns)


def _wrapped_rows_above_cursor(screen: Any, new_width: int, cursor_x: int, cursor_y: int) -> int:
    """Physical rows from the top of the last frame down to the cursor.

    The terminal reflows the previous frame before SIGWINCH is delivered, so
    the cursor sits lower than prompt_toolkit's logical y. Moving up by this
    count lands on the first row of that frame.
    """
    width = max(1, new_width)
    above = 0
    if screen is not None:
        for y in range(min(cursor_y, screen.height)):
            row = screen.data_buffer[y]
            used = max((index + (cell.width or 1) for index, cell in row.items()), default=0)
            above += _physical_rows(used, width)
    return above + max(0, cursor_x) // width


def _border_row(left: Any, right: str) -> VSplit:
    """One border row that stretches with the terminal and never wraps."""
    left_ctrl = FormattedTextControl(left) if callable(left) else FormattedTextControl(str(left))
    return VSplit(
        [
            Window(left_ctrl, height=1, dont_extend_width=True, wrap_lines=False, style="class:border"),
            Window(char="─", height=1, wrap_lines=False, style="class:border"),
            Window(FormattedTextControl(right), height=1, dont_extend_width=True, wrap_lines=False, style="class:border"),
        ],
        height=1,
    )


def prompt_box(mode: str, history: Any, completer: Any) -> str:
    """Three-line input box that stays intact when the terminal is resized.

    Top border, input line and bottom border are one prompt_toolkit layout, so
    the bottom border is visible while typing. On resize the terminal wraps the
    previous frame before SIGWINCH arrives; we move up by the wrapped row count
    and erase before redrawing, instead of trusting the logical 3-row height.
    """
    kb = KeyBindings()
    buf = Buffer(history=history, completer=completer, complete_while_typing=True)

    @kb.add("enter")
    def _on_enter(event: Any) -> None:
        b = event.current_buffer
        if b.complete_state and b.complete_state.current_completion:
            b.apply_completion(b.complete_state.current_completion)
        else:
            event.app.exit(result=b.text)

    @kb.add("tab")
    def _on_tab(event: Any) -> None:
        b = event.current_buffer
        if b.complete_state:
            b.complete_next()
        else:
            b.start_completion(select_first=True)

    @kb.add("escape")
    def _on_esc(event: Any) -> None:
        b = event.current_buffer
        if b.complete_state:
            b.cancel_completion()

    @kb.add("up")
    def _on_up(event: Any) -> None:
        b = event.current_buffer
        if b.complete_state:
            b.complete_previous()
        else:
            b.auto_up()

    @kb.add("down")
    def _on_down(event: Any) -> None:
        b = event.current_buffer
        if b.complete_state:
            b.complete_next()
        else:
            b.auto_down()

    @kb.add("c-c")
    def _on_sigint(event: Any) -> None:
        event.app.exit(exception=KeyboardInterrupt())

    @kb.add("c-d")
    def _on_eof(event: Any) -> None:
        event.app.exit(exception=EOFError())

    top_w = _border_row(lambda: _format_top_left(mode), "╮")
    input_w = Window(
        BufferControl(buffer=buf),
        height=1,
        dont_extend_height=True,
        wrap_lines=False,
        get_line_prefix=lambda line_number, wrap_count: [
            ("class:border", "│ "),
            ("class:prompt", "> "),
        ],
    )
    bottom_w = _border_row("╰", "╯")
    layout_root = FloatContainer(
        content=HSplit([top_w, input_w, bottom_w]),
        floats=[Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=8))],
    )

    app: Application[str] = Application(
        layout=Layout(layout_root),
        key_bindings=kb,
        style=REPL_STYLE,
        full_screen=False,
    )

    def _safe_on_resize() -> None:
        renderer = app.renderer
        out = renderer.output
        screen = renderer._last_screen
        # A resize can land after reset() cleared the previous frame but before
        # the next paint stored one. Erasing from that unknown cursor stacks a
        # new banner on the old one, so only move/erase when the frame is known.
        if screen is not None:
            rows = _wrapped_rows_above_cursor(
                screen,
                out.get_size().columns,
                renderer._cursor_pos.x,
                renderer._cursor_pos.y,
            )
            out.write_raw("\r")
            out.cursor_up(rows)
            out.erase_down()
            out.reset_attributes()
            out.flush()
            renderer._last_screen = None
            renderer._last_size = None
            renderer._cursor_pos = type(renderer._cursor_pos)(0, 0)
        app._request_absolute_cursor_position()
        app._redraw()

    app._on_resize = _safe_on_resize  # type: ignore[method-assign]
    return app.run()


def _get_git_branch() -> str:
    try:
        return subprocess.check_output(
            ["git", "branch", "--show-current"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip() or "main"
    except Exception:
        return "main"


def _get_active_model_name() -> str:
    provider = get_active_provider()
    if provider == "antigravity":
        return getattr(settings, "antigravity_model", "gemini-2.5-flash")
    if provider == "openai":
        return getattr(settings, "default_model", "gpt-4o")
    return "No Provider"


class SlashCommandCompleter(Completer):
    """Auto-complete slash commands with descriptions at the cursor."""

    def get_completions(self, document: Document, complete_event: Any):
        text = document.text_before_cursor
        if text.startswith("/"):
            query = text.strip().lower()
            for cmd, desc in SLASH_COMMANDS.items():
                if cmd.startswith(query):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=cmd,
                        display_meta=desc,
                    )


def _show_help_table() -> None:
    table = Table(title="Available Commands", border_style="#3f3f46", show_header=True)
    table.add_column("Command", style="bold cyan", width=12)
    table.add_column("Description", style="white")
    for cmd, desc in SLASH_COMMANDS.items():
        table.add_row(cmd, desc)
    console.print(table)


def _handle_slash_command(cmd: str) -> bool:
    """Xử lý lệnh slash. Trả về True nếu tiếp tục, False nếu exit."""
    parts = cmd.strip().split(maxsplit=1)
    action = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if action in ("/exit", "/quit"):
        console.print("[dim]Goodbye![/dim]")
        return False

    if action == "/clear":
        os.system("clear")
        return True

    if action == "/help":
        _show_help_table()
        return True

    if action == "/status":
        from main import status as show_status
        show_status()
        return True

    if action == "/login":
        from main import login as do_login
        do_login(provider="antigravity", timeout=120)
        return True

    if action == "/model":
        if arg:
            provider = get_active_provider()
            if provider == "antigravity":
                settings.antigravity_model = arg
                settings.antigravity_planner_model = arg
                settings.antigravity_generator_model = arg
            else:
                settings.default_model = arg
                settings.planner_model = arg
                settings.generator_model = arg
            console.print(f"[green]✔ Model đã đổi thành:[/green] [bold]{arg}[/bold]")
        else:
            console.print(f"[cyan]Active Model:[/cyan] [bold]{_get_active_model_name()}[/bold]")
            console.print("[dim]Để đổi model, gõ: /model <tên_model>[/dim]")
        return True

    global current_mode
    if action in ("/test", "/debug", "/fix"):
        current_mode = action[1:].lower()
        console.print(f"[bold cyan]Chế độ đã chuyển sang:[/bold cyan] [bold]{current_mode.upper()}[/bold]")
        return True

    console.print(f"[yellow]Lệnh không xác định: {action}. Gõ /help để xem trợ giúp.[/yellow]")
    return True


def _run_qc_pipeline(request: str, code_path: str = ".") -> None:
    """Chạy pipeline lập test plan và thực thi qua LangGraph."""
    from agents.graph import qc_graph
    from agents.state import AgentState
    from sandbox.config import load_sandbox_config
    from sandbox.session import sandbox_session

    sandbox_cfg = load_sandbox_config(settings.agent_yaml_path)
    app_root = Path(code_path).resolve()

    with sandbox_session(sandbox_cfg, app_root) as (_db_env, seed_manifest):
        initial_state: AgentState = {
            "user_request": request,
            "documents": [],
            "code_paths": [str(app_root)],
            "openapi_spec": None,
            "messages": [],
            "test_plan": None,
            "generated_tests": [],
            "human_approved": False,
            "execution_result": None,
            "report_path": None,
            "final_summary": None,
            "current_step": "start",
            "error": None,
            "ui_headed": False,
            "shared_context": {},
            "seed_manifest": seed_manifest,
        }

        config = {"configurable": {"thread_id": str(uuid.uuid4())}}

        console.print("\n[bold]▶ Planner đang phân tích...[/bold]")
        plan = None
        for event in qc_graph.stream(initial_state, config, stream_mode="values"):
            step = event.get("current_step", "")
            if step == "planner_done":
                plan = event.get("test_plan")
                if plan:
                    console.print("\n[bold magenta]Test Plan được sinh ra:[/bold magenta]")
                    import json
                    console.print(Syntax(json.dumps(plan, ensure_ascii=False, indent=2), "json", theme="monokai"))
                break
            if event.get("error"):
                console.print(f"[bold red]Lỗi Planner:[/bold red] {event['error']}")
                return

        if not plan:
            return

        approved = Confirm.ask(
            "[bold yellow]Bạn có approve Test Plan này để tiếp tục generate + execute không?[/bold yellow]",
            default=True,
        )
        if not approved:
            console.print("[yellow]Đã hủy theo yêu cầu user.[/yellow]\n")
            return

        qc_graph.update_state(config, {"human_approved": True})
        console.print("[green]✓ Đã approve. Tiếp tục generate + execute...[/green]\n")

        for event in qc_graph.stream(None, config, stream_mode="values"):
            step = event.get("current_step", "")
            if event.get("error"):
                console.print(f"[bold red]Lỗi:[/bold red] {event['error']}")
                return
            if step == "reporter_done":
                break

        console.print("\n[bold green]Xong![/bold green]\n")


def run_repl(code_path: str = ".") -> None:
    """Vòng lặp REPL tương tác chuẩn CLI của QC Agent."""
    history_dir = Path.home() / ".qc-agent"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / "history.txt"
    history = FileHistory(str(history_file))
    completer = SlashCommandCompleter()

    console.print(
        Panel.fit(
            "[bold cyan]Pika · QC Agent Interactive CLI[/bold cyan]\n"
            "[dim]Gõ yêu cầu kiểm thử hoặc dùng / cho danh sách lệnh (/help, /model, /login, /exit)[/dim]",
            border_style="cyan",
        )
    )

    global current_mode
    while True:
        try:
            user_input = prompt_box(
                mode=current_mode,
                history=history,
                completer=completer,
            ).strip()

            if not user_input:
                continue

            if user_input.startswith("/"):
                cont = _handle_slash_command(user_input)
                if not cont:
                    break
                continue

            # Route intent
            with console.status("[bold yellow]⚡ Thinking...[/bold yellow]", spinner="dots"):
                intent = route_intent(user_input)

            if intent == "answer":
                reply = generate_conversational_response(user_input)
                console.print()
                console.print(Markdown(reply))
                console.print()
            else:
                _run_qc_pipeline(user_input, code_path=code_path)

        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            break
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
