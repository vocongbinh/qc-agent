"""Chaos / Resilience Executor – Phase 3 (tinh chỉnh).

Hỗ trợ:
- Docker: stop/start Redis, Kafka, DB (+ auto restore)
- Concurrent load (httpx thread pool)
- Network: delay mô phỏng + Toxiproxy (latency, timeout, reset_peer, disable proxy)
- Scenario-driven type=chaos
"""

from __future__ import annotations

import concurrent.futures
import subprocess
import time
from typing import Any

import httpx
from rich.console import Console

from agents.state import AgentState, ExecutionResult, TestCaseStatus
from config.settings import settings

console = Console()


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

def _docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _docker_stop(container: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["docker", "stop", container],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return True, f"Stopped: {container}"
        return False, (r.stderr or r.stdout or "").strip()
    except Exception as e:
        return False, str(e)


def _docker_start(container: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["docker", "start", container],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return True, f"Started: {container}"
        return False, (r.stderr or r.stdout or "").strip()
    except Exception as e:
        return False, str(e)


def _docker_inspect_running(container: str) -> bool:
    try:
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and r.stdout.strip().lower() == "true"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# HTTP probe + concurrent
# ---------------------------------------------------------------------------

def _run_http_once(
    method: str,
    url: str,
    headers: dict | None = None,
    body: dict | None = None,
    timeout: float = 10.0,
) -> dict:
    start = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            if method.upper() in ("GET", "DELETE", "HEAD"):
                resp = client.request(method.upper(), url, headers=headers or {}, params=body)
            else:
                resp = client.request(method.upper(), url, headers=headers or {}, json=body)
        return {
            "ok": True,
            "status_code": resp.status_code,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "error": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "status_code": None,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "error": str(e),
        }


def _run_concurrent(
    method: str,
    url: str,
    headers: dict | None,
    body: dict | None,
    concurrency: int,
    timeout: float,
) -> dict:
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(concurrency, 50)) as pool:
        futs = [
            pool.submit(_run_http_once, method, url, headers, body, timeout)
            for _ in range(concurrency)
        ]
        for f in concurrent.futures.as_completed(futs):
            results.append(f.result())

    ok = sum(1 for r in results if r["ok"])
    statuses = [r["status_code"] for r in results if r["status_code"] is not None]
    durations = [r["duration_ms"] for r in results]
    errors = [r["error"] for r in results if r["error"]]
    return {
        "total": len(results),
        "success": ok,
        "failed": len(results) - ok,
        "status_codes": {str(s): statuses.count(s) for s in set(statuses)},
        "duration_ms_avg": round(sum(durations) / len(durations), 2) if durations else 0,
        "duration_ms_p95": round(sorted(durations)[int(len(durations) * 0.95) - 1], 2) if len(durations) >= 2 else (durations[0] if durations else 0),
        "duration_ms_max": max(durations) if durations else 0,
        "sample_errors": errors[:5],
    }


def _parse_http_step(step: dict, base_url: str) -> tuple[str, str, dict, dict | None]:
    raw = step.get("action", "GET /health")
    parts = raw.strip().split(maxsplit=1)
    method = parts[0].upper() if len(parts) == 2 else "GET"
    path = parts[1] if len(parts) == 2 else parts[0]
    url = path if path.startswith("http") else base_url.rstrip("/") + "/" + path.lstrip("/")
    return method, url, step.get("headers") or {}, step.get("data")


def _evaluate_probe(probe: dict, expected: dict) -> list[str]:
    """Trả về list lỗi (rỗng = pass). Resilience-aware."""
    errors = []
    status_code = probe.get("status_code")
    exp_status = expected.get("status_code")
    must_not = expected.get("must_not_status") or [500]
    allow_conn_err = expected.get("allow_connection_error", False)

    if not probe.get("ok"):
        if allow_conn_err:
            return []
        # Resilience: connection error khi dependency down có thể chấp nhận nếu không expect status cụ thể
        if exp_status is None and not expected.get("require_success", False):
            return []
        errors.append(f"request error: {probe.get('error')}")
        return errors

    if exp_status is not None:
        allowed = exp_status if isinstance(exp_status, list) else [exp_status]
        if status_code not in allowed:
            errors.append(f"status {status_code} not in allowed {allowed}")

    if status_code in must_not:
        errors.append(f"status {status_code} bị cấm (must_not={must_not})")

    max_dur = expected.get("max_duration_ms")
    if max_dur and probe.get("duration_ms", 0) > max_dur:
        errors.append(f"duration {probe['duration_ms']}ms > {max_dur}ms")

    return errors


# ---------------------------------------------------------------------------
# Toxiproxy helpers
# ---------------------------------------------------------------------------

def _get_toxiproxy():
    try:
        from tools.toxiproxy_client import ToxiproxyClient
        client = ToxiproxyClient()
        if client.available():
            return client
    except Exception:
        pass
    return None


def _apply_toxiproxy_fault(chaos: dict) -> tuple[Any, str, list[str]]:
    """
    Apply toxic. Returns (client, proxy_name, toxic_names_applied).
    chaos fields:
      proxy: tên proxy (default api_proxy)
      listen: 0.0.0.0:18080
      upstream: host.docker.internal:8000 hoặc app:8000
      toxic: latency | timeout | reset_peer | disable
      latency_ms, jitter_ms, timeout_ms
    """
    client = _get_toxiproxy()
    if not client:
        return None, "", []

    proxy_name = chaos.get("proxy") or settings.toxiproxy_proxy_name
    listen = chaos.get("listen") or settings.toxiproxy_listen
    upstream = (
        chaos.get("upstream")
        or chaos.get("upstream_host")
        or settings.app_upstream  # từ .env: APP_UPSTREAM_HOST + APP_UPSTREAM_PORT
    )

    client.ensure_proxy(proxy_name, listen=listen, upstream=upstream)
    toxic_type = (chaos.get("toxic") or chaos.get("action") or "latency").lower()
    applied = []

    if toxic_type in ("latency", "toxiproxy_latency", "network_delay"):
        name = "qc_latency"
        client.add_latency(
            proxy_name,
            latency_ms=int(chaos.get("latency_ms", chaos.get("delay_ms", 2000))),
            jitter_ms=int(chaos.get("jitter_ms", 100)),
            toxic_name=name,
        )
        applied.append(name)
    elif toxic_type in ("timeout", "toxiproxy_timeout"):
        name = "qc_timeout"
        client.add_timeout(
            proxy_name,
            timeout_ms=int(chaos.get("timeout_ms", 1000)),
            toxic_name=name,
        )
        applied.append(name)
    elif toxic_type in ("reset_peer", "reset"):
        name = "qc_reset"
        client.add_reset_peer(proxy_name, toxic_name=name)
        applied.append(name)
    elif toxic_type in ("disable", "connection_down"):
        client.disable_proxy(proxy_name)
        applied.append("__disabled__")

    return client, proxy_name, applied


def _cleanup_toxiproxy(client, proxy_name: str, applied: list[str]) -> None:
    if not client or not proxy_name:
        return
    try:
        if "__disabled__" in applied:
            client.enable_proxy(proxy_name)
        for t in applied:
            if t != "__disabled__":
                try:
                    client.remove_toxic(proxy_name, t)
                except Exception:
                    pass
    except Exception as e:
        console.print(f"    [yellow]Toxiproxy cleanup warning: {e}[/yellow]")


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------


def _kafka_http_probe(bootstrap_http: str = "http://localhost:8082") -> dict:
    """Probe Kafka qua Redpanda Pandaproxy (HTTP) – không cần kafka-python.
    GET /topics hoặc topics list.
    """
    start = time.perf_counter()
    try:
        with httpx.Client(timeout=5.0) as client:
            # Pandaproxy: GET /topics
            r = client.get(f"{bootstrap_http.rstrip('/')}/topics")
            return {
                "ok": r.status_code < 500,
                "status_code": r.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                "error": None,
                "body_preview": r.text[:200],
            }
    except Exception as e:
        return {
            "ok": False,
            "status_code": None,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "error": str(e),
            "body_preview": None,
        }


def _run_chaos_scenario(test: dict, base_url: str) -> dict:
    start = time.perf_counter()
    result = {
        "id": test.get("id"),
        "title": test.get("title") or test.get("name"),
        "status": TestCaseStatus.UNTESTED.value,
        "error_message": None,
        "actual_result": None,
        "duration_ms": 0,
        "chaos_action": None,
        "chaos_detail": None,
        "restored": False,
    }

    chaos = test.get("chaos") or {}
    action = (chaos.get("action") or "http_check").lower()
    result["chaos_action"] = action
    expected = test.get("expected") or {}
    steps = test.get("steps") or [{"step": 1, "action": "GET /health", "data": {}}]

    # Resolve probe base URL (qua toxiproxy listen port nếu dùng toxic)
    probe_base = base_url
    if action in (
        "toxiproxy_latency", "toxiproxy_timeout", "toxiproxy_reset",
        "latency", "timeout", "reset_peer", "disable", "network_delay",
    ) and chaos.get("use_proxy_url", True):
        # Gọi qua proxy port 18080 thay vì trực tiếp app
        proxy_url = chaos.get("proxy_url") or getattr(settings, "toxiproxy_proxy_url", None) or "http://localhost:18080"
        probe_base = proxy_url

    container = chaos.get("target") or chaos.get("container")
    if action in ("kill_redis",):
        container = container or settings.redis_container
        action = "stop_container"
    elif action in ("kill_kafka", "kafka_down"):
        container = container or settings.kafka_container
        action = "stop_container"
    elif action in ("kill_db",):
        container = container or settings.db_container
        action = "stop_container"

    toxic_client = None
    proxy_name = ""
    applied_toxics: list[str] = []
    was_running = False

    try:
        # ----- Inject fault -----
        if action == "stop_container":
            if not container:
                raise ValueError("stop_container cần chaos.target (tên container)")
            if not _docker_available():
                result["status"] = TestCaseStatus.BLOCKED.value
                result["error_message"] = "Docker không available"
                result["actual_result"] = "Blocked: no Docker"
                return result
            was_running = _docker_inspect_running(container)
            ok, msg = _docker_stop(container)
            result["chaos_detail"] = msg
            if not ok:
                result["status"] = TestCaseStatus.BLOCKED.value
                result["error_message"] = f"Không stop được: {msg}"
                return result
            console.print(f"    [yellow]CHAOS docker stop: {container}[/yellow]")
            time.sleep(float(chaos.get("wait_after_sec", 2)))

        elif action in (
            "toxiproxy_latency", "toxiproxy_timeout", "toxiproxy_reset",
            "latency", "timeout", "reset_peer", "disable", "network_delay",
        ):
            # Ưu tiên Toxiproxy; fallback sleep delay nếu không có
            toxic_client, proxy_name, applied_toxics = _apply_toxiproxy_fault({
                **chaos,
                "toxic": {
                    "network_delay": "latency",
                    "toxiproxy_latency": "latency",
                    "latency": "latency",
                    "toxiproxy_timeout": "timeout",
                    "timeout": "timeout",
                    "toxiproxy_reset": "reset_peer",
                    "reset_peer": "reset_peer",
                    "disable": "disable",
                }.get(action, action),
            })
            if toxic_client:
                result["chaos_detail"] = f"toxiproxy {action} on {proxy_name}: {applied_toxics}"
                console.print(f"    [yellow]CHAOS toxiproxy: {result['chaos_detail']}[/yellow]")
            else:
                # Fallback: sleep mô phỏng delay
                delay_ms = int(chaos.get("latency_ms", chaos.get("delay_ms", 2000)))
                time.sleep(delay_ms / 1000.0)
                result["chaos_detail"] = f"fallback sleep delay {delay_ms}ms (no Toxiproxy)"
                console.print(f"    [yellow]CHAOS fallback delay: {delay_ms}ms[/yellow]")
                probe_base = base_url  # không dùng proxy

        elif action in ("kafka_probe", "kafka_health"):
            http_proxy = chaos.get("kafka_http") or "http://localhost:8082"
            probe = _kafka_http_probe(http_proxy)
            result["chaos_detail"] = probe
            result["actual_result"] = str(probe)
            fails = _evaluate_probe(probe, expected)
            if not probe.get("ok") and expected.get("expect_kafka_up", True):
                fails.append(probe.get("error") or "kafka probe failed")
            if fails:
                result["status"] = TestCaseStatus.FAILED.value
                result["error_message"] = "; ".join(fails)
            else:
                result["status"] = TestCaseStatus.PASSED.value
            return result

        elif action == "concurrent":
            method, url, headers, body = _parse_http_step(steps[0], probe_base)
            concurrency = int(chaos.get("concurrency", 20))
            timeout = float(chaos.get("timeout", 10))
            stats = _run_concurrent(method, url, headers, body, concurrency, timeout)
            result["chaos_detail"] = stats
            result["actual_result"] = (
                f"n={concurrency}, ok={stats['success']}/{stats['total']}, "
                f"avg={stats['duration_ms_avg']}ms, p95={stats['duration_ms_p95']}ms, max={stats['duration_ms_max']}ms"
            )
            min_rate = float(expected.get("min_success_rate", 0.8))
            rate = stats["success"] / stats["total"] if stats["total"] else 0
            fails = []
            if rate < min_rate:
                fails.append(f"success_rate {rate:.1%} < {min_rate:.1%}")
            max_dur = expected.get("max_duration_ms")
            if max_dur and stats["duration_ms_max"] > max_dur:
                fails.append(f"max {stats['duration_ms_max']}ms > {max_dur}ms")
            p95_max = expected.get("p95_duration_ms")
            if p95_max and stats["duration_ms_p95"] > p95_max:
                fails.append(f"p95 {stats['duration_ms_p95']}ms > {p95_max}ms")
            if fails:
                result["status"] = TestCaseStatus.FAILED.value
                result["error_message"] = "; ".join(fails)
            else:
                result["status"] = TestCaseStatus.PASSED.value
            return result  # concurrent không cần probe steps thêm

        # ----- Probe HTTP -----
        last_probe = None
        for step in steps:
            method, url, headers, body = _parse_http_step(step, probe_base)
            timeout = float(chaos.get("timeout", (expected.get("max_duration_ms") or 10000) / 1000))
            last_probe = _run_http_once(method, url, headers, body, timeout)

        result["actual_result"] = str(last_probe)
        fails = _evaluate_probe(last_probe or {}, expected)
        if fails:
            result["status"] = TestCaseStatus.FAILED.value
            result["error_message"] = "; ".join(fails)
        else:
            result["status"] = TestCaseStatus.PASSED.value

    except Exception as e:
        result["status"] = TestCaseStatus.ERROR.value
        result["error_message"] = str(e)
        result["actual_result"] = str(e)

    finally:
        # ----- Restore docker -----
        if chaos.get("restore", True) and container and action == "stop_container":
            if _docker_available() and (was_running or chaos.get("force_restore", True)):
                ok, msg = _docker_start(container)
                result["restored"] = ok
                console.print(f"    [dim]RESTORE docker: {msg}[/dim]")
                time.sleep(float(chaos.get("wait_restore_sec", 2)))

        # ----- Restore toxiproxy -----
        if toxic_client and applied_toxics:
            _cleanup_toxiproxy(toxic_client, proxy_name, applied_toxics)
            result["restored"] = True
            console.print("    [dim]RESTORE toxiproxy toxics removed[/dim]")

    result["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return result


def chaos_executor_node(state: AgentState) -> dict[str, Any]:
    tests = state.get("generated_tests") or []
    chaos_tests = [t for t in tests if (t.get("type") or "").lower() == "chaos"]

    if not chaos_tests:
        console.print("[dim]Không có Chaos test case nào để chạy.[/dim]")
        existing = state.get("execution_result") or ExecutionResult().model_dump()
        return {
            "execution_result": existing,
            "current_step": "chaos_executor_done",
            "error": None,
        }

    base_url = settings.default_base_url
    has_docker = _docker_available()
    has_toxi = _get_toxiproxy() is not None
    console.print(
        f"\n[bold cyan]▶ Chaos tests[/bold cyan] "
        f"({len(chaos_tests)} cases | docker={'yes' if has_docker else 'no'} | toxiproxy={'yes' if has_toxi else 'no'})"
    )

    details = list((state.get("execution_result") or {}).get("details") or [])
    passed = failed = error = blocked = 0
    total_duration = 0.0

    for test in chaos_tests:
        res = _run_chaos_scenario(test, base_url=base_url)
        details.append(res)
        total_duration += res["duration_ms"]
        title = res.get("title") or res.get("id")
        st = res["status"]
        if st == TestCaseStatus.PASSED.value:
            passed += 1
            console.print(f"  [green]✓[/green] {res['id']} – {title} [{res.get('chaos_action')}]")
        elif st == TestCaseStatus.BLOCKED.value:
            blocked += 1
            console.print(f"  [yellow]□[/yellow] {res['id']} – {title} (blocked)")
        elif st == TestCaseStatus.FAILED.value:
            failed += 1
            console.print(f"  [red]✗[/red] {res['id']} – {title} → {res.get('error_message')}")
        else:
            error += 1
            console.print(f"  [yellow]![/yellow] {res['id']} – {title} → {res.get('error_message')}")

    prev = state.get("execution_result") or {}
    execution = ExecutionResult(
        total=prev.get("total", 0) + len(chaos_tests),
        passed=prev.get("passed", 0) + passed,
        failed=prev.get("failed", 0) + failed,
        error=prev.get("error", 0) + error,
        blocked=prev.get("blocked", 0) + blocked,
        skipped=prev.get("skipped", 0),
        duration_ms=round(prev.get("duration_ms", 0) + total_duration, 2),
        details=details,
    )
    return {
        "execution_result": execution.model_dump(),
        "current_step": "chaos_executor_done",
        "error": None,
    }
