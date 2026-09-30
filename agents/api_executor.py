"""API Executor – chạy test case API bằng httpx.
Hỗ trợ:
- Multi-step trong 1 test case
- Thay thế {{variable}} từ test_data
- Extract giá trị từ response (context) để dùng cho step sau (auth flow)
- Base URL lấy từ config
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
from rich.console import Console

from agents.state import AgentState, ExecutionResult, TestCaseStatus
from config.settings import settings

console = Console()


def _substitute(template: Any, context: dict) -> Any:
    """Thay {{key}} bằng giá trị trong context (test_data + extracted)."""
    if isinstance(template, str):
        def replacer(match: re.Match) -> str:
            key = match.group(1)
            return str(context.get(key, match.group(0)))
        return re.sub(r"\{\{(\w+)\}\}", replacer, template)
    if isinstance(template, dict):
        return {k: _substitute(v, context) for k, v in template.items()}
    if isinstance(template, list):
        return [_substitute(item, context) for item in template]
    return template


def _parse_action(action: str) -> tuple[str, str]:
    """'POST /api/v1/login' → ('POST', '/api/v1/login')"""
    parts = action.strip().split(maxsplit=1)
    if len(parts) == 2:
        return parts[0].upper(), parts[1]
    return "GET", action


def _extract_from_body(body: Any, extract_map: dict[str, str]) -> dict[str, Any]:
    """
    extract_map ví dụ: {"token": "token", "user_id": "user.id"}
    Lấy giá trị từ response body và trả về dict để đưa vào context.
    """
    extracted = {}
    if not isinstance(body, dict):
        return extracted

    for var_name, path in extract_map.items():
        parts = path.split(".")
        cur: Any = body
        try:
            for p in parts:
                if isinstance(cur, dict):
                    cur = cur[p]
                else:
                    cur = None
                    break
            if cur is not None:
                extracted[var_name] = cur
        except Exception:
            pass
    return extracted


def _check_body(body: Any, expected_body: dict) -> list[str]:
    """Kiểm tra expected.body. Trả về list lỗi (rỗng = pass)."""
    errors = []
    if not expected_body:
        return errors
    if not isinstance(body, dict):
        if expected_body:
            errors.append("Response body không phải JSON object")
        return errors

    for key, rule in expected_body.items():
        parts = key.split(".")
        cur: Any = body
        found = True
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                found = False
                break

        if rule == "exists":
            if not found:
                errors.append(f"'{key}' không tồn tại trong response")
        else:
            if not found:
                errors.append(f"'{key}' không tồn tại (expected: {rule})")
            elif cur != rule:
                errors.append(f"'{key}' expected {rule!r}, got {cur!r}")
    return errors


def _run_single_api_test(test: dict, base_url: str) -> dict:
    """Chạy 1 test case API, hỗ trợ multi-step + context chaining."""
    start = time.perf_counter()
    result = {
        "id": test.get("id"),
        "title": test.get("title") or test.get("name"),
        "status": TestCaseStatus.UNTESTED.value,
        "error_message": None,
        "actual_result": None,
        "duration_ms": 0,
        "response_status": None,
        "response_body": None,
        "steps_run": 0,
        "extracted": {},  # Hybrid: biến lấy từ response (token, ...)
    }

    try:
        steps = test.get("steps") or []
        if not steps:
            raise ValueError("Test case không có steps")

        # Context ban đầu = test_data
        context: dict[str, Any] = dict(test.get("test_data") or {})
        expected = test.get("expected") or {}
        last_resp = None
        last_body: Any = None

        # extract map (có thể đặt trong test case hoặc expected)
        extract_map = test.get("extract") or expected.get("extract") or {}

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for step in steps:
                action = step.get("action", "GET /")
                method, path = _parse_action(action)
                data = _substitute(step.get("data") or {}, context)
                headers = _substitute(step.get("headers") or {"Content-Type": "application/json"}, context)

                # Hỗ trợ Authorization: Bearer {{token}}
                url = path if path.startswith("http") else base_url.rstrip("/") + "/" + path.lstrip("/")

                if method in ("GET", "DELETE", "HEAD"):
                    resp = client.request(method, url, headers=headers, params=data if data else None)
                else:
                    resp = client.request(method, url, headers=headers, json=data if data else None)

                last_resp = resp
                result["steps_run"] += 1

                try:
                    last_body = resp.json()
                except Exception:
                    last_body = resp.text[:1000]

                # Extract biến từ response để dùng step sau + hybrid UI
                if extract_map and isinstance(last_body, dict):
                    extracted = _extract_from_body(last_body, extract_map)
                    context.update(extracted)
                    result["extracted"].update(extracted)

                # Nếu step có expected riêng (tùy chọn) thì check luôn
                step_expected = step.get("expected")
                if step_expected:
                    exp_status = step_expected.get("status_code")
                    if exp_status and resp.status_code != exp_status:
                        raise AssertionError(
                            f"Step {step.get('step')}: expected status {exp_status}, got {resp.status_code}"
                        )

        # Đánh giá expected của cả test case (dựa trên response cuối)
        result["response_status"] = last_resp.status_code if last_resp else None
        result["response_body"] = last_body

        expected_status = expected.get("status_code")
        if expected_status is not None and last_resp and last_resp.status_code != expected_status:
            result["status"] = TestCaseStatus.FAILED.value
            result["error_message"] = f"Expected status {expected_status}, got {last_resp.status_code}"
            result["actual_result"] = f"Status {last_resp.status_code}"
        else:
            body_errors = _check_body(last_body, expected.get("body") or {})
            if body_errors:
                result["status"] = TestCaseStatus.FAILED.value
                result["error_message"] = "; ".join(body_errors)
                result["actual_result"] = str(last_body)[:400]
            else:
                result["status"] = TestCaseStatus.PASSED.value
                result["actual_result"] = f"Status {last_resp.status_code if last_resp else 'N/A'}, body OK"

    except Exception as e:
        result["status"] = TestCaseStatus.ERROR.value
        result["error_message"] = str(e)
        result["actual_result"] = str(e)

    result["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return result


def api_executor_node(state: AgentState) -> dict[str, Any]:
    """Node: thực thi danh sách generated_tests (API)."""
    tests = state.get("generated_tests") or []
    if not tests:
        return {
            "error": "Không có test case nào để chạy",
            "current_step": "api_executor_failed",
            "execution_result": ExecutionResult().model_dump(),
        }

    base_url = settings.default_base_url
    console.print(f"\n[bold cyan]▶ Đang chạy API tests[/bold cyan] (base_url={base_url})")

    details = []
    passed = failed = error = skipped = blocked = 0
    total_duration = 0.0

    for test in tests:
        ttype = (test.get("type") or "api").lower()
        if ttype not in ("api", "integration"):
            skipped += 1
            continue

        res = _run_single_api_test(test, base_url=base_url)
        details.append(res)
        total_duration += res["duration_ms"]

        status = res["status"]
        title = res.get("title") or res.get("id")
        if status == TestCaseStatus.PASSED.value:
            passed += 1
            console.print(f"  [green]✓[/green] {res['id']} – {title} ({res['duration_ms']}ms)")
        elif status == TestCaseStatus.FAILED.value:
            failed += 1
            console.print(f"  [red]✗[/red] {res['id']} – {title} → {res['error_message']}")
        elif status == TestCaseStatus.BLOCKED.value:
            blocked += 1
            console.print(f"  [yellow]□[/yellow] {res['id']} – {title} (blocked)")
        else:
            error += 1
            console.print(f"  [yellow]![/yellow] {res['id']} – {title} → {res['error_message']}")

    execution = ExecutionResult(
        total=len(details),
        passed=passed,
        failed=failed,
        blocked=blocked,
        skipped=skipped,
        error=error,
        duration_ms=round(total_duration, 2),
        details=details,
    )

    # Hybrid: gom extracted token/cookie vào shared_context
    shared = dict(state.get("shared_context") or {})
    for d in details:
        if d.get("status") == TestCaseStatus.PASSED.value and d.get("extracted"):
            shared.update(d["extracted"])
    if shared:
        console.print(f"  [dim]shared_context keys: {list(shared.keys())}[/dim]")

    return {
        "execution_result": execution.model_dump(),
        "shared_context": shared,
        "current_step": "api_executor_done",
        "error": None,
    }
