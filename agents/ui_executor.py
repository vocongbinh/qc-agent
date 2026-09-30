"""UI Executor Agent – Playwright + self-healing (role/text + Vision) + Hybrid API→UI auth."""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

from rich.console import Console

from agents.state import AgentState, ExecutionResult, TestCaseStatus
from config.settings import settings

console = Console()
UI_ARTIFACTS_DIR = settings.reports_dir / "ui_artifacts"


def _ensure_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError as e:
        raise ImportError(
            "Playwright chưa được cài. Chạy: pip install playwright && playwright install chromium"
        ) from e


def _substitute_vars(value: str, context: dict) -> str:
    if not isinstance(value, str):
        return value
    for k, v in context.items():
        value = value.replace(f"{{{{{k}}}}}", str(v))
    return value


def _self_heal_role_text(page, selector: str, action_hint: str = ""):
    """Self-heal bằng role / text / placeholder / label."""
    # 1. Selector gốc
    try:
        loc = page.locator(selector)
        if loc.count() > 0:
            return loc.first
    except Exception:
        pass

    role_map = {
        "button": "button", "btn": "button", "submit": "button",
        "link": "link", "a[": "link",
        "input": "textbox", "email": "textbox", "password": "textbox",
        "text": "textbox", "search": "textbox",
        "checkbox": "checkbox", "radio": "radio",
    }
    guessed_role = None
    sel_lower = selector.lower()
    for key, role in role_map.items():
        if key in sel_lower:
            guessed_role = role
            break
    if action_hint == "click" and not guessed_role:
        guessed_role = "button"
    if action_hint == "fill" and not guessed_role:
        guessed_role = "textbox"

    hint_text = None
    for pattern in [
        r'[#.]([a-zA-Z][\w-]*)',
        r'\[name=[\'"]([^\'"]+)[\'"]\]',
        r'\[placeholder=[\'"]([^\'"]+)[\'"]\]',
        r'\[aria-label=[\'"]([^\'"]+)[\'"]\]',
        r'\[data-testid=[\'"]([^\'"]+)[\'"]\]',
    ]:
        m = re.search(pattern, selector)
        if m:
            hint_text = m.group(1).replace("-", " ").replace("_", " ")
            break

    if guessed_role:
        try:
            if hint_text:
                loc = page.get_by_role(guessed_role, name=re.compile(hint_text, re.I))
                if loc.count() > 0:
                    console.print(f"    [dim]self-heal: get_by_role({guessed_role}, ~{hint_text})[/dim]")
                    return loc.first
            loc = page.get_by_role(guessed_role)
            if loc.count() == 1:
                console.print(f"    [dim]self-heal: get_by_role({guessed_role})[/dim]")
                return loc.first
        except Exception:
            pass

    if hint_text:
        for method_name in ("get_by_placeholder", "get_by_label"):
            try:
                method = getattr(page, method_name)
                loc = method(re.compile(hint_text, re.I))
                if loc.count() > 0:
                    console.print(f"    [dim]self-heal: {method_name}(~{hint_text})[/dim]")
                    return loc.first
            except Exception:
                pass

    if hint_text and len(hint_text) > 2:
        try:
            loc = page.get_by_text(re.compile(hint_text, re.I))
            if loc.count() > 0:
                console.print(f"    [dim]self-heal: get_by_text(~{hint_text})[/dim]")
                return loc.first
        except Exception:
            pass

    return None  # để Vision fallback xử lý tiếp


def _vision_heal(page, selector: str, action_hint: str, value: str | None = None) -> str | None:
    """
    Khi role/text fail → chụp screenshot → hỏi Vision model selector mới.
    Trả về selector string gợi ý, hoặc None.
    """
    if not settings.openai_api_key:
        console.print("    [yellow]Vision heal bỏ qua (thiếu OPENAI_API_KEY)[/yellow]")
        return None

    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage

        screenshot_bytes = page.screenshot(type="png")
        b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        prompt = f"""Bạn là chuyên gia UI testing với Playwright.
Selector sau đã FAIL: `{selector}`
Hành động cần làm: {action_hint}
{"Giá trị cần điền: " + value if value and action_hint == "fill" else ""}

Nhìn screenshot trang web, hãy đề xuất **một** CSS selector hoặc Playwright selector ổn định nhất
để thực hiện hành động trên (ưu tiên: data-testid, role, placeholder, label, text rõ ràng).

Chỉ trả về đúng 1 dòng selector, không giải thích. Ví dụ:
button:has-text("Đăng nhập")
#email
[data-testid="submit-btn"]
input[placeholder="Email"]
"""

        llm = ChatOpenAI(model=settings.vision_model, temperature=0, api_key=settings.openai_api_key)
        msg = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]
        )
        resp = llm.invoke([msg])
        suggested = (resp.content or "").strip().split("\n")[0].strip().strip("`")
        if suggested and len(suggested) < 200:
            console.print(f"    [dim]vision-heal: gợi ý selector → {suggested}[/dim]")
            return suggested
    except Exception as e:
        console.print(f"    [yellow]Vision heal lỗi: {e}[/yellow]")
    return None


def _resolve_locator(page, selector: str, action_hint: str = "", value: str | None = None):
    """Thử selector gốc → role/text heal → vision heal."""
    # 1. Gốc
    try:
        loc = page.locator(selector)
        if loc.count() > 0:
            return loc.first
    except Exception:
        pass

    # 2. Role / text
    loc = _self_heal_role_text(page, selector, action_hint)
    if loc is not None:
        return loc

    # 3. Vision
    suggested = _vision_heal(page, selector, action_hint, value)
    if suggested:
        try:
            loc = page.locator(suggested)
            if loc.count() > 0:
                return loc.first
            # Thử luôn get_by_text nếu vision trả về text thuần
            loc = page.get_by_text(suggested)
            if loc.count() > 0:
                return loc.first
        except Exception:
            pass

    raise TimeoutError(f"Không tìm thấy element: {selector} (role/text + vision đều thất bại)")


def _inject_hybrid_auth(page, context: dict, base_url: str):
    """
    Hybrid API → UI: nếu có token trong shared_context thì inject vào browser
    trước khi chạy UI steps (localStorage + optional cookie).
    """
    token = context.get("token") or context.get("access_token") or context.get("accessToken")
    if not token:
        return

    console.print(f"  [dim]Hybrid auth: inject token vào browser (localStorage)[/dim]")

    # Điều hướng tạm về origin để set storage
    origin = base_url.rstrip("/")
    try:
        from urllib.parse import urlparse
        # Nếu base_url là API, thử suy ra FE origin từ context
        fe_url = context.get("frontend_url") or context.get("ui_base_url")
        if fe_url:
            origin = fe_url.rstrip("/")
        page.goto(origin, wait_until="domcontentloaded", timeout=15000)
    except Exception:
        # Không goto được thì vẫn thử add_init_script cho lần goto sau
        pass

    # set localStorage keys phổ biến
    keys = ["token", "access_token", "accessToken", "authToken", "jwt"]
    script_parts = [f'localStorage.setItem("{k}", {json.dumps(str(token))});' for k in keys]
    # Một số app lưu object
    script_parts.append(
        f'localStorage.setItem("auth", {json.dumps(json.dumps({"token": str(token)}))});'
    )
    page.evaluate("() => { " + " ".join(script_parts) + " }")

    # Cookie nếu có
    if context.get("cookie") or context.get("session_id"):
        cookie_val = context.get("cookie") or context.get("session_id")
        try:
            page.context.add_cookies([{
                "name": "session",
                "value": str(cookie_val),
                "url": origin,
            }])
        except Exception:
            pass


def _run_single_ui_test(test: dict, headed: bool = False, shared_context: dict | None = None) -> dict:
    sync_playwright = _ensure_playwright()
    start = time.perf_counter()
    result = {
        "id": test.get("id"),
        "title": test.get("title") or test.get("name"),
        "status": TestCaseStatus.UNTESTED.value,
        "error_message": None,
        "actual_result": None,
        "duration_ms": 0,
        "artifacts": [],
        "steps_run": 0,
        "self_heal_used": False,
        "vision_heal_used": False,
    }

    # Merge test_data + shared_context (API token ưu tiên merge vào)
    ctx = {}
    ctx.update(shared_context or {})
    ctx.update(test.get("test_data") or {})

    steps = test.get("steps") or []
    if not steps:
        result["status"] = TestCaseStatus.ERROR.value
        result["error_message"] = "Test case UI không có steps"
        return result

    UI_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    video_dir = UI_ARTIFACTS_DIR / f"{test.get('id', 'ui')}_{ts}"
    video_dir.mkdir(parents=True, exist_ok=True)

    page = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed)
            browser_ctx = browser.new_context(
                record_video_dir=str(video_dir),
                viewport={"width": 1280, "height": 720},
            )
            browser_ctx.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = browser_ctx.new_page()

            # Hybrid: inject auth nếu có token từ API
            base_url = settings.default_base_url
            if ctx.get("token") or ctx.get("access_token") or ctx.get("accessToken"):
                _inject_hybrid_auth(page, ctx, base_url)

            for step in steps:
                action = (step.get("action") or "").lower().strip()
                data = dict(step.get("data") or {})
                for k, v in list(data.items()):
                    if isinstance(v, str):
                        data[k] = _substitute_vars(v, ctx)

                result["steps_run"] += 1

                if action in ("goto", "navigate", "open"):
                    url = data.get("url") or data.get("value")
                    if not url:
                        raise ValueError(f"Step {step.get('step')}: goto thiếu url")
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)

                elif action == "fill":
                    selector = data.get("selector")
                    value = data.get("value", "")
                    if not selector:
                        raise ValueError(f"Step {step.get('step')}: fill thiếu selector")
                    try:
                        page.fill(selector, value, timeout=8000)
                    except Exception:
                        result["self_heal_used"] = True
                        try:
                            loc = _resolve_locator(page, selector, "fill", value)
                            # Nếu đi qua vision thì đánh dấu
                            result["vision_heal_used"] = result["vision_heal_used"] or True
                            loc.fill(value, timeout=8000)
                        except Exception:
                            # Phân biệt: resolve đã thử vision
                            result["vision_heal_used"] = True
                            raise

                elif action == "click":
                    selector = data.get("selector")
                    if not selector:
                        raise ValueError(f"Step {step.get('step')}: click thiếu selector")
                    try:
                        page.click(selector, timeout=8000)
                    except Exception:
                        result["self_heal_used"] = True
                        loc = _resolve_locator(page, selector, "click")
                        result["vision_heal_used"] = True
                        loc.click(timeout=8000)

                elif action in ("expect", "assert", "wait_for"):
                    selector = data.get("selector")
                    state = data.get("state", "visible")
                    if not selector:
                        raise ValueError(f"Step {step.get('step')}: expect thiếu selector")
                    try:
                        page.wait_for_selector(selector, state=state, timeout=12000)
                    except Exception:
                        result["self_heal_used"] = True
                        loc = _resolve_locator(page, selector, "expect")
                        result["vision_heal_used"] = True
                        loc.wait_for(state=state, timeout=12000)

                elif action == "press":
                    selector = data.get("selector")
                    key = data.get("key", "Enter")
                    if selector:
                        try:
                            page.press(selector, key)
                        except Exception:
                            result["self_heal_used"] = True
                            loc = _resolve_locator(page, selector, "press")
                            loc.press(key)
                    else:
                        page.keyboard.press(key)

                elif action == "wait":
                    page.wait_for_timeout(int(data.get("ms", 1000)))

                elif action in ("set_auth", "inject_token"):
                    # Cho phép test case chủ động inject
                    _inject_hybrid_auth(page, {**ctx, **data}, base_url)

                else:
                    console.print(f"  [yellow]Unknown UI action: {action}[/yellow]")

            screenshot_path = video_dir / "final.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            result["artifacts"].append(str(screenshot_path))

            trace_path = video_dir / "trace.zip"
            browser_ctx.tracing.stop(path=str(trace_path))
            result["artifacts"].append(str(trace_path))

            browser_ctx.close()
            browser.close()

            videos = list(video_dir.glob("*.webm"))
            if videos:
                result["artifacts"].append(str(videos[0]))

            result["status"] = TestCaseStatus.PASSED.value
            notes = []
            if result["self_heal_used"]:
                notes.append("self-heal")
            if result["vision_heal_used"]:
                notes.append("vision")
            note = f" ({', '.join(notes)})" if notes else ""
            result["actual_result"] = f"UI steps completed ({result['steps_run']} steps){note}"

    except Exception as e:
        result["status"] = TestCaseStatus.FAILED.value
        result["error_message"] = str(e)
        result["actual_result"] = str(e)
        try:
            if page:
                fail_shot = video_dir / "failure.png"
                page.screenshot(path=str(fail_shot))
                result["artifacts"].append(str(fail_shot))
        except Exception:
            pass

    result["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return result


def ui_executor_node(state: AgentState) -> dict[str, Any]:
    """Node: chạy UI tests, dùng shared_context từ API (hybrid)."""
    tests = state.get("generated_tests") or []
    ui_tests = [t for t in tests if (t.get("type") or "").lower() == "ui"]

    if not ui_tests:
        console.print("[dim]Không có UI test case nào để chạy.[/dim]")
        existing = state.get("execution_result") or ExecutionResult().model_dump()
        return {
            "execution_result": existing,
            "current_step": "ui_executor_done",
            "error": None,
        }

    headed = bool(state.get("ui_headed", False))
    shared = state.get("shared_context") or {}
    console.print(
        f"\n[bold cyan]▶ Đang chạy UI tests[/bold cyan] "
        f"({len(ui_tests)} cases, {'headed' if headed else 'headless'}"
        f"{', hybrid-auth' if shared.get('token') or shared.get('access_token') else ''})"
    )

    details = list((state.get("execution_result") or {}).get("details") or [])
    passed = failed = error = 0
    total_duration = 0.0

    for test in ui_tests:
        res = _run_single_ui_test(test, headed=headed, shared_context=shared)
        details.append(res)
        total_duration += res["duration_ms"]

        title = res.get("title") or res.get("id")
        tags = []
        if res.get("self_heal_used"):
            tags.append("self-heal")
        if res.get("vision_heal_used"):
            tags.append("vision")
        tag_str = f" [{', '.join(tags)}]" if tags else ""

        if res["status"] == TestCaseStatus.PASSED.value:
            passed += 1
            console.print(f"  [green]✓[/green] {res['id']} – {title} ({res['duration_ms']}ms){tag_str}")
        else:
            failed += 1
            console.print(f"  [red]✗[/red] {res['id']} – {title} → {res['error_message']}")

    prev = state.get("execution_result") or {}
    execution = ExecutionResult(
        total=prev.get("total", 0) + len(ui_tests),
        passed=prev.get("passed", 0) + passed,
        failed=prev.get("failed", 0) + failed,
        error=prev.get("error", 0) + error,
        skipped=prev.get("skipped", 0),
        blocked=prev.get("blocked", 0),
        duration_ms=round(prev.get("duration_ms", 0) + total_duration, 2),
        details=details,
    )

    return {
        "execution_result": execution.model_dump(),
        "current_step": "ui_executor_done",
        "error": None,
    }
