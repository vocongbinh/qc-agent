from pathlib import Path
from agents.state import AgentState
from config.settings import settings


def test_agent_state_has_seed_manifest_annotation():
    assert "seed_manifest" in AgentState.__annotations__


def test_sandbox_settings_defaults():
    assert hasattr(settings, "sandbox_mode")
    assert settings.sandbox_mode == "external"
    assert hasattr(settings, "agent_yaml_path")
    assert isinstance(settings.agent_yaml_path, Path)
