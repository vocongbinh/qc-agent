from __future__ import annotations

from pathlib import Path
import kuzu
import pytest

from codeintel.indexer.builder import build_index
from codeintel.store.db import reset_connection
from codeintel.tools import (
    inspect_function_branches,
    get_external_dependencies,
    trace_execution_path,
    get_function_source,
    list_scope_functions,
)


def test_codeintel_tools_suite(tmp_path: Path):
    sample_root = Path("testdata/sample_go")
    target_db = tmp_path / "codeintel_kuzu"
    build_index(repo_root=sample_root, db_path=target_db)
    reset_connection()

    # 1. list_scope_functions
    res_list = list_scope_functions(db_path=target_db)
    assert res_list["ok"] is True
    assert len(res_list["data"]) >= 2
    assert res_list["error"] is None

    # Filter by package
    res_pkg = list_scope_functions(package="service", db_path=target_db)
    assert res_pkg["ok"] is True
    assert all(f["package"] == "service" for f in res_pkg["data"])
    assert len(res_pkg["data"]) >= 1

    # Filter by complexity
    res_cc = list_scope_functions(min_complexity=3, db_path=target_db)
    assert res_cc["ok"] is True
    assert all(f["complexity"] >= 3 for f in res_cc["data"])

    # 2. inspect_function_branches (JIT)
    proc_order_id = [f["id"] for f in res_list["data"] if f["name"] == "ProcessOrder"][0]
    res_branches = inspect_function_branches(proc_order_id, repo_root=sample_root, db_path=target_db)
    assert res_branches["ok"] is True
    assert len(res_branches["data"]["branches"]) == 2  # 2 if statements in order.go
    assert res_branches["error"] is None
    conditions = [b["condition"] for b in res_branches["data"]["branches"]]
    assert any("order == nil" in c for c in conditions)

    # 3. get_function_source
    res_src = get_function_source(proc_order_id, repo_root=sample_root, db_path=target_db)
    assert res_src["ok"] is True
    assert "func (s *OrderService) ProcessOrder" in res_src["data"]["source"]
    assert res_src["error"] is None

    # 4. get_function_source truncation
    res_trunc = get_function_source(proc_order_id, max_lines=3, repo_root=sample_root, db_path=target_db)
    assert res_trunc["ok"] is True
    assert "// ... [truncated by codeintel]" in res_trunc["data"]["source"]


def test_trace_execution_path_and_dependencies(tmp_path: Path):
    sample_root = Path("testdata/sample_go")
    target_db = tmp_path / "codeintel_kuzu"
    build_index(repo_root=sample_root, db_path=target_db)
    reset_connection()

    res_list = list_scope_functions(db_path=target_db)
    proc_order_id = [f["id"] for f in res_list["data"] if f["name"] == "ProcessOrder"][0]
    main_id = [f["id"] for f in res_list["data"] if f["name"] == "main"][0]

    # Add a CALLS edge from main to ProcessOrder for testing
    db = kuzu.Database(str(target_db), read_only=False)
    conn = kuzu.Connection(db)
    conn.execute(
        f"MATCH (src:Function {{id: '{main_id}'}}), (dst:Function {{id: '{proc_order_id}'}}) "
        f"CREATE (src)-[:CALLS {{line_number: 10, is_external: false}}]->(dst)"
    )
    del conn
    del db
    reset_connection()

    # Test get_external_dependencies
    res_deps = get_external_dependencies(main_id, db_path=target_db)
    assert res_deps["ok"] is True
    assert res_deps["error"] is None
    callees = res_deps["data"]["callees"]
    assert len(callees) == 1
    assert callees[0]["callee_id"] == proc_order_id
    assert callees[0]["name"] == "ProcessOrder"

    # Test trace_execution_path (existing path)
    res_path = trace_execution_path(main_id, proc_order_id, db_path=target_db)
    assert res_path["ok"] is True
    assert res_path["error"] is None
    assert res_path["data"]["source"] == main_id
    assert res_path["data"]["sink"] == proc_order_id
    assert res_path["data"]["path"] == [main_id, proc_order_id]

    # Test trace_execution_path (clean handling of non-existent path)
    res_no_path = trace_execution_path(proc_order_id, main_id, db_path=target_db)
    assert res_no_path["ok"] is True
    assert res_no_path["error"] is None
    assert res_no_path["data"]["path"] == []


def test_path_traversal_protection(tmp_path: Path):
    sample_root = Path("testdata/sample_go")
    target_db = tmp_path / "codeintel_kuzu"
    build_index(repo_root=sample_root, db_path=target_db)
    reset_connection()

    # Insert a malicious function with path traversal file_path
    evil_func_id = "evil::Func::1"
    db = kuzu.Database(str(target_db), read_only=False)
    conn = kuzu.Connection(db)
    conn.execute(
        f"CREATE (:Function {{"
        f"  id: '{evil_func_id}', name: 'EvilFunc', package: 'evil', signature: 'func Evil()', "
        f"  file_path: '../../etc/passwd', start_line: 1, end_line: 5, start_byte: 0, end_byte: 50, "
        f"  cyclomatic_complexity: 1, branch_count: 0"
        f"}})"
    )
    del conn
    del db
    reset_connection()

    # get_function_source should block access
    res_src = get_function_source(evil_func_id, repo_root=sample_root, db_path=target_db)
    assert res_src["ok"] is False
    assert res_src["data"] is None
    assert "traversal" in res_src["error"].lower() or "access denied" in res_src["error"].lower()

    # inspect_function_branches should also block access
    res_branches = inspect_function_branches(evil_func_id, repo_root=sample_root, db_path=target_db)
    assert res_branches["ok"] is False
    assert res_branches["data"] is None
    assert "traversal" in res_branches["error"].lower() or "access denied" in res_branches["error"].lower()


def test_function_not_found(tmp_path: Path):
    sample_root = Path("testdata/sample_go")
    target_db = tmp_path / "codeintel_kuzu"
    build_index(repo_root=sample_root, db_path=target_db)
    reset_connection()

    missing_id = "missing.go::DoesNotExist::999"

    res_b = inspect_function_branches(missing_id, repo_root=sample_root, db_path=target_db)
    assert res_b["ok"] is False
    assert res_b["data"] is None
    assert "not found" in res_b["error"].lower()

    res_d = get_external_dependencies(missing_id, db_path=target_db)
    assert res_d["ok"] is False
    assert res_d["data"] is None
    assert "not found" in res_d["error"].lower()

    res_p1 = trace_execution_path(missing_id, "main.go::main::8", db_path=target_db)
    assert res_p1["ok"] is False
    assert res_p1["data"] is None
    assert "not found" in res_p1["error"].lower()

    res_p2 = trace_execution_path("main.go::main::8", missing_id, db_path=target_db)
    assert res_p2["ok"] is False
    assert res_p2["data"] is None
    assert "not found" in res_p2["error"].lower()

    res_s = get_function_source(missing_id, repo_root=sample_root, db_path=target_db)
    assert res_s["ok"] is False
    assert res_s["data"] is None
    assert "not found" in res_s["error"].lower()


def test_db_does_not_exist(tmp_path: Path):
    non_existent = tmp_path / "missing_db"

    res_list = list_scope_functions(db_path=non_existent)
    assert res_list["ok"] is False
    assert res_list["data"] is None
    assert res_list["error"] is not None

    res_b = inspect_function_branches("any_id", db_path=non_existent)
    assert res_b["ok"] is False
    assert res_b["data"] is None
    assert res_b["error"] is not None

    res_d = get_external_dependencies("any_id", db_path=non_existent)
    assert res_d["ok"] is False
    assert res_d["data"] is None
    assert res_d["error"] is not None

    res_p = trace_execution_path("src", "sink", db_path=non_existent)
    assert res_p["ok"] is False
    assert res_p["data"] is None
    assert res_p["error"] is not None

    res_s = get_function_source("any_id", db_path=non_existent)
    assert res_s["ok"] is False
    assert res_s["data"] is None
    assert res_s["error"] is not None
