from pathlib import Path
from agents.retriever import codeintel_retriever_node
from agents.state import AgentState
from config.settings import settings
from codeintel.indexer.builder import build_index
from codeintel.store.db import reset_connection

def test_codeintel_retriever_passthrough_when_disabled():
    state: AgentState = {
        "user_request": "Test orders",
        "documents": [],
        "code_paths": ["service/order.go"],
        "openapi_spec": None,
        "messages": [],
        "test_plan": None,
        "generated_tests": [],
        "human_approved": False,
        "shared_context": {},
        "execution_result": None,
        "report_path": None,
        "final_summary": None,
        "current_step": "init",
        "error": None,
        "ui_headed": False,
    }
    settings.enable_codeintel = False
    result = codeintel_retriever_node(state)
    assert result.get("code_intelligence_summary") is None

def test_codeintel_retriever_extracts_summary_when_enabled(tmp_path: Path):
    sample_root = Path("testdata/sample_go")
    target_db = tmp_path / "codeintel_kuzu"
    build_index(repo_root=sample_root, db_path=target_db)

    orig_enable = settings.enable_codeintel
    orig_db_path = settings.codeintel_db_path
    try:
        settings.enable_codeintel = True
        settings.codeintel_db_path = target_db

        state: AgentState = {
            "user_request": "Test orders",
            "documents": [],
            "code_paths": ["service/order.go"],
            "openapi_spec": None,
            "messages": [],
            "test_plan": None,
            "generated_tests": [],
            "human_approved": False,
            "shared_context": {},
            "execution_result": None,
            "report_path": None,
            "final_summary": None,
            "current_step": "init",
            "error": None,
            "ui_headed": False,
        }

        result = codeintel_retriever_node(state)
        assert result.get("code_intelligence_summary") is not None
        summary = result["code_intelligence_summary"]
        assert "target_functions" in summary
        assert len(summary["target_functions"]) >= 1
    finally:
        settings.enable_codeintel = orig_enable
        settings.codeintel_db_path = orig_db_path
        reset_connection()
