"""Tests for the hybrid intent router + conversational assistant (OpenCode style).

The LLM router (agents/router.py) decides intent; the assistant produces chat
replies. These tests avoid network by forcing provider "none" so routing falls
back to "answer" and the response uses the offline fallback.
"""

import pytest
from textual.widgets import Input

from agents.assistant import generate_conversational_response
from agents.router import route_intent
from tui.app import QCTApp


def test_router_falls_back_to_answer_without_provider(monkeypatch):
    import agents.router as router
    monkeypatch.setattr(router, "get_active_provider", lambda: "none")
    # No provider → always safe default, never forces a test run.
    assert route_intent("hi") == "answer"
    assert route_intent("Test login API") == "answer"
    assert route_intent("") == "answer"


def test_fallback_response_is_informative(monkeypatch):
    import agents.assistant as assistant
    monkeypatch.setattr(assistant, "get_active_provider", lambda: "none")
    reply = generate_conversational_response("hi")
    assert "QC Agent" in reply
    assert "API Testing" in reply


class FakeGraph:
    def stream(self, *args, **kwargs):
        return []


async def test_greeting_does_not_start_a_run(tmp_path, monkeypatch):
    # Provider "none" → router returns "answer" → chat reply, no pipeline.
    import agents.router as router
    import agents.assistant as assistant
    monkeypatch.setattr(router, "get_active_provider", lambda: "none")
    monkeypatch.setattr(assistant, "get_active_provider", lambda: "none")

    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.action_focus_request()
        await pilot.pause()
        inp = a.query_one("#request", Input)
        inp.value = "hi"
        await pilot.press("enter")
        import asyncio
        await asyncio.sleep(0.05)
        await pilot.pause()

        assert a.running is False
        assert a.runner is None
        assert any("QC Agent" in line for line in a.log_pane.lines)


async def test_run_intent_starts_pipeline_with_phase(tmp_path, monkeypatch):
    # Force router to a specific run intent; verify it starts the run and
    # selects only that phase.
    import agents.router as router
    monkeypatch.setattr(router, "route_intent", lambda text: "run_api")

    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.action_focus_request()
        await pilot.pause()
        inp = a.query_one("#request", Input)
        inp.value = "run the api tests"
        await pilot.press("enter")
        import asyncio
        await asyncio.sleep(0.05)
        await pilot.pause()
        # Phase was narrowed to api, and the prompt was dispatched to the pipeline
        # (running may already have settled with the instant FakeGraph).
        assert a.footer.selected_phases() == ["api"]
        assert any("run the api tests" in line for line in a.log_pane.lines)
