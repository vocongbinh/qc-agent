from __future__ import annotations

import threading
import time

from tui.gate import REVIEW_TIMED_OUT, ReviewGate


def test_wait_blocks_until_resolved():
    gate = ReviewGate(timeout=5.0)
    result: list = []

    def worker():
        result.append(gate.wait())

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.1)
    assert not result, "phải block khi chưa resolve"

    kept = [{"id": "TC_001"}]
    gate.resolve(kept)
    t.join(timeout=3)

    assert result == [kept]


def test_resolve_none_means_reject():
    gate = ReviewGate(timeout=5.0)
    result: list = []
    t = threading.Thread(target=lambda: result.append(gate.wait()))
    t.start()
    time.sleep(0.05)
    gate.resolve(None)
    t.join(timeout=3)
    assert result == [None]


def test_timeout_returns_none():
    gate = ReviewGate(timeout=0.1)
    result: list = []
    start = time.perf_counter()
    # wait() chạy ở thread riêng: nếu timeout bị bỏ sót, thread treo vĩnh viễn
    # và treo luôn cả pytest thay vì báo test fail. daemon=True để thread rò
    # không chặn interpreter shutdown — lỗi vẫn hiện ra dưới dạng assert fail.
    t = threading.Thread(target=lambda: result.append(gate.wait()), daemon=True)
    t.start()
    t.join(timeout=3)
    elapsed = time.perf_counter() - start
    assert not t.is_alive(), "wait() không timeout — treo vĩnh viễn"
    # Timeout phải phân biệt được với Reject (`None`) — spec §4.3 yêu cầu báo
    # `run_error` cho timeout, `cancelled` cho reject.
    assert result == [REVIEW_TIMED_OUT]
    assert 0.05 < elapsed < 2.0, f"timeout sai: {elapsed}s"


def test_is_resolved_flag():
    gate = ReviewGate(timeout=5.0)
    assert gate.is_resolved() is False
    gate.resolve([])
    assert gate.is_resolved() is True


def test_wait_after_resolve_returns_immediately():
    gate = ReviewGate(timeout=0.1)
    gate.resolve([{"id": "X"}])
    start = time.perf_counter()
    got = gate.wait()
    assert got == [{"id": "X"}]
    assert time.perf_counter() - start < 0.05


def test_release_unblocks_with_none():
    """Dùng khi thread crash — gate không được treo vĩnh viễn."""
    gate = ReviewGate(timeout=10.0)
    result: list = []
    t = threading.Thread(target=lambda: result.append(gate.wait()))
    t.start()
    time.sleep(0.05)
    gate.release()
    t.join(timeout=3)
    assert result == [None]
