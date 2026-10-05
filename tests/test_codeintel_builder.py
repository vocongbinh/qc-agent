from pathlib import Path
from typer.testing import CliRunner

from codeintel.indexer.builder import build_index
from codeintel.store.db import execute_query, reset_connection
from main import app


def test_build_index_end_to_end(tmp_path: Path):
    sample_root = Path("testdata/sample_go")
    target_db = tmp_path / "codeintel_kuzu"

    stats = build_index(repo_root=sample_root, db_path=target_db)
    reset_connection()

    assert stats["files_indexed"] >= 2
    assert stats["functions_indexed"] >= 2
    assert (target_db).exists()

    rows = execute_query(target_db, "MATCH (f:Function) RETURN f.name AS name")
    names = [r["name"] for r in rows]
    assert "ProcessOrder" in names
    assert "main" in names


def test_build_index_idempotency(tmp_path: Path):
    sample_root = Path("testdata/sample_go")
    target_db = tmp_path / "codeintel_kuzu_idempotent"

    stats1 = build_index(repo_root=sample_root, db_path=target_db)
    reset_connection()
    stats2 = build_index(repo_root=sample_root, db_path=target_db)
    reset_connection()

    assert stats1 == stats2
    rows = execute_query(target_db, "MATCH (f:Function) RETURN f.name AS name")
    assert len(rows) == stats1["functions_indexed"]


def test_index_cli_help():
    runner = CliRunner()
    result = runner.invoke(app, ["index", "--help"])
    assert result.exit_code == 0
    assert "Xây dựng Code Intelligence Graph" in result.output
