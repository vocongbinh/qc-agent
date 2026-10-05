from unittest.mock import MagicMock
import pytest
import typer

import main


def test_interactive_login_prompt_choice_exit(monkeypatch):
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *args, **kwargs: "0")
    with pytest.raises(typer.Exit) as exc:
        main._interactive_login_prompt()
    assert exc.value.exit_code == 0


def test_interactive_login_prompt_choice_openai_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda prompt, **kwargs: "sk-my-secret-test-key" if "sk-" in prompt else "2")
    
    main._interactive_login_prompt()
    assert main.settings.openai_api_key == "sk-my-secret-test-key"
    assert main.settings.llm_provider == "openai"
    env_file = tmp_path / ".env"
    assert "sk-my-secret-test-key" in env_file.read_text(encoding="utf-8")
