"""Timeline Renderables for OpenCode / Claude Code Session Experience.

Cung cấp các khối giao diện dạng Card thời gian thực:
- User Prompt Card (❯ User)
- Thinking Block (⚡ Thinking với các bước suy luận)
- Test Plan Card (📋 Kế hoạch kiểm thử với phím tắt duyệt inline)
- Tool Execution Card (⚙ Tool thực thi Playwright, Docker, API, k6)
- Execution Summary Card (🏁 Báo cáo tổng kết)
"""

from __future__ import annotations

from typing import Any, Optional

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def create_user_prompt_panel(prompt: str) -> Panel:
    """Render khối prompt của user."""
    content = Text(f"{prompt.strip()}", style="bold white")
    return Panel(
        content,
        title="[bold cyan]❯ User Request[/bold cyan]",
        title_align="left",
        border_style="cyan",
        padding=(0, 1),
    )

def create_assistant_message_panel(content: str) -> Panel:
    """Render phản hồi trò chuyện của Assistant."""
    from rich.markdown import Markdown
    return Panel(
        Markdown(content),
        title="[bold green]🤖 QC Agent[/bold green]",
        title_align="left",
        border_style="green",
        padding=(0, 1),
    )


def create_thinking_panel(title: str, steps: list[str]) -> Panel:
    """Render khối suy luận (Thinking Stream)."""
    text = Text()
    text.append(f"{title}\n", style="italic cyan")
    for step in steps:
        text.append(f"  ● {step}\n", style="dim white")
    return Panel(
        text,
        title="[bold yellow]⚡ Thinking[/bold yellow]",
        title_align="left",
        border_style="yellow",
        padding=(0, 1),
    )


def create_plan_card_panel(plan: dict[str, Any], count: int) -> Panel:
    """Render card kế hoạch kiểm thử inline."""
    title = str(plan.get("title") or "Test Plan")
    cases = list(plan.get("test_cases") or [])

    table = Table(show_header=True, header_style="bold magenta", box=None, padding=(0, 1))
    table.add_column("Status", width=8)
    table.add_column("ID", width=16)
    table.add_column("Priority", width=10)
    table.add_column("Type", width=8)
    table.add_column("Title")

    for case in cases[:10]:
        cid = str(case.get("id") or "")
        prio = str(case.get("priority") or "medium")
        ctype = str(case.get("type") or "api")
        ctitle = str(case.get("title") or "")
        
        prio_style = "bold red" if prio == "critical" else ("yellow" if prio == "high" else "dim")
        table.add_row(
            Text("[✓]", style="green"),
            Text(cid, style="bold cyan"),
            Text(prio, style=prio_style),
            Text(ctype, style="magenta"),
            Text(ctitle),
        )

    if len(cases) > 10:
        table.add_row(Text("...", style="dim"), Text("...", style="dim"), Text("", style="dim"), Text("", style="dim"), Text(f"... and {len(cases) - 10} more cases", style="dim italic"))

    footer_hint = Text("\n👉 Press Enter ↵ to Approve & Run  ·  Esc to Cancel  ·  Space for options", style="bold green")

    content = Table.grid()
    content.add_row(Text(f"Plan: {title} · {count} test cases formulated\n", style="bold"))
    content.add_row(table)
    content.add_row(footer_hint)

    return Panel(
        content,
        title="[bold green]📋 Proposed Test Plan[/bold green]",
        title_align="left",
        border_style="green",
        padding=(0, 1),
    )


def create_tool_card_panel(result: dict[str, Any]) -> Panel:
    """Render card thực thi tool kiểm thử (Playwright, Docker, API, k6)."""
    tool_type = str(result.get("type") or "api").lower()
    tool_name = f"{tool_type}_executor"
    cid = str(result.get("id") or "")
    title = str(result.get("title") or cid)
    status = str(result.get("status") or "unknown").lower()
    duration = result.get("duration_ms", 0)
    err = result.get("error_message")

    status_color = "green" if status == "passed" else ("bold red" if status in ("failed", "error") else "yellow")
    status_icon = "✓ PASSED" if status == "passed" else ("✗ FAILED" if status in ("failed", "error") else f"□ {status.upper()}")

    grid = Table.grid(padding=(0, 1))
    grid.add_row(Text(f"Test Case: {cid} — {title}", style="bold"))
    
    status_line = Text(f"Result: {status_icon} ({round(float(duration or 0))}ms)", style=status_color)
    grid.add_row(status_line)

    if err:
        grid.add_row(Text(f"Error: {err}", style="red italic"))

    border_color = "green" if status == "passed" else ("red" if status in ("failed", "error") else "yellow")

    return Panel(
        grid,
        title=f"[bold blue]⚙ Tool: {tool_name}[/bold blue]",
        title_align="left",
        border_style=border_color,
        padding=(0, 1),
    )


def create_summary_panel(summary: str, report_path: str) -> Panel:
    """Render card tổng kết kết quả chạy."""
    grid = Table.grid(padding=(0, 1))
    grid.add_row(Text(summary or "Run completed.", style="bold white"))
    if report_path:
        grid.add_row(Text(f"Report: {report_path}", style="dim cyan"))

    return Panel(
        grid,
        title="[bold green]🏁 Execution Summary[/bold green]",
        title_align="left",
        border_style="green",
        padding=(0, 1),
    )
