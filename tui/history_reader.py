"""Đọc lịch sử run từ reports/report_*.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union


@dataclass
class RunSummary:
    path: str
    timestamp: str
    request: str
    plan_title: str
    scope: str
    total: int
    passed: int
    failed: int
    error: int
    skipped: int
    duration_ms: float
    details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def short_id(self) -> str:
        return Path(self.path).stem.replace("report_", "")


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def parse_report(data: dict[str, Any], path: str) -> RunSummary:
    plan = data.get("test_plan") or {}
    ex = data.get("execution_result") or {}
    details = ex.get("details") or []
    return RunSummary(
        path=path,
        timestamp=_as_str(data.get("timestamp")),
        request=_as_str(data.get("user_request")),
        plan_title=_as_str(plan.get("title")),
        scope=_as_str(plan.get("scope")),
        total=_as_int(ex.get("total")),
        passed=_as_int(ex.get("passed")),
        failed=_as_int(ex.get("failed")),
        error=_as_int(ex.get("error")),
        skipped=_as_int(ex.get("skipped")),
        duration_ms=_as_float(ex.get("duration_ms")),
        details=list(details) if isinstance(details, list) else [],
    )


def load_history(reports_dir: Union[Path, str]) -> tuple[list[RunSummary], int]:
    """Trả (runs mới nhất trước, số file JSON hỏng)."""
    reports_dir = Path(reports_dir)
    if not reports_dir.is_dir():
        return [], 0

    runs: list[RunSummary] = []
    broken = 0
    for f in reports_dir.glob("report_*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            broken += 1
            continue
        if not isinstance(data, dict):
            broken += 1
            continue
        runs.append(parse_report(data, str(f)))

    runs.sort(key=lambda r: r.timestamp, reverse=True)
    return runs, broken
