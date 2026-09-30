"""Performance Executor – Phase 4.

- API load: concurrent httpx + optional k6 script generate/run
- FE: Playwright performance metrics (navigation timing, Core Web Vitals xấp xỉ)
- type=performance trong test case
"""

from __future__ import annotations

import concurrent.futures
import json
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console

from agents.state import AgentState, ExecutionResult, TestCaseStatus
from config.settings import settings

console = Console()
PERF_DIR = settings.reports_dir / "performance"


def _run_http_once(method: str, url: str, headers: dict | None, body: dict | None, timeout: float) -> dict:
    start = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            if method.upper() in ("GET", "DELETE", "HEAD"):
                resp = client.request(method.upper(), url, headers=headers or {}, params=body)
            else:
                resp = client.request(method.upper(), url, headers=headers or {}, json=body)
        return {
            "ok": resp.status_code < 500,
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


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(int(len(s) * p / 100), len(s) - 1)
    return round(s[idx], 2)


def _api_load_test(
    method: str,
    url: str,
    headers: dict | None,
    body: dict | None,
    vus: int,
    duration_sec: float,
    timeout: float,
) -> dict:
    """Load test đơn giản: duy trì ~vus workers trong duration_sec."""
    results: list[dict] = []
    end_at = time.perf_counter() + duration_sec
    lock_results: list[dict] = []

    def worker():
        local = []
        while time.perf_counter() < end_at:
            local.append(_run_http_once(method, url, headers, body, timeout))
        return local

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(vus, 100)) as pool:
        futs = [pool.submit(worker) for _ in range(vus)]
        for f in concurrent.futures.as_completed(futs):
            lock_results.extend(f.result())

    results = lock_results
    durations = [r["duration_ms"] for r in results]
    ok = sum(1 for r in results if r["ok"])
    statuses = {}
    for r in results:
        sc = str(r["status_code"])
        statuses[sc] = statuses.get(sc, 0) + 1

    return {
        "total_requests": len(results),
        "success": ok,
        "failed": len(results) - ok,
        "success_rate": round(ok / len(results), 4) if results else 0,
        "rps": round(len(results) / duration_sec, 2) if duration_sec else 0,
        "latency_ms": {
            "avg": round(statistics.mean(durations), 2) if durations else 0,
            "min": min(durations) if durations else 0,
            "max": max(durations) if durations else 0,
            "p50": _percentile(durations, 50),
            "p95": _percentile(durations, 95),
            "p99": _percentile(durations, 99),
        },
        "status_codes": statuses,
    }


def _generate_k6_script(method: str, url: str, vus: int, duration: str, out_path: Path) -> Path:
    script = f'''import http from 'k6/http';
import {{ check, sleep }} from 'k6';

export const options = {{
  vus: {vus},
  duration: '{duration}',
  thresholds: {{
    http_req_duration: ['p(95)<2000'],
    http_req_failed: ['rate<0.1'],
  }},
}};

export default function () {{
  const res = http.{method.lower()}('{url}');
  check(res, {{
    'status is 2xx': (r) => r.status >= 200 && r.status < 300,
  }});
  sleep(0.1);
}}
'''
    out_path.write_text(script, encoding="utf-8")
    return out_path


def _run_k6_if_available(script_path: Path) -> dict | None:
    try:
        r = subprocess.run(
            ["k6", "run", "--summary-export", str(script_path.with_suffix(".summary.json")), str(script_path)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        summary_path = script_path.with_suffix(".summary.json")
        summary = {}
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return {
            "k6_exit": r.returncode,
            "stdout_tail": (r.stdout or "")[-1500:],
            "summary": summary,
        }
    except FileNotFoundError:
        return None
    except Exception as e:
        return {"error": str(e)}


def _fe_performance(url: str, headed: bool = False) -> dict:
    """Thu thập metrics FE bằng Playwright (navigation timing + paint)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"error": "Playwright chưa cài", "ok": False}

    metrics: dict[str, Any] = {"url": url, "ok": False}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed)
            page = browser.new_page()
            start = time.perf_counter()
            page.goto(url, wait_until="networkidle", timeout=60000)
            load_ms = round((time.perf_counter() - start) * 1000, 2)

            timing = page.evaluate(
                """() => {
                const t = performance.timing || {};
                const nav = performance.getEntriesByType('navigation')[0] || {};
                const paint = performance.getEntriesByType('paint') || [];
                const fcp = paint.find(x => x.name === 'first-contentful-paint');
                return {
                  domContentLoaded: nav.domContentLoadedEventEnd || (t.domContentLoadedEventEnd - t.navigationStart) || null,
                  loadEvent: nav.loadEventEnd || (t.loadEventEnd - t.navigationStart) || null,
                  responseStart: nav.responseStart || null,
                  ttfb: nav.responseStart || null,
                  fcp: fcp ? fcp.startTime : null,
                  transferSize: nav.transferSize || null,
                };
            }"""
            )
            # Web Vitals xấp xỉ qua PerformanceObserver nếu có
            vitals = page.evaluate(
                """() => new Promise((resolve) => {
                  const out = {};
                  try {
                    new PerformanceObserver((list) => {
                      for (const e of list.getEntries()) {
                        if (e.name === 'first-contentful-paint') out.fcp = e.startTime;
                        if (e.entryType === 'largest-contentful-paint') out.lcp = e.startTime;
                        if (e.entryType === 'layout-shift' && !e.hadRecentInput) {
                          out.cls = (out.cls || 0) + e.value;
                        }
                      }
                    }).observe({ type: 'paint', buffered: true });
                    new PerformanceObserver((list) => {
                      const entries = list.getEntries();
                      if (entries.length) out.lcp = entries[entries.length - 1].startTime;
                    }).observe({ type: 'largest-contentful-paint', buffered: true });
                    new PerformanceObserver((list) => {
                      for (const e of list.getEntries()) {
                        if (!e.hadRecentInput) out.cls = (out.cls || 0) + e.value;
                      }
                    }).observe({ type: 'layout-shift', buffered: true });
                  } catch (e) {}
                  setTimeout(() => resolve(out), 1000);
                })"""
            )
            browser.close()

            metrics.update({
                "ok": True,
                "page_load_ms": load_ms,
                "navigation": timing,
                "web_vitals": vitals,
            })
    except Exception as e:
        metrics["error"] = str(e)
        metrics["ok"] = False
    return metrics


def _run_performance_case(test: dict, base_url: str, headed: bool = False) -> dict:
    start = time.perf_counter()
    result = {
        "id": test.get("id"),
        "title": test.get("title") or test.get("name"),
        "status": TestCaseStatus.UNTESTED.value,
        "error_message": None,
        "actual_result": None,
        "duration_ms": 0,
        "perf_detail": None,
        "artifacts": [],
    }

    perf = test.get("performance") or test.get("test_data") or {}
    kind = (perf.get("kind") or perf.get("target") or "api").lower()  # api | fe | k6
    expected = test.get("expected") or {}
    steps = test.get("steps") or []

    PERF_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")

    try:
        if kind in ("fe", "frontend", "lighthouse", "web"):
            url = perf.get("url")
            if not url and steps:
                # steps[0] action "goto https://..."
                action = steps[0].get("action", "")
                parts = action.split(maxsplit=1)
                url = parts[1] if len(parts) == 2 else perf.get("url")
            if not url:
                raise ValueError("FE performance cần performance.url hoặc step goto")

            metrics = _fe_performance(url, headed=headed)
            result["perf_detail"] = metrics
            result["actual_result"] = json.dumps(metrics, ensure_ascii=False)[:500]

            out = PERF_DIR / f"fe_{test.get('id', 'perf')}_{ts}.json"
            out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
            result["artifacts"].append(str(out))

            fails = []
            if not metrics.get("ok"):
                fails.append(metrics.get("error") or "FE metrics failed")
            max_load = expected.get("max_page_load_ms") or expected.get("max_duration_ms")
            if max_load and metrics.get("page_load_ms", 0) > max_load:
                fails.append(f"page_load {metrics['page_load_ms']}ms > {max_load}ms")
            max_lcp = expected.get("max_lcp_ms")
            lcp = (metrics.get("web_vitals") or {}).get("lcp")
            if max_lcp and lcp and lcp > max_lcp:
                fails.append(f"LCP {lcp}ms > {max_lcp}ms")

            if fails:
                result["status"] = TestCaseStatus.FAILED.value
                result["error_message"] = "; ".join(fails)
            else:
                result["status"] = TestCaseStatus.PASSED.value

        else:
            # API load
            if not steps:
                steps = [{"step": 1, "action": f"GET {base_url}/health", "data": {}}]
            raw = steps[0].get("action", "GET /health")
            parts = raw.strip().split(maxsplit=1)
            method = parts[0].upper() if len(parts) == 2 else "GET"
            path = parts[1] if len(parts) == 2 else parts[0]
            url = path if path.startswith("http") else base_url.rstrip("/") + "/" + path.lstrip("/")

            vus = int(perf.get("vus", perf.get("concurrency", 10)))
            duration_sec = float(perf.get("duration_sec", perf.get("duration", 10)))
            timeout = float(perf.get("timeout", 30))

            # Optional k6
            k6_result = None
            if kind == "k6" or perf.get("use_k6"):
                script_path = PERF_DIR / f"k6_{test.get('id', 'perf')}_{ts}.js"
                _generate_k6_script(method, url, vus, f"{int(duration_sec)}s", script_path)
                result["artifacts"].append(str(script_path))
                k6_result = _run_k6_if_available(script_path)
                if k6_result is None:
                    console.print("    [dim]k6 không có trên PATH – fallback httpx load[/dim]")

            stats = _api_load_test(
                method, url, steps[0].get("headers"), steps[0].get("data"),
                vus=vus, duration_sec=duration_sec, timeout=timeout,
            )
            if k6_result:
                stats["k6"] = k6_result
            result["perf_detail"] = stats
            result["actual_result"] = (
                f"reqs={stats['total_requests']}, rps={stats['rps']}, "
                f"ok={stats['success_rate']:.1%}, p95={stats['latency_ms']['p95']}ms"
            )

            out = PERF_DIR / f"api_{test.get('id', 'perf')}_{ts}.json"
            out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
            result["artifacts"].append(str(out))

            fails = []
            min_rate = float(expected.get("min_success_rate", 0.95))
            if stats["success_rate"] < min_rate:
                fails.append(f"success_rate {stats['success_rate']:.1%} < {min_rate:.1%}")
            max_p95 = expected.get("p95_duration_ms") or expected.get("max_duration_ms")
            if max_p95 and stats["latency_ms"]["p95"] > max_p95:
                fails.append(f"p95 {stats['latency_ms']['p95']}ms > {max_p95}ms")
            min_rps = expected.get("min_rps")
            if min_rps and stats["rps"] < min_rps:
                fails.append(f"rps {stats['rps']} < {min_rps}")

            if fails:
                result["status"] = TestCaseStatus.FAILED.value
                result["error_message"] = "; ".join(fails)
            else:
                result["status"] = TestCaseStatus.PASSED.value

    except Exception as e:
        result["status"] = TestCaseStatus.ERROR.value
        result["error_message"] = str(e)
        result["actual_result"] = str(e)

    result["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return result


def performance_executor_node(state: AgentState) -> dict[str, Any]:
    tests = state.get("generated_tests") or []
    perf_tests = [t for t in tests if (t.get("type") or "").lower() == "performance"]

    if not perf_tests:
        console.print("[dim]Không có Performance test case nào.[/dim]")
        existing = state.get("execution_result") or ExecutionResult().model_dump()
        return {
            "execution_result": existing,
            "current_step": "performance_executor_done",
            "error": None,
        }

    base_url = settings.default_base_url
    headed = bool(state.get("ui_headed", False))
    console.print(f"\n[bold cyan]▶ Performance tests[/bold cyan] ({len(perf_tests)} cases)")

    details = list((state.get("execution_result") or {}).get("details") or [])
    passed = failed = error = 0
    total_duration = 0.0

    for test in perf_tests:
        res = _run_performance_case(test, base_url=base_url, headed=headed)
        details.append(res)
        total_duration += res["duration_ms"]
        title = res.get("title") or res.get("id")
        if res["status"] == TestCaseStatus.PASSED.value:
            passed += 1
            console.print(f"  [green]✓[/green] {res['id']} – {title} → {res.get('actual_result')}")
        elif res["status"] == TestCaseStatus.FAILED.value:
            failed += 1
            console.print(f"  [red]✗[/red] {res['id']} – {title} → {res.get('error_message')}")
        else:
            error += 1
            console.print(f"  [yellow]![/yellow] {res['id']} – {title} → {res.get('error_message')}")

    prev = state.get("execution_result") or {}
    execution = ExecutionResult(
        total=prev.get("total", 0) + len(perf_tests),
        passed=prev.get("passed", 0) + passed,
        failed=prev.get("failed", 0) + failed,
        error=prev.get("error", 0) + error,
        blocked=prev.get("blocked", 0),
        skipped=prev.get("skipped", 0),
        duration_ms=round(prev.get("duration_ms", 0) + total_duration, 2),
        details=details,
    )
    return {
        "execution_result": execution.model_dump(),
        "current_step": "performance_executor_done",
        "error": None,
    }
