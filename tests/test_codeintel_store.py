from __future__ import annotations

from pathlib import Path
import kuzu
import pytest

from codeintel.store.db import execute_query, get_codeintel_db, reset_connection
from codeintel.store.ddl import init_schema
from config.settings import settings


def test_kuzudb_init_and_query(tmp_path: Path) -> None:
    db_path = tmp_path / "test_kuzu"
    # Writer phase
    init_schema(str(db_path))
    reset_connection()

    # Reader phase
    rows = execute_query(str(db_path), "MATCH (f:Function) RETURN count(f) AS cnt")
    assert len(rows) == 1
    assert rows[0]["cnt"] == 0


def test_init_schema_idempotency(tmp_path: Path) -> None:
    db_path = tmp_path / "test_kuzu_idempotent"
    init_schema(db_path)
    # Calling init_schema again should not raise errors due to IF NOT EXISTS
    init_schema(db_path)
    reset_connection()

    rows = execute_query(db_path, "MATCH (f:Function) RETURN count(f) AS cnt")
    assert len(rows) == 1
    assert rows[0]["cnt"] == 0


def test_reset_connection_and_singleton(tmp_path: Path) -> None:
    db1_path = tmp_path / "db1"
    db2_path = tmp_path / "db2"
    init_schema(db1_path)
    init_schema(db2_path)
    reset_connection()

    # First access
    instance1 = get_codeintel_db(db1_path)
    # Second access with same path returns cached instance
    instance2 = get_codeintel_db(db1_path)
    assert instance1 is instance2

    # Reset connection clears cached instance
    reset_connection()
    instance3 = get_codeintel_db(db1_path)
    assert instance3 is not instance1

    # Accessing different path replaces instance
    instance4 = get_codeintel_db(db2_path)
    assert instance4 is not instance3
    reset_connection()


def test_insert_and_query_with_parameters(tmp_path: Path) -> None:
    db_path = tmp_path / "test_kuzu_params"
    init_schema(db_path)
    reset_connection()

    # Insert test data using writer connection
    write_db = kuzu.Database(str(db_path), read_only=False)
    conn = kuzu.Connection(write_db)
    conn.execute(
        """
        CREATE (:File {path: 'pkg/service.go', language: 'go'})
        """
    )
    conn.execute(
        """
        CREATE (:Function {
            id: 'func_1',
            name: 'ProcessPayment',
            package: 'pkg',
            signature: 'func ProcessPayment(amount int) bool',
            file_path: 'pkg/service.go',
            start_line: 10,
            end_line: 25,
            start_byte: 150,
            end_byte: 450,
            cyclomatic_complexity: 4,
            branch_count: 3
        })
        """
    )
    conn.execute(
        """
        CREATE (:Function {
            id: 'func_2',
            name: 'ValidateCard',
            package: 'pkg',
            signature: 'func ValidateCard(card string) bool',
            file_path: 'pkg/service.go',
            start_line: 30,
            end_line: 40,
            start_byte: 500,
            end_byte: 700,
            cyclomatic_complexity: 2,
            branch_count: 1
        })
        """
    )
    # Connect CONTAINS and CALLS
    conn.execute(
        """
        MATCH (file:File), (fn:Function)
        WHERE file.path = 'pkg/service.go' AND fn.id = 'func_1'
        CREATE (file)-[:CONTAINS]->(fn)
        """
    )
    conn.execute(
        """
        MATCH (f1:Function), (f2:Function)
        WHERE f1.id = 'func_1' AND f2.id = 'func_2'
        CREATE (f1)-[:CALLS {line_number: 18, is_external: false}]->(f2)
        """
    )
    del conn
    del write_db
    reset_connection()

    # Query with parameters
    query = """
    MATCH (caller:Function)-[c:CALLS]->(callee:Function)
    WHERE caller.name = $caller_name
    RETURN caller.name AS caller, callee.name AS callee, c.line_number AS line
    """
    rows = execute_query(db_path, query, {"caller_name": "ProcessPayment"})
    assert len(rows) == 1
    assert rows[0]["caller"] == "ProcessPayment"
    assert rows[0]["callee"] == "ValidateCard"
    assert rows[0]["line"] == 18

    # Query with non-matching parameter
    empty_rows = execute_query(db_path, query, {"caller_name": "NonExistent"})
    assert len(empty_rows) == 0
    reset_connection()


def test_default_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default_path = tmp_path / "default_codeintel_db"
    monkeypatch.setattr(settings, "codeintel_db_path", default_path)
    init_schema(default_path)
    reset_connection()

    # execute_query with db_path=None should use settings.codeintel_db_path
    rows = execute_query(None, "MATCH (f:Function) RETURN count(f) AS cnt")
    assert len(rows) == 1
    assert rows[0]["cnt"] == 0
    reset_connection()


def test_readonly_connection_rejects_write(tmp_path: Path) -> None:
    db_path = tmp_path / "readonly_test_db"
    init_schema(db_path)
    reset_connection()

    with pytest.raises(RuntimeError):
        execute_query(db_path, "CREATE (:File {path: 'evil.go', language: 'go'})")
    reset_connection()
