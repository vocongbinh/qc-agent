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

app = typer.Typer(help="QC Agent – AI-powered Quality Control Agent (Phase 1+2)")
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


if __name__ == "__main__":
    app()
