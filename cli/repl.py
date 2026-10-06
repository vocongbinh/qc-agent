from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
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
    "prompt": "bold #38bdf8",
    "completion-menu.completion": "bg:#1e1e2e #cdd6f4",
    "completion-menu.completion.current": "bg:#313244 #ffffff bold",
    "completion-menu.meta.completion": "bg:#1e1e2e #6c7086",
    "completion-menu.meta.completion.current": "bg:#313244 #a6adc8",
    "bottom-toolbar": "#a6adc8 bg:#181825",
})


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


def _bottom_toolbar() -> str:
    branch = _get_git_branch()
    cwd = Path.cwd().name
    model = _get_active_model_name()
    return f" π ☯ {model} · ~/{cwd} · ⎇ {branch} · Ready"


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

    if action in ("/test", "/debug", "/fix"):
        console.print(f"[bold cyan]Chế độ:[/bold cyan] {action[1:].upper()}")
        console.print("[dim]Mô tả yêu cầu để bắt đầu kiểm thử hoặc sửa lỗi.[/dim]")
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

    session: PromptSession[str] = PromptSession(
        history=FileHistory(str(history_file)),
        completer=SlashCommandCompleter(),
        style=REPL_STYLE,
    )

    console.print(
        Panel.fit(
            "[bold cyan]Pika · QC Agent Interactive CLI[/bold cyan]\n"
            "[dim]Gõ yêu cầu kiểm thử hoặc dùng / cho danh sách lệnh (/help, /model, /login, /exit)[/dim]",
            border_style="cyan",
        )
    )

    while True:
        try:
            user_input = session.prompt(
                "❯ ",
                bottom_toolbar=_bottom_toolbar,
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
