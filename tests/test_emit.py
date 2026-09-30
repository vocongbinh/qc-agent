from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import threading
from typing import Any

import pytest

from tui.bus import Event, EventBus


class RecordingBus:
    """Bus stub gom event vào list, an toàn khi emit từ thread bất kỳ.

    `EventBus` thật đẩy qua `loop.call_soon_threadsafe`; bus dựng ngoài running
    loop sẽ có `loop=None` và `emit()` im lặng bỏ qua. Assert vào queue của
    bus đó là assert vào cái rỗng — nên test emit đổi bus này, và chỉ một test
    duy nhất chạm bus thật trên loop thật.
    """

    def __init__(self) -> None:
        self.events: list[Event] = []
        self._lock = threading.Lock()

    def emit(self, event: Event) -> None:
        with self._lock:
            self.events.append(event)

    @property
    def kinds(self) -> list[str]:
        with self._lock:
            return [e.kind for e in self.events]

    def only(self) -> Event:
        assert len(self.events) == 1, f"expected exactly 1 event, got {self.kinds}"
        return self.events[0]


@pytest.fixture(autouse=True)
def _reset_emitter_bus():
    """Mọi test bắt đầu với bus None — không rò event giữa các test."""
    from agents import emitter

    emitter.set_bus(None)
    yield
    emitter.set_bus(None)


def _full_result(**over: Any) -> dict[str, Any]:
    base = {
        "id": "TC_LOGIN_001",
        "title": "Login thành công",
        "type": "api",
        "status": "passed",
        "duration_ms": 12.5,
        "error_message": None,
    }
    base.update(over)
    return base


_EXPECTED_KEYS = {
    "id",
    "title",
    "type",
    "status",
    "duration_ms",
    "error_message",
}


# ---------------------------------------------------------------------------
# emitter.py — bus optional
# ---------------------------------------------------------------------------

def test_no_bus_is_noop():
    from agents import emitter

    assert emitter.get_bus() is None
    emitter.emit_test_result(_full_result())  # CLI path: không được ném lỗi
    assert emitter.get_bus() is None


def test_set_bus_get_bus_round_trip_and_reset():
    from agents import emitter

    bus = RecordingBus()
    emitter.set_bus(bus)
    assert emitter.get_bus() is bus
    emitter.set_bus(None)
    assert emitter.get_bus() is None


def test_bus_receives_exactly_one_test_result_with_full_payload():
    from agents import emitter

    bus = RecordingBus()
    emitter.set_bus(bus)

    emitter.emit_test_result(_full_result())

    assert bus.kinds == ["test_result"]
    payload = bus.only().payload
    assert set(payload) == _EXPECTED_KEYS
    assert payload == _full_result()


def test_payload_is_snapshot_not_live_reference():
    """Sau emit, sửa res không được đổi event đã gửi đi."""
    from agents import emitter

    bus = RecordingBus()
    emitter.set_bus(bus)
    res = _full_result()
    emitter.emit_test_result(res)
    res["status"] = "failed"
    res["id"] = "KHAC"
    assert bus.only().payload["status"] == "passed"
    assert bus.only().payload["id"] == "TC_LOGIN_001"


def test_missing_keys_become_none():
    from agents import emitter

    bus = RecordingBus()
    emitter.set_bus(bus)
    emitter.emit_test_result({"id": "X"})
    assert bus.only().payload == {
        "id": "X",
        "title": None,
        "type": "api",
        "status": None,
        "duration_ms": None,
        "error_message": None,
    }


def test_sparse_result_defaults_type_to_api():
    from agents import emitter

    bus = RecordingBus()
    emitter.set_bus(bus)
    emitter.emit_test_result({"id": "X"})
    assert bus.only().payload["type"] == "api"


def test_explicit_none_type_falls_back_to_api():
    from agents import emitter

    bus = RecordingBus()
    emitter.set_bus(bus)
    emitter.emit_test_result({"id": "X", "type": None})
    assert bus.only().payload["type"] == "api"


@pytest.mark.parametrize("t", ["api", "integration", "ui", "chaos", "performance"])
def test_present_type_is_preserved(t: str):
    from agents import emitter

    bus = RecordingBus()
    emitter.set_bus(bus)
    emitter.emit_test_result({"id": "X", "type": t})
    assert bus.only().payload["type"] == t


def test_error_result_payload_carries_message():
    from agents import emitter

    bus = RecordingBus()
    emitter.set_bus(bus)
    emitter.emit_test_result(
        _full_result(type="ui", status="failed", error_message="timeout 30s", duration_ms=30000.0)
    )
    payload = bus.only().payload
    assert payload["status"] == "failed"
    assert payload["error_message"] == "timeout 30s"


@pytest.mark.asyncio
async def test_real_event_bus_delivers_on_running_loop():
    """Bus thật + loop thật: event phải tới queue sau khi loop chạy.

    `emit` chỉ `call_soon_threadsafe`, nên phải `await` để loop thực thi
    callback trước khi đọc queue.
    """
    from agents import emitter

    bus = EventBus()  # dựng TRONG running loop -> có loop
    assert bus.loop is asyncio.get_running_loop()
    emitter.set_bus(bus)

    emitter.emit_test_result(_full_result(type="ui", status="passed"))

    await bus.drain(timeout=1.0)
    assert bus.queue.qsize() == 1
    ev = bus.queue.get_nowait()
    assert ev.kind == "test_result"
    assert ev.payload["type"] == "ui"
    assert ev.payload["id"] == "TC_LOGIN_001"


# ---------------------------------------------------------------------------
# Executor wiring — hành vi thật với bus giả
# ---------------------------------------------------------------------------

def _canned(res_id: str, status: str = "passed", duration_ms: float = 7.5) -> dict[str, Any]:
    return {
        "id": res_id,
        "title": f"title of {res_id}",
        "status": status,
        "error_message": None if status == "passed" else "boom",
        "actual_result": "ok",
        "duration_ms": duration_ms,
        # `type` cố tình vắng: executor phải tự setdefault trước khi emit.
    }


def test_api_executor_emits_one_result_per_executed_test(monkeypatch):
    from agents import api_executor, emitter

    ran: list[str] = []

    def fake_run(test: dict, base_url: str) -> dict:
        ran.append(test["id"])
        return _canned(test["id"])

    monkeypatch.setattr(api_executor, "_run_single_api_test", fake_run)

    bus = RecordingBus()
    emitter.set_bus(bus)

    out = api_executor.api_executor_node(
        {
            "generated_tests": [
                {"id": "TC-1", "type": "api"},
                {"id": "TC-2", "type": "integration"},
                {"id": "TC-3", "type": "ui"},  # bị skip, không emit
            ]
        }
    )

    assert ran == ["TC-1", "TC-2"]
    assert bus.kinds == ["test_result", "test_result"]
    assert [e.payload["id"] for e in bus.events] == ["TC-1", "TC-2"]
    assert [e.payload["type"] for e in bus.events] == ["api", "integration"]
    assert [e.payload["status"] for e in bus.events] == ["passed", "passed"]
    # CLI vẫn phải cho ra kết quả đầy đủ như trước
    assert out["execution_result"]["total"] == 2
    assert out["execution_result"]["passed"] == 2
    assert [d["id"] for d in out["execution_result"]["details"]] == ["TC-1", "TC-2"]


def test_api_executor_emits_integration_type_preserved(monkeypatch):
    """Integration case chạy qua API executor vẫn phải mang type gốc."""
    from agents import api_executor, emitter

    monkeypatch.setattr(api_executor, "_run_single_api_test", lambda test, base_url: _canned(test["id"]))

    bus = RecordingBus()
    emitter.set_bus(bus)

    api_executor.api_executor_node(
        {"generated_tests": [{"id": "TC-INT", "type": "integration"}]}
    )

    assert bus.kinds == ["test_result"]
    payload = bus.only().payload
    assert payload["id"] == "TC-INT"
    assert payload["type"] == "integration"


def test_api_executor_emits_api_type_when_test_has_no_type(monkeypatch):
    """Thiếu type = api, đúng như `ttype` mà executor tự tính để route."""
    from agents import api_executor, emitter

    monkeypatch.setattr(api_executor, "_run_single_api_test", lambda test, base_url: _canned(test["id"]))

    bus = RecordingBus()
    emitter.set_bus(bus)

    api_executor.api_executor_node({"generated_tests": [{"id": "TC-NO-TYPE"}]})

    assert bus.only().payload["type"] == "api"


def test_api_executor_emits_failure_payload(monkeypatch):
    from agents import api_executor, emitter

    monkeypatch.setattr(
        api_executor, "_run_single_api_test", lambda test, base_url: _canned(test["id"], "failed")
    )
    bus = RecordingBus()
    emitter.set_bus(bus)

    api_executor.api_executor_node({"generated_tests": [{"id": "TC-BAD", "type": "api"}]})

    payload = bus.only().payload
    assert payload["status"] == "failed"
    assert payload["error_message"] == "boom"


def test_ui_executor_emits_one_result_per_test(monkeypatch):
    from agents import ui_executor, emitter

    ran: list[str] = []
    canned = _canned("UI-1")
    canned["self_heal_used"] = False
    canned["vision_heal_used"] = False

    def fake_run(test: dict, headed: bool = False, shared_context: dict | None = None) -> dict:
        ran.append(test["id"])
        out = dict(canned)
        out["id"] = test["id"]
        return out

    monkeypatch.setattr(ui_executor, "_run_single_ui_test", fake_run)

    bus = RecordingBus()
    emitter.set_bus(bus)

    out = ui_executor.ui_executor_node(
        {"generated_tests": [{"id": "UI-1", "type": "ui"}, {"id": "API-1", "type": "api"}]}
    )

    assert ran == ["UI-1"]
    assert bus.kinds == ["test_result"]
    payload = bus.only().payload
    assert payload["id"] == "UI-1"
    assert payload["type"] == "ui"
    assert out["execution_result"]["passed"] == 1


def test_chaos_executor_emits_one_result_per_test(monkeypatch):
    from agents import chaos_executor, emitter

    # Không đụng docker / toxiproxy thật.
    monkeypatch.setattr(chaos_executor, "_docker_available", lambda: False)
    monkeypatch.setattr(chaos_executor, "_get_toxiproxy", lambda: None)

    def fake_run(test: dict, base_url: str) -> dict:
        out = _canned(test["id"])
        out["chaos_action"] = "network_delay"
        return out

    monkeypatch.setattr(chaos_executor, "_run_chaos_scenario", fake_run)

    bus = RecordingBus()
    emitter.set_bus(bus)

    out = chaos_executor.chaos_executor_node(
        {"generated_tests": [{"id": "CH-1", "type": "chaos"}, {"id": "API-1", "type": "api"}]}
    )

    assert bus.kinds == ["test_result"]
    payload = bus.only().payload
    assert payload["id"] == "CH-1"
    assert payload["type"] == "chaos"
    assert out["execution_result"]["passed"] == 1


def test_performance_executor_emits_one_result_per_test(monkeypatch):
    from agents import performance_executor, emitter

    def fake_run(test: dict, base_url: str, headed: bool = False) -> dict:
        return _canned(test["id"])

    monkeypatch.setattr(performance_executor, "_run_performance_case", fake_run)

    bus = RecordingBus()
    emitter.set_bus(bus)

    out = performance_executor.performance_executor_node(
        {"generated_tests": [{"id": "PF-1", "type": "performance"}, {"id": "UI-1", "type": "ui"}]}
    )

    assert bus.kinds == ["test_result"]
    payload = bus.only().payload
    assert payload["id"] == "PF-1"
    assert payload["type"] == "performance"
    assert out["execution_result"]["passed"] == 1


def test_executors_emit_even_with_no_bus(monkeypatch):
    """CLI: bus None thì executor vẫn chạy và trả kết quả như cũ."""
    from agents import api_executor, emitter

    emitter.set_bus(None)
    monkeypatch.setattr(api_executor, "_run_single_api_test", lambda test, base_url: _canned(test["id"]))
    out = api_executor.api_executor_node({"generated_tests": [{"id": "TC-1", "type": "api"}]})
    assert out["execution_result"]["total"] == 1
    assert emitter.get_bus() is None


# ---------------------------------------------------------------------------
# Vị trí emit trong source (chống emit nhầm chỗ)
# ---------------------------------------------------------------------------

def _callee_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return getattr(func, "id", "")


def _emit_calls(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and _callee_name(n) == "emit_test_result"]


_EXECUTOR_NODES = [
    ("agents.api_executor", "api_executor_node"),
    ("agents.ui_executor", "ui_executor_node"),
    ("agents.chaos_executor", "chaos_executor_node"),
    ("agents.performance_executor", "performance_executor_node"),
]


@pytest.mark.parametrize("module_name,func_name", _EXECUTOR_NODES)
def test_emit_sits_once_inside_the_per_test_loop_before_details_append(module_name: str, func_name: str):
    import importlib

    mod = importlib.import_module(module_name)
    func = getattr(mod, func_name)
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))

    # Một số node có thêm vòng lặp khác (vd api_executor gom shared_context),
    # nên vòng lặp per-test được nhận diện là vòng lặp có details.append.
    loops = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.For)
        and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "append"
                for c in ast.walk(n))
    ]
    assert len(loops) == 1, f"{func_name} phải có đúng một vòng lặp per-test"

    loop = loops[0]
    in_loop = _emit_calls(loop)
    assert len(in_loop) == 1, "phải emit đúng một chỗ, trong vòng lặp per-test"
    assert len(_emit_calls(tree)) == 1, "không được emit thêm ngoài vòng lặp per-test"

    appends = [
        n
        for n in ast.walk(loop)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "append"
    ]
    assert appends, "vòng lặp phải còn details.append(res)"
    assert in_loop[0].lineno < min(a.lineno for a in appends), (
        "emit phải chạy trước khi res được gom vào details"
    )
