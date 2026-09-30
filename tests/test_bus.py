from __future__ import annotations

import asyncio
import io
import sys
import threading
import types

import pytest
from rich.console import Console

from tui.bus import Event, EventBus, get_bus, reset_bus, silence_agent_console


def test_event_is_frozen():
    ev = Event(kind="log", payload={"text": "hi"})
    with pytest.raises(Exception):
        ev.kind = "other"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_emit_then_drain_preserves_order():
    bus = EventBus()
    for i in range(5):
        bus.emit(Event(kind="log", payload={"text": str(i)}))
    await bus.drain(0.05)
    drained = []
    while not bus.queue.empty():
        drained.append(bus.queue.get_nowait())
    assert [e.payload["text"] for e in drained] == ["0", "1", "2", "3", "4"]


@pytest.mark.asyncio
async def test_emit_from_thread_is_thread_safe():
    loop = asyncio.get_running_loop()
    bus = EventBus(loop=loop)
    N = 200

    def worker():
        for i in range(N):
            bus.emit(Event(kind="log", payload={"i": i}))

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    await bus.drain(0.2)
    count = bus.queue.qsize()
    assert count == N, f"expected {N} events, got {count}"


@pytest.mark.asyncio
async def test_drain_stops_early_when_queue_empty():
    bus = EventBus()
    bus.emit(Event(kind="log", payload={}))
    await bus.drain(0.1)
    await bus.drain(0.01)
    assert bus.queue.qsize() == 1


def test_emit_without_running_loop_is_noop():
    """emit() ngoài event loop không được raise (worker thread chưa có loop)."""
    bus = EventBus(loop=None)
    bus.emit(Event(kind="log", payload={"text": "orphan"}))  # không raise


def test_get_bus_is_singleton():
    reset_bus()
    a = get_bus()
    b = get_bus()
    assert a is b
    reset_bus()


def test_silence_agent_console_replaces_agent_consoles():
    mod = types.ModuleType("agents.fake_executor")
    mod.console = Console()
    sys.modules["agents.fake_executor"] = mod
    real_stdout = sys.stdout
    captured = io.StringIO()
    sys.stdout = captured
    try:
        original = mod.console
        silence_agent_console()

        assert mod.console is not original, "phải thay object console"
        mod.console.print("SHOULD_NOT_APPEAR")
        assert "SHOULD_NOT_APPEAR" not in captured.getvalue()
    finally:
        sys.stdout = real_stdout
        del sys.modules["agents.fake_executor"]


def test_silence_agent_console_is_idempotent():
    mod = types.ModuleType("agents.fake2")
    mod.console = Console()
    sys.modules["agents.fake2"] = mod
    try:
        silence_agent_console()
        first = mod.console.file
        silence_agent_console()
        assert mod.console.file is first
    finally:
        del sys.modules["agents.fake2"]


def test_silence_ignores_non_agent_modules():
    mod = types.ModuleType("some_other_pkg")
    mod.console = Console()
    sys.modules["some_other_pkg"] = mod
    try:
        original = mod.console
        silence_agent_console()
        assert mod.console is original
    finally:
        del sys.modules["some_other_pkg"]
