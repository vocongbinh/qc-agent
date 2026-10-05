from pathlib import Path
from config.settings import settings
from agents.state import AgentState

def test_codeintel_settings_defaults():
    assert hasattr(settings, "enable_codeintel")
    assert isinstance(settings.enable_codeintel, bool)
    assert hasattr(settings, "codeintel_db_path")
    assert isinstance(settings.codeintel_db_path, Path)

def test_agent_state_has_code_intelligence_summary():
    annotations = AgentState.__annotations__
    assert "code_intelligence_summary" in annotations
