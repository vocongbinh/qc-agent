"""Test cho CLI entrypoint (`main.py`).

Hai thứ được gác ở đây:

1. `tui` được đăng ký như một lệnh Typer bình thường, và nhánh thiếu
   `OPENAI_API_KEY` thoát non-zero mà KHÔNG dựng `QCTApp` (app thật chạy
   event loop của Textual, không được phép chạy trong test).
2. `run`/`version` còn nguyên, và `import main` KHÔNG kéo theo `tui` hay
   `textual` — nếu không thì `main.py run` trả giá import Textual và chết
   nếu Textual chưa cài.

Vì `main.py` in qua `console = Console()` ở module level (không giữ
`sys.stdout` cứng), Rich resolve file lúc ghi nên `CliRunner` vẫn bắt được
output. Test `test_lazy_import_...` chạy subprocess vì pytest đã import
`textual` rồi — `sys.modules` trong process test không còn sạch.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import main
import tui.app as tui_app
import tui.bus as tui_bus

runner = CliRunner()


def _command_names() -> set[str]:
    """Tên lệnh Typer suy từ callback — `CommandInfo.name` là None khi
    `@app.command()` không truyền tên, nên phải tự suy ra như Typer làm."""
    return {
        (info.name or info.callback.__name__).replace("_", "-")
        for info in main.app.registered_commands
    }


class _QCTAppSpy:
    """Thay `tui.app.QCTApp`. `run()` chỉ ghi lại — app thật không bao giờ
    được dựng trong test."""

    def __init__(self) -> None:
        self.constructed = 0
        self.ran = 0

    def __call__(self, *args, **kwargs) -> "_QCTAppSpy":
        self.constructed += 1
        return self

    def run(self, *args, **kwargs) -> None:
        self.ran += 1


@pytest.fixture
def spy_app(monkeypatch) -> _QCTAppSpy:
    spy = _QCTAppSpy()
    monkeypatch.setattr(tui_app, "QCTApp", spy)
    return spy


@pytest.fixture
def no_api_key(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "openai_api_key", None)


# ----- registration -----


def test_tui_command_is_registered():
    assert "tui" in _command_names()


def test_run_and_version_are_still_registered():
    """CI và người dùng vẫn gọi `run`; đừng đổi tên nó theo."""
    assert {"run", "version"} <= _command_names()


def test_registered_commands_are_exactly_run_version_tui():
    assert _command_names() == {"run", "version", "tui"}


def test_app_help_advertises_the_tui():
    result = runner.invoke(main.app, ["--help"])
    assert result.exit_code == 0
    assert "tui" in result.stdout
    # Help cấp app không còn nói "Phase 1+2" nữa.
    assert "Phase 1+2" not in result.stdout


# ----- tui: nhánh thiếu key -----


def test_tui_without_api_key_exits_nonzero(no_api_key, spy_app):
    result = runner.invoke(main.app, ["tui"])
    assert result.exit_code != 0
    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.stdout


def test_tui_without_api_key_never_builds_the_app(no_api_key, spy_app):
    runner.invoke(main.app, ["tui"])
    assert spy_app.constructed == 0
    assert spy_app.ran == 0


def test_tui_without_api_key_mentions_the_env_file(no_api_key, spy_app):
    """Thông báo phải chỉ đường thoát cho người dùng, không chỉ báo lỗi."""
    result = runner.invoke(main.app, ["tui"])
    assert ".env" in result.stdout


# ----- tui: nhánh có key -----


def test_tui_starts_the_app_when_key_is_present(monkeypatch, spy_app):
    monkeypatch.setattr(main.settings, "openai_api_key", "sk-test")
    result = runner.invoke(main.app, ["tui"])
    assert result.exit_code == 0
    assert spy_app.constructed == 1
    assert spy_app.ran == 1


def test_tui_resets_the_bus_before_running(monkeypatch, spy_app):
    """Bus là singleton module-level. Không reset thì state của lần chạy
    trước trong cùng process rò sang lần này."""
    monkeypatch.setattr(main.settings, "openai_api_key", "sk-test")
    stale = object()
    monkeypatch.setattr(tui_bus, "_bus", stale)

    result = runner.invoke(main.app, ["tui"])

    assert result.exit_code == 0
    assert tui_bus.get_bus() is not stale


# ----- help rendering -----


def test_tui_help_renders():
    result = runner.invoke(main.app, ["tui", "--help"])
    assert result.exit_code == 0
    assert "Mở giao diện TUI dashboard" in result.stdout


def test_run_help_still_renders():
    """`run` phải giữ nguyên help — đây là thứ CI dựa vào."""
    result = runner.invoke(main.app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--ci" in result.stdout
    assert "request" in result.stdout


def test_version_command_still_runs():
    result = runner.invoke(main.app, ["version"])
    assert result.exit_code == 0
    assert "QC Agent" in result.stdout


# ----- lazy import -----


def test_importing_main_does_not_pull_in_tui_or_textual():
    """Chạy subprocess: process pytest đã import `textual` rồi, nên
    `sys.modules` ở đây không còn sạch để assert."""
    code = (
        "import sys, main;"
        "leaked = sorted(m for m in sys.modules if m == 'tui' or m.startswith(('tui.', 'textual')));"
        "print(leaked);"
        "assert not leaked, leaked"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(Path(main.__file__).parent),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"
