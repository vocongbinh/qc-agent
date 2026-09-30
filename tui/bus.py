"""Event bus: kênh giao tiếp một chiều từ worker thread sang TUI event loop."""

from __future__ import annotations

import asyncio
import io
import sys
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

_AGENT_CONSOLE_BUFS: dict[str, io.StringIO] = {}

# Trần bộ nhớ cho buffer capture của mỗi module. Đo theo ký tự chứ không theo
# dòng: một `console.print` dump JSON dài có thể nhảy nhiều dòng cùng lúc, mà
# cắt theo dòng sẽ phải parse lại nội dung.
AGENT_CONSOLE_MAX_CHARS = 100_000


@dataclass(frozen=True)
class Event:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


_DRAIN_POLL_INTERVAL = 0.001


class EventBus:
    """Bus đẩy event từ thread bất kỳ vào asyncio.Queue của event loop chính."""

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        if loop is None:
            # Bus được tạo trong event loop (app startup) thì tự bind luôn; tạo ở
            # worker thread sẽ không có running loop và bus chưa bind.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
        self.loop = loop
        self.queue: asyncio.Queue[Event] = asyncio.Queue()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def emit(self, event: Event) -> None:
        if self.loop is None or self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, event)
        except RuntimeError:
            pass

    async def drain(self, timeout: float = 0.05) -> None:
        """Đợi queue có phần tử, hoặc hết timeout.

        Không consume event: đây chỉ là cách chờ cho event loop bắt kịp các
        `emit` đang được schedule từ worker thread. Việc đọc event là của TUI.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self.queue.empty():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(_DRAIN_POLL_INTERVAL, remaining))


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_bus() -> None:
    global _bus
    _bus = None


def agent_console_buffer(module_name: str) -> io.StringIO:
    if module_name not in _AGENT_CONSOLE_BUFS:
        _AGENT_CONSOLE_BUFS[module_name] = io.StringIO()
    buf = _AGENT_CONSOLE_BUFS[module_name]
    if len(buf.getvalue()) > AGENT_CONSOLE_MAX_CHARS:
        # Run dài với executor verbose sẽ phình buffer vô hạn → xoá cho sạch.
        buf.seek(0)
        buf.truncate(0)
    return buf


def silence_agent_console() -> None:
    """Chuyển Console của mọi module `agents.*` sang buffer trong bộ nhớ.

    Rich resolve `sys.stdout` lúc ghi nên không thể redirect stdout (sẽ phá
    Textual). Thay object console từng module là cách an toàn.
    """
    for name, mod in list(sys.modules.items()):
        if not name.startswith("agents."):
            continue
        console = getattr(mod, "console", None)
        if not isinstance(console, Console):
            continue
        buf = agent_console_buffer(name)
        mod.console = Console(file=buf, width=200)
