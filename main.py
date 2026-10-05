#!/usr/bin/env python3
"""QC Agent – Phase 1–4 (API + UI + Chaos + Perf)+2 CLI entrypoint."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax

from agents.graph import qc_graph
from agents.state import AgentState
from config.settings import settings

app = typer.Typer(
    help="QC Agent – AI-powered Quality Control Agent (CLI headless + TUI dashboard)"
)
console = Console()


def load_documents(paths: list[str]) -> list[str]:
    """Đọc nội dung các file tài liệu."""
    contents = []
    for p in paths:
        path = Path(p)
        if path.exists() and path.is_file():
            contents.append(f"=== {path.name} ===\n{path.read_text(encoding='utf-8')}")
        else:
            console.print(f"[yellow]Warning: không tìm thấy file {p}[/yellow]")
    return contents


@app.command()
def run(
    request: str = typer.Argument(..., help="Mô tả nhiệm vụ test, ví dụ: 'Test API login flow'"),
    docs: Optional[list[str]] = typer.Option(None, "--doc", "-d", help="Đường dẫn file tài liệu (PRD, API doc...)"),
    code: Optional[list[str]] = typer.Option(None, "--code", "-c", help="Đường dẫn source code FE/BE"),
    openapi: Optional[str] = typer.Option(None, "--openapi", help="Đường dẫn file OpenAPI/Swagger"),
    thread_id: Optional[str] = typer.Option(None, help="Thread ID để resume (nâng cao)"),
    headed: bool = typer.Option(False, "--headed", help="Chạy UI test ở chế độ headed (nhìn thấy browser)"),
    ci: bool = typer.Option(False, "--ci", help="CI mode: bỏ human review (dùng cho pipeline)"),
):
    """Chạy QC Agent với yêu cầu của bạn.
    
    Phase 1: Human review Test Plan là BẮT BUỘC.
    """
    console.print(Panel.fit("[bold green]QC Agent – Phase 1–4 (API + UI + Chaos + Perf)[/bold green]", border_style="green"))
    console.print(f"[cyan]Request:[/cyan] {request}\n")
    console.print("[yellow]Human review Test Plan là bắt buộc. Hỗ trợ API + UI.[/yellow]\n")
    from agents.llm_factory import get_active_provider
    if get_active_provider() == "none":
        console.print(
            "[bold red]Thiếu OPENAI_API_KEY hoặc chưa đăng nhập subscription.[/bold red]\n"
            "Tạo file .env ở thư mục gốc và điền:\n"
            "  OPENAI_API_KEY=sk-...\n"
            "Hoặc đăng nhập Google Antigravity:\n"
            "  python main.py login antigravity"
        )
        raise typer.Exit(1)

    # Chuẩn bị input
    documents = load_documents(docs or [])
    openapi_content = None
    if openapi:
        p = Path(openapi)
        if p.exists():
            openapi_content = p.read_text(encoding="utf-8")

    initial_state: AgentState = {
        "user_request": request,
        "documents": documents,
        "code_paths": code or [],
        "openapi_spec": openapi_content,
        "messages": [],
        "test_plan": None,
        "generated_tests": [],
        "human_approved": ci,  # False mặc định; True khi --ci
        "execution_result": None,
        "report_path": None,
        "final_summary": None,
        "current_step": "start",
        "error": None,
        "ui_headed": headed,
        "shared_context": {},
    }

    config = {
        "configurable": {
            "thread_id": thread_id or str(uuid.uuid4()),
        }
    }

    # Chạy đến human review (nếu có)
    console.print("[bold]▶ Planner đang phân tích...[/bold]")
    for event in qc_graph.stream(initial_state, config, stream_mode="values"):
        step = event.get("current_step", "")
        if step == "planner_done":
            plan = event.get("test_plan")
            if plan:
                console.print("\n[bold magenta]Test Plan được sinh ra:[/bold magenta]")
                console.print(Syntax(json.dumps(plan, ensure_ascii=False, indent=2), "json", theme="monokai"))
            break
        if event.get("error"):
            console.print(f"[bold red]Lỗi Planner:[/bold red] {event['error']}")
            raise typer.Exit(1)

    # Human review – BẮT BUỘC ở Phase 1
    if ci:
        qc_graph.update_state(config, {"human_approved": True})
        console.print("[green]✓ CI mode – skip human review[/green]\n")
    else:
        console.print()
        approved = Confirm.ask(
            "[bold yellow]Bạn có approve Test Plan này để tiếp tục generate + execute không?[/bold yellow]",
            default=True,
        )
        if not approved:
            console.print("[yellow]Đã hủy theo yêu cầu user. Test Plan chưa được thực thi.[/yellow]")
            raise typer.Exit(0)
        qc_graph.update_state(config, {"human_approved": True})
        console.print("[green]✓ Đã approve. Tiếp tục generate + execute...[/green]\n")

    # Tiếp tục chạy phần còn lại
    for event in qc_graph.stream(None, config, stream_mode="values"):
        step = event.get("current_step", "")
        if event.get("error"):
            console.print(f"[bold red]Lỗi:[/bold red] {event['error']}")
            raise typer.Exit(1)
        if step == "reporter_done":
            break

    console.print("\n[bold green]Xong![/bold green]")


@app.command()
def version():
    """Hiển thị phiên bản."""
    console.print("QC Agent Phase 1 – LangGraph foundation")


@app.command()
def tui():
    """Mở giao diện TUI dashboard."""
    from agents.llm_factory import get_active_provider
    if get_active_provider() == "none":
        console.print(
            "[bold red]Thiếu OPENAI_API_KEY hoặc chưa đăng nhập subscription.[/bold red]\n"
            "Tạo file .env ở thư mục gốc và điền:\n"
            "  OPENAI_API_KEY=sk-...\n"
            "Hoặc đăng nhập Google Antigravity:\n"
            "  python main.py login antigravity"
        )
        raise typer.Exit(1)

    # Import trễ: `main.py run` không được trả giá import Textual, và phải chạy
    # được cả khi Textual chưa cài.
    from tui.app import QCTApp
    from tui.bus import reset_bus

    # Bus là singleton module-level — state của lần chạy trước trong cùng
    # process sẽ rò sang lần này nếu không reset.
    reset_bus()
    QCTApp().run()


@app.command()
def login(
    provider: str = typer.Argument("antigravity", help="Provider cần đăng nhập: 'antigravity'"),
    timeout: int = typer.Option(120, help="Thời gian chờ xác thực trên trình duyệt (giây)"),
):
    """Đăng nhập tài khoản subscription (Google Antigravity / Gemini) không cần API key."""
    if provider.lower() in ("antigravity", "gemini", "google"):
        from auth.antigravity import run_antigravity_login
        console.print(Panel.fit("[bold green]Đăng nhập Google Antigravity (Subscription)[/bold green]", border_style="green"))
        try:
            creds = run_antigravity_login(timeout=timeout)
            email = creds.get("email") or "thành công"
            project_id = creds.get("project_id") or "mặc định"
            console.print(f"[bold green]✔ Đăng nhập thành công:[/bold green] {email}")
            console.print(f"[cyan]Project ID:[/cyan] {project_id}")
            console.print("[dim]Credentials đã được lưu tại ~/.qc-agent/credentials.json[/dim]")
        except Exception as e:
            console.print(f"[bold red]Đăng nhập thất bại:[/bold red] {e}")
            raise typer.Exit(1)
    else:
        console.print(f"[bold red]Provider không được hỗ trợ:[/bold red] {provider}. Hiện hỗ trợ: 'antigravity'")
        raise typer.Exit(1)


@app.command()
def status():
    """Kiểm tra trạng thái đăng nhập và cấu hình LLM."""
    from agents.llm_factory import get_active_provider
    from auth.antigravity import get_valid_antigravity_credentials

    active = get_active_provider()
    console.print(f"[bold]Provider đang hoạt động:[/bold] [cyan]{active}[/cyan]")

    creds = get_valid_antigravity_credentials()
    if creds:
        email = creds.get("email") or "Unknown"
        project_id = creds.get("project_id") or "Auto"
        console.print(f"  [green]✔ Google Antigravity:[/green] Đã đăng nhập ({email}) - Project: {project_id}")
    else:
        console.print("  [dim]• Google Antigravity: Chưa đăng nhập[/dim]")

    if settings.openai_api_key:
        masked = settings.openai_api_key[:6] + "..." + settings.openai_api_key[-4:]
        console.print(f"  [green]✔ OpenAI API Key:[/green] {masked}")
    else:
        console.print("  [dim]• OpenAI API Key: Không tìm thấy trong .env[/dim]")

@app.command(name="index")
def index_cmd(
    root: str = typer.Option(".", "--root", "-r", help="Thư mục repo cần index"),
    lang: str = typer.Option("go", "--lang", "-l", help="Ngôn ngữ mục tiêu (hiện tại: go)"),
    db: Optional[str] = typer.Option(None, "--db", "-d", help="Đường dẫn lưu KùzuDB (mặc định theo settings)"),
):
    """Xây dựng Code Intelligence Graph vào KùzuDB."""
    if lang.lower() != "go":
        console.print(f"[bold red]Lỗi:[/bold red] Ngôn ngữ '{lang}' chưa được hỗ trợ ở Phase 1. Vui lòng chọn '--lang go'.")
        raise typer.Exit(1)

    from codeintel.indexer.builder import build_index
    from config.settings import settings

    db_target = Path(db) if db else settings.codeintel_db_path
    console.print(f"[bold cyan]Đang index repo {root} (ngôn ngữ: {lang}, db: {db_target})...[/bold cyan]")
    try:
        stats = build_index(root, db_target)
        console.print(f"[bold green]Index thành công![/bold green] Files: {stats['files_indexed']}, Functions: {stats['functions_indexed']}, Calls: {stats['calls_recorded']}")
    except Exception as exc:
        console.print(f"[bold red]Lỗi khi index:[/bold red] {exc}")
        raise typer.Exit(1)

if __name__ == "__main__":
    app()
