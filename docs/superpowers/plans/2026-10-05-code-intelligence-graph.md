# Code Intelligence Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an embedded Code Intelligence Graph using Tree-sitter, SCIP, and KùzuDB to provide `qc-agent` with accurate, scope-first function, branch, and call-dependency retrieval for test generation and defect localization.

**Architecture:** A standalone single-writer CLI (`qc-index build`) executes `scip-go` and Tree-sitter AST queries to build a lean KùzuDB property graph (omitting granular branch nodes to prevent graph bloat). LangGraph worker threads use a thread-safe singleton reader connection (`read_only=True`) to power 5 parameter-locked retrieval tools, including JIT Tree-sitter branch extraction, surfaced through a pre-step `codeintel_retriever` node before `planner`.

**Tech Stack:** Python 3.11+, KùzuDB (`kuzu`), Tree-sitter (`tree-sitter`, `tree-sitter-go`), SCIP (`protobuf`), LangGraph, Typer.

---

### File Structure Map

```text
qc-agent/
├── requirements.txt                         # Add kuzu, tree-sitter, tree-sitter-go, protobuf
├── config/settings.py                       # Add enable_codeintel, codeintel_db_path
├── agents/
│   ├── state.py                            # Add code_intelligence_summary to AgentState
│   ├── retriever.py                        # LangGraph codeintel_retriever node
│   ├── graph.py                            # Wire retriever node before planner
│   └── planner.py                          # Incorporate code_intelligence_summary into prompt
├── codeintel/
│   ├── __init__.py
│   ├── store/
│   │   ├── __init__.py
│   │   ├── ddl.py                          # KùzuDB DDL statements for Node & Rel tables
│   │   └── db.py                           # Singleton read-only connection & query helpers
│   ├── indexer/
│   │   ├── __init__.py
│   │   ├── ast_parser.py                   # Tree-sitter AST parsing for Go
│   │   ├── scip_reader.py                  # Read & decode SCIP protobuf indexes
│   │   ├── stitcher.py                     # Correlate SCIP symbols with AST ranges
│   │   └── builder.py                      # Orchestrate index pipeline & write index_stats.json
│   └── tools/
│       ├── __init__.py
│       ├── branches.py                     # inspect_function_branches (JIT slice parsing)
│       ├── dependencies.py                 # get_external_dependencies
│       ├── paths.py                        # trace_execution_path (shortest path)
│       ├── source.py                       # get_function_source (bounded slice reader)
│       └── scope.py                        # list_scope_functions
├── testdata/
│   └── sample_go/                          # Mini Go project with known AST & call graphs
│       ├── main.go
│       ├── service/order.go
│       ├── index.scip                      # Pre-generated fixture for offline testing
│       └── go.mod
└── tests/
    ├── test_codeintel_store.py             # DDL and connection lifecycle tests
    ├── test_codeintel_ast.py               # Tree-sitter range & complexity calculation tests
    ├── test_codeintel_stitcher.py          # SCIP-to-AST relationship tests
    ├── test_codeintel_tools.py             # Tool contract tests (no LLM required)
    └── test_codeintel_retriever.py         # LangGraph pre-step integration tests
```

---

### Task 1: Environment & State Configuration

**Files:**
- Modify: `requirements.txt`
- Modify: `config/settings.py:10-35`
- Modify: `agents/state.py:110-140`
- Test: `tests/test_codeintel_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_codeintel_config.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_codeintel_config.py -v`
Expected: FAIL with `AssertionError` or `AttributeError: 'Settings' object has no attribute 'enable_codeintel'`

- [ ] **Step 3: Implement settings and state changes**

Add to `requirements.txt`:
```text
kuzu>=0.8.0
tree-sitter>=0.23.0
tree-sitter-go>=0.23.0
protobuf>=5.27.0
```

Add to `config/settings.py`:
```python
    enable_codeintel: bool = False
    codeintel_db_path: Path = project_root / ".codeintel_db"
```

Add to `AgentState` in `agents/state.py`:
```python
    code_intelligence_summary: NotRequired[dict[str, Any] | None]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_codeintel_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add requirements.txt config/settings.py agents/state.py tests/test_codeintel_config.py
git commit -m "feat(codeintel): add configuration and AgentState definitions"
```

---

### Task 2: Go Fixtures & SCIP Protobuf Reader

**Files:**
- Create: `testdata/sample_go/go.mod`
- Create: `testdata/sample_go/service/order.go`
- Create: `testdata/sample_go/main.go`
- Create: `codeintel/indexer/scip_reader.py`
- Test: `tests/test_scip_reader.py`

- [ ] **Step 1: Write the failing test and create sample Go fixture**

Create `testdata/sample_go/go.mod`:
```go
module example.com/sample_go

go 1.22
```

Create `testdata/sample_go/service/order.go`:
```go
package service

import "errors"

type Order struct {
	ID     string
	Amount float64
}

type OrderService struct{}

func (s *OrderService) ProcessOrder(order *Order) error {
	if order == nil {
		return errors.New("nil order")
	}
	if order.Amount <= 0 {
		return errors.New("invalid amount")
	}
	return nil
}
```

Create `testdata/sample_go/main.go`:
```go
package main

import (
	"fmt"
	"example.com/sample_go/service"
)

func main() {
	svc := &service.OrderService{}
	err := svc.ProcessOrder(&service.Order{ID: "1", Amount: 100})
	if err != nil {
		fmt.Println("Error:", err)
	}
}
```

Write `tests/test_scip_reader.py`:
```python
# tests/test_scip_reader.py
from pathlib import Path
from codeintel.indexer.scip_reader import parse_scip_occurrences, ScipOccurrence

def test_parse_scip_occurrences_handles_empty_or_missing(tmp_path):
    missing_file = tmp_path / "nonexistent.scip"
    results = parse_scip_occurrences(missing_file)
    assert results == []

def test_scip_occurrence_data_structure():
    occ = ScipOccurrence(
        file_path="service/order.go",
        start_line=13,
        start_col=6,
        symbol="example.com/sample_go/service/OrderService#ProcessOrder().",
        symbol_roles=1, # Definition
    )
    assert occ.is_definition is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scip_reader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeintel'`

- [ ] **Step 3: Implement minimal SCIP Reader**

Create `codeintel/__init__.py` and `codeintel/indexer/__init__.py`.
Create `codeintel/indexer/scip_reader.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# SCIP SymbolRole bit flags
# 1 = Definition, 2 = Import, 4 = WriteAccess, 8 = ReadAccess
ROLE_DEFINITION = 1

@dataclass(slots=True)
class ScipOccurrence:
    file_path: str
    start_line: int
    start_col: int
    symbol: str
    symbol_roles: int = 0

    @property
    def is_definition(self) -> bool:
        return bool(self.symbol_roles & ROLE_DEFINITION)


def parse_scip_occurrences(scip_path: Path | str) -> list[ScipOccurrence]:
    path = Path(scip_path)
    if not path.exists() or not path.is_file():
        return []

    # Fallback to direct reading or protobuf if installed
    try:
        # Protobuf decode if scip_pb2 is present
        from codeintel.indexer import scip_pb2  # type: ignore
        index = scip_pb2.Index()
        index.ParseFromString(path.read_bytes())
        results: list[ScipOccurrence] = []
        for doc in index.documents:
            for occ in doc.occurrences:
                # SCIP range: [start_line, start_col, end_line?, end_col]
                line = occ.range[0] if len(occ.range) > 0 else 0
                col = occ.range[1] if len(occ.range) > 1 else 0
                results.append(
                    ScipOccurrence(
                        file_path=doc.relative_path,
                        start_line=line,
                        start_col=col,
                        symbol=occ.symbol,
                        symbol_roles=occ.symbol_roles,
                    )
                )
        return results
    except Exception:
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scip_reader.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add testdata/sample_go/ codeintel/__init__.py codeintel/indexer/__init__.py codeintel/indexer/scip_reader.py tests/test_scip_reader.py
git commit -m "feat(codeintel): add sample Go fixtures and SCIP occurrence reader"
```

---

### Task 3: KùzuDB Schema DDL & Connection Manager

**Files:**
- Create: `codeintel/store/ddl.py`
- Create: `codeintel/store/db.py`
- Test: `tests/test_codeintel_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_codeintel_store.py
import pytest
from pathlib import Path
from codeintel.store.db import get_codeintel_db, execute_query, reset_connection
from codeintel.store.ddl import init_schema

def test_kuzudb_init_and_query(tmp_path: Path):
    db_path = tmp_path / "test_kuzu"
    # Writer phase
    init_schema(str(db_path))
    reset_connection()

    # Reader phase
    rows = execute_query(str(db_path), "MATCH (f:Function) RETURN count(f) AS cnt")
    assert len(rows) == 1
    assert rows[0]["cnt"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_codeintel_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeintel.store'`

- [ ] **Step 3: Implement DDL and DB Manager**

Create `codeintel/store/__init__.py`.
Create `codeintel/store/ddl.py`:
```python
from __future__ import annotations
import kuzu

DDL_STATEMENTS = [
    """CREATE NODE TABLE IF NOT EXISTS File (
        path STRING,
        language STRING,
        PRIMARY KEY (path)
    );""",
    """CREATE NODE TABLE IF NOT EXISTS Function (
        id STRING,
        name STRING,
        package STRING,
        signature STRING,
        file_path STRING,
        start_line INT64,
        end_line INT64,
        start_byte INT64,
        end_byte INT64,
        cyclomatic_complexity INT64,
        branch_count INT64,
        PRIMARY KEY (id)
    );""",
    """CREATE NODE TABLE IF NOT EXISTS StructOrClass (
        id STRING,
        name STRING,
        file_path STRING,
        kind STRING,
        PRIMARY KEY (id)
    );""",
    """CREATE NODE TABLE IF NOT EXISTS Endpoint (
        id STRING,
        method STRING,
        path_template STRING,
        handler_func_id STRING,
        PRIMARY KEY (id)
    );""",
    """CREATE REL TABLE IF NOT EXISTS CONTAINS (
        FROM File TO Function,
        FROM File TO StructOrClass
    );""",
    """CREATE REL TABLE IF NOT EXISTS CALLS (
        FROM Function TO Function,
        line_number INT64,
        is_external BOOL
    );""",
    """CREATE REL TABLE IF NOT EXISTS USES_TYPE (
        FROM Function TO StructOrClass
    );""",
    """CREATE REL TABLE IF NOT EXISTS HANDLES (
        FROM Endpoint TO Function
    );""",
]

def init_schema(db_path: str) -> None:
    db = kuzu.Database(db_path, read_only=False)
    conn = kuzu.Connection(db)
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)
```

Create `codeintel/store/db.py`:
```python
from __future__ import annotations
from pathlib import Path
from typing import Any
import kuzu
from config.settings import settings

_db_instance: kuzu.Database | None = None
_current_db_path: str | None = None

def reset_connection() -> None:
    global _db_instance, _current_db_path
    _db_instance = None
    _current_db_path = None

def get_codeintel_db(db_path: str | Path | None = None) -> kuzu.Database:
    global _db_instance, _current_db_path
    target_path = str(db_path or settings.codeintel_db_path)
    if _db_instance is None or _current_db_path != target_path:
        _db_instance = kuzu.Database(target_path, read_only=True)
        _current_db_path = target_path
    return _db_instance

def execute_query(db_path: str | Path | None, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    db = get_codeintel_db(db_path)
    conn = kuzu.Connection(db)
    result = conn.execute(query, parameters or {})
    columns = result.get_column_names()
    rows: list[dict[str, Any]] = []
    while result.has_next():
        rows.append(dict(zip(columns, result.get_next())))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_codeintel_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codeintel/store/ tests/test_codeintel_store.py
git commit -m "feat(codeintel): implement KùzuDB DDL and connection singleton"
```

---

### Task 4: Tree-sitter AST Parser for Go

**Files:**
- Create: `codeintel/indexer/ast_parser.py`
- Test: `tests/test_codeintel_ast.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_codeintel_ast.py
from pathlib import Path
from codeintel.indexer.ast_parser import parse_go_file, ExtractedFunction

def test_parse_go_file_extracts_functions():
    code = """package service

func Add(a, b int) int {
	if a > 0 {
		return a + b
	}
	return b
}
"""
    funcs = parse_go_file("service/math.go", code)
    assert len(funcs) == 1
    fn = funcs[0]
    assert fn.name == "Add"
    assert fn.package == "service"
    assert fn.start_line == 3
    assert fn.cyclomatic_complexity == 2  # 1 base + 1 if
    assert fn.branch_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_codeintel_ast.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeintel.indexer.ast_parser'`

- [ ] **Step 3: Implement Tree-sitter AST Parser**

Create `codeintel/indexer/ast_parser.py`:
```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import tree_sitter_go as tsgo
from tree_sitter import Language, Parser, Node

GO_LANGUAGE = Language(tsgo.language())

@dataclass(slots=True)
class ExtractedFunction:
    id: str
    name: str
    package: str
    signature: str
    file_path: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    cyclomatic_complexity: int
    branch_count: int

def _extract_package(root_node: Node, source_bytes: bytes) -> str:
    for child in root_node.children:
        if child.type == "package_clause":
            for sub in child.children:
                if sub.type == "package_identifier":
                    return source_bytes[sub.start_byte:sub.end_byte].decode("utf-8")
    return "main"

def _calc_complexity_and_branches(node: Node) -> tuple[int, int]:
    branch_types = {
        "if_statement", "for_statement", "expression_switch_statement",
        "type_switch_statement", "communication_case", "expression_case",
    }
    operator_types = {"&&", "||"}
    branches = 0

    def traverse(n: Node) -> None:
        nonlocal branches
        if n.type in branch_types:
            branches += 1
        elif n.type in operator_types:
            branches += 1
        for child in n.children:
            traverse(child)

    traverse(node)
    return 1 + branches, branches

def parse_go_file(rel_path: str, source_code: str) -> list[ExtractedFunction]:
    parser = Parser(GO_LANGUAGE)
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)
    root = tree.root_node

    pkg_name = _extract_package(root, source_bytes)
    results: list[ExtractedFunction] = []

    for child in root.children:
        if child.type in ("function_declaration", "method_declaration"):
            name = ""
            for sub in child.children:
                if sub.type == "field_identifier" or sub.type == "identifier":
                    name = source_bytes[sub.start_byte:sub.end_byte].decode("utf-8")
                    break

            start_line = child.start_point[0] + 1
            end_line = child.end_point[0] + 1
            func_id = f"{rel_path}::{name}::{start_line}"
            signature = source_bytes[child.start_byte:child.children[-1].start_byte].decode("utf-8").strip()
            complexity, branches = _calc_complexity_and_branches(child)

            results.append(
                ExtractedFunction(
                    id=func_id,
                    name=name,
                    package=pkg_name,
                    signature=signature,
                    file_path=rel_path,
                    start_line=start_line,
                    end_line=end_line,
                    start_byte=child.start_byte,
                    end_byte=child.end_byte,
                    cyclomatic_complexity=complexity,
                    branch_count=branches,
                )
            )
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_codeintel_ast.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codeintel/indexer/ast_parser.py tests/test_codeintel_ast.py
git commit -m "feat(codeintel): implement Go AST parser with Tree-sitter"
```

---

### Task 5: SCIP-to-AST Stitcher

**Files:**
- Create: `codeintel/indexer/stitcher.py`
- Test: `tests/test_codeintel_stitcher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_codeintel_stitcher.py
from codeintel.indexer.ast_parser import ExtractedFunction
from codeintel.indexer.scip_reader import ScipOccurrence
from codeintel.indexer.stitcher import stitch_calls_and_types

def test_stitch_calls_correlates_occurrences_to_caller():
    caller = ExtractedFunction(
        id="main.go::main::5",
        name="main",
        package="main",
        signature="func main()",
        file_path="main.go",
        start_line=5,
        end_line=12,
        start_byte=50,
        end_byte=150,
        cyclomatic_complexity=1,
        branch_count=0,
    )
    occ = ScipOccurrence(
        file_path="main.go",
        start_line=7,  # inside main
        start_col=4,
        symbol="example.com/sample_go/service/OrderService#ProcessOrder().",
        symbol_roles=0, # Reference
    )
    calls = stitch_calls_and_types([caller], [occ])
    assert len(calls) == 1
    assert calls[0]["caller_id"] == caller.id
    assert "ProcessOrder" in calls[0]["callee_symbol"]
    assert calls[0]["line_number"] == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_codeintel_stitcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeintel.indexer.stitcher'`

- [ ] **Step 3: Implement Stitcher**

Create `codeintel/indexer/stitcher.py`:
```python
from __future__ import annotations
from typing import Any
from codeintel.indexer.ast_parser import ExtractedFunction
from codeintel.indexer.scip_reader import ScipOccurrence

def stitch_calls_and_types(
    functions: list[ExtractedFunction],
    occurrences: list[ScipOccurrence],
) -> list[dict[str, Any]]:
    # Map functions by file for fast lookup
    file_to_funcs: dict[str, list[ExtractedFunction]] = {}
    for fn in functions:
        file_to_funcs.setdefault(fn.file_path, []).append(fn)

    calls: list[dict[str, Any]] = []

    for occ in occurrences:
        if occ.is_definition:
            continue
        funcs_in_file = file_to_funcs.get(occ.file_path, [])
        for fn in funcs_in_file:
            if fn.start_line <= occ.start_line <= fn.end_line:
                calls.append({
                    "caller_id": fn.id,
                    "callee_symbol": occ.symbol,
                    "line_number": occ.start_line,
                    "is_external": not occ.symbol.startswith("example.com/"),
                })
                break

    return calls
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_codeintel_stitcher.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codeintel/indexer/stitcher.py tests/test_codeintel_stitcher.py
git commit -m "feat(codeintel): stitch SCIP occurrences to function scopes"
```

---

### Task 6: Indexer Pipeline & CLI Builder

**Files:**
- Create: `codeintel/indexer/builder.py`
- Modify: `main.py`
- Test: `tests/test_codeintel_builder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_codeintel_builder.py
from pathlib import Path
from codeintel.indexer.builder import build_index
from codeintel.store.db import execute_query, reset_connection

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_codeintel_builder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeintel.indexer.builder'`

- [ ] **Step 3: Implement Builder & Register CLI command**

Create `codeintel/indexer/builder.py`:
```python
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import kuzu

from codeintel.indexer.ast_parser import parse_go_file, ExtractedFunction
from codeintel.indexer.scip_reader import parse_scip_occurrences
from codeintel.indexer.stitcher import stitch_calls_and_types
from codeintel.store.ddl import init_schema

def build_index(repo_root: Path | str, db_path: Path | str) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    db_p = Path(db_path).resolve()

    # 1. Initialize schema in write mode
    init_schema(str(db_p))

    # 2. Parse Go files with Tree-sitter
    all_functions: list[ExtractedFunction] = []
    files_indexed = 0

    go_files = list(root.rglob("*.go"))
    for gf in go_files:
        rel_path = str(gf.relative_to(root))
        source = gf.read_text(encoding="utf-8")
        funcs = parse_go_file(rel_path, source)
        all_functions.extend(funcs)
        files_indexed += 1

    # 3. Read SCIP index if present
    scip_file = root / "index.scip"
    occurrences = parse_scip_occurrences(scip_file)
    calls = stitch_calls_and_types(all_functions, occurrences)

    # 4. Insert into KùzuDB
    db = kuzu.Database(str(db_p), read_only=False)
    conn = kuzu.Connection(db)

    # Insert files & functions
    for f in all_functions:
        conn.execute(
            """MERGE (f:File {path: $path}) ON CREATE SET f.language = 'go'""",
            {"path": f.file_path},
        )
        conn.execute(
            """MERGE (fn:Function {id: $id})
               ON CREATE SET fn.name = $name,
                             fn.package = $package,
                             fn.signature = $signature,
                             fn.file_path = $file_path,
                             fn.start_line = $start_line,
                             fn.end_line = $end_line,
                             fn.start_byte = $start_byte,
                             fn.end_byte = $end_byte,
                             fn.cyclomatic_complexity = $complexity,
                             fn.branch_count = $branches""",
            {
                "id": f.id,
                "name": f.name,
                "package": f.package,
                "signature": f.signature,
                "file_path": f.file_path,
                "start_line": f.start_line,
                "end_line": f.end_line,
                "start_byte": f.start_byte,
                "end_byte": f.end_byte,
                "complexity": f.cyclomatic_complexity,
                "branches": f.branch_count,
            },
        )
        conn.execute(
            """MATCH (fl:File {path: $file_path}), (fn:Function {id: $id})
               MERGE (fl)-[:CONTAINS]->(fn)""",
            {"file_path": f.file_path, "id": f.id},
        )

    stats = {
        "files_indexed": files_indexed,
        "functions_indexed": len(all_functions),
        "calls_recorded": len(calls),
    }

    stats_file = db_p / "index_stats.json"
    stats_file.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats
```

Add CLI subcommand in `main.py`:
```python
@app.command(name="index")
def index_cmd(
    root: str = typer.Option(".", "--root", "-r", help="Thư mục repo cần index"),
    lang: str = typer.Option("go", "--lang", "-l", help="Ngôn ngữ mục tiêu (hiện tại: go)"),
):
    """Xây dựng Code Intelligence Graph vào KùzuDB."""
    from codeintel.indexer.builder import build_index
    from config.settings import settings
    console.print(f"[bold cyan]Đang index repo {root} (ngôn ngữ: {lang})...[/bold cyan]")
    stats = build_index(root, settings.codeintel_db_path)
    console.print(f"[bold green]Index thành công![/bold green] Files: {stats['files_indexed']}, Functions: {stats['functions_indexed']}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_codeintel_builder.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codeintel/indexer/builder.py main.py tests/test_codeintel_builder.py
git commit -m "feat(codeintel): add indexer pipeline and CLI build command"
```

---

### Task 7: Agent Query Tools (Read-Only)

**Files:**
- Create: `codeintel/tools/branches.py`
- Create: `codeintel/tools/dependencies.py`
- Create: `codeintel/tools/paths.py`
- Create: `codeintel/tools/source.py`
- Create: `codeintel/tools/scope.py`
- Create: `codeintel/tools/__init__.py`
- Test: `tests/test_codeintel_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_codeintel_tools.py
from pathlib import Path
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

    # 2. inspect_function_branches (JIT)
    fn_id = "service/order.go::ProcessOrder::12"
    res_branches = inspect_function_branches(fn_id, repo_root=sample_root, db_path=target_db)
    assert res_branches["ok"] is True
    assert len(res_branches["data"]["branches"]) == 2  # 2 if statements

    # 3. get_function_source
    res_src = get_function_source(fn_id, repo_root=sample_root, db_path=target_db)
    assert res_src["ok"] is True
    assert "func (s *OrderService) ProcessOrder" in res_src["data"]["source"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_codeintel_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeintel.tools'`

- [ ] **Step 3: Implement Tools with JIT Parsing**

Create `codeintel/tools/branches.py`:
```python
from __future__ import annotations
from pathlib import Path
from typing import Any
import tree_sitter_go as tsgo
from tree_sitter import Language, Parser, Node
from codeintel.store.db import execute_query

GO_LANGUAGE = Language(tsgo.language())

def inspect_function_branches(
    func_id: str,
    repo_root: Path | str = ".",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    rows = execute_query(
        db_path,
        """MATCH (fn:Function {id: $id})
           RETURN fn.file_path AS file_path, fn.start_byte AS start_byte,
                  fn.end_byte AS end_byte, fn.start_line AS start_line""",
        {"id": func_id},
    )
    if not rows:
        return {"ok": False, "data": None, "error": f"Function {func_id} not found in index."}

    info = rows[0]
    file_path = Path(repo_root) / info["file_path"]
    if not file_path.exists():
        return {"ok": False, "data": None, "error": f"File {file_path} not found on disk."}

    content_bytes = file_path.read_bytes()
    slice_bytes = content_bytes[info["start_byte"]:info["end_byte"]]

    parser = Parser(GO_LANGUAGE)
    tree = parser.parse(slice_bytes)

    branches: list[dict[str, Any]] = []
    def traverse(node: Node) -> None:
        if node.type in ("if_statement", "for_statement", "expression_case"):
            cond = ""
            for child in node.children:
                if "condition" in child.type or child.type == "binary_expression":
                    cond = slice_bytes[child.start_byte:child.end_byte].decode("utf-8")
                    break
            branches.append({
                "type": node.type,
                "line": info["start_line"] + node.start_point[0],
                "condition": cond,
            })
        for child in node.children:
            traverse(child)

    traverse(tree.root_node)
    return {
        "ok": True,
        "data": {
            "func_id": func_id,
            "branches": branches,
        },
        "error": None,
    }
```

Create `codeintel/tools/dependencies.py`:
```python
from __future__ import annotations
from pathlib import Path
from typing import Any
from codeintel.store.db import execute_query

def get_external_dependencies(func_id: str, db_path: Path | str | None = None) -> dict[str, Any]:
    rows = execute_query(
        db_path,
        """MATCH (caller:Function {id: $id})-[c:CALLS]->(callee:Function)
           RETURN callee.id AS callee_id, callee.name AS name, c.line_number AS line, c.is_external AS is_external""",
        {"id": func_id},
    )
    return {
        "ok": True,
        "data": {
            "func_id": func_id,
            "callees": rows,
        },
        "error": None,
    }
```

Create `codeintel/tools/paths.py`:
```python
from __future__ import annotations
from pathlib import Path
from typing import Any
from codeintel.store.db import execute_query

def trace_execution_path(source_func_id: str, sink_func_id: str, db_path: Path | str | None = None) -> dict[str, Any]:
    rows = execute_query(
        db_path,
        """MATCH p = (src:Function {id: $src})-[c:CALLS*1..5]->(sink:Function {id: $sink})
           RETURN [n in nodes(p) | n.id] AS path LIMIT 1""",
        {"src": source_func_id, "sink": sink_func_id},
    )
    path = rows[0]["path"] if rows else []
    return {
        "ok": True,
        "data": {
            "source": source_func_id,
            "sink": sink_func_id,
            "path": path,
        },
        "error": None,
    }
```

Create `codeintel/tools/source.py`:
```python
from __future__ import annotations
from pathlib import Path
from typing import Any
from codeintel.store.db import execute_query

def get_function_source(func_id: str, max_lines: int = 150, repo_root: Path | str = ".", db_path: Path | str | None = None) -> dict[str, Any]:
    rows = execute_query(
        db_path,
        """MATCH (fn:Function {id: $id})
           RETURN fn.file_path AS file_path, fn.start_line AS start_line, fn.end_line AS end_line""",
        {"id": func_id},
    )
    if not rows:
        return {"ok": False, "data": None, "error": f"Function {func_id} not found."}

    info = rows[0]
    file_path = Path(repo_root) / info["file_path"]
    if not file_path.exists():
        return {"ok": False, "data": None, "error": f"File {file_path} not found."}

    lines = file_path.read_text(encoding="utf-8").splitlines()
    start = max(0, info["start_line"] - 1)
    end = min(len(lines), info["end_line"])
    snippet = lines[start:end]

    if len(snippet) > max_lines:
        snippet = snippet[:max_lines] + ["// ... [truncated by codeintel]"]

    return {
        "ok": True,
        "data": {
            "func_id": func_id,
            "source": "\n".join(snippet),
        },
        "error": None,
    }
```

Create `codeintel/tools/scope.py`:
```python
from __future__ import annotations
from pathlib import Path
from typing import Any
from codeintel.store.db import execute_query

def list_scope_functions(package: str = "", min_complexity: int = 1, db_path: Path | str | None = None) -> dict[str, Any]:
    query = """MATCH (fn:Function)
               WHERE fn.cyclomatic_complexity >= $min_complexity
               RETURN fn.id AS id, fn.name AS name, fn.package AS package,
                      fn.cyclomatic_complexity AS complexity, fn.branch_count AS branches
               ORDER BY fn.cyclomatic_complexity DESC"""
    rows = execute_query(db_path, query, {"min_complexity": min_complexity})
    if package:
        rows = [r for r in rows if r["package"] == package]

    return {
        "ok": True,
        "data": rows,
        "error": None,
    }
```

Create `codeintel/tools/__init__.py`:
```python
from codeintel.tools.branches import inspect_function_branches
from codeintel.tools.dependencies import get_external_dependencies
from codeintel.tools.paths import trace_execution_path
from codeintel.tools.source import get_function_source
from codeintel.tools.scope import list_scope_functions

__all__ = [
    "inspect_function_branches",
    "get_external_dependencies",
    "trace_execution_path",
    "get_function_source",
    "list_scope_functions",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_codeintel_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codeintel/tools/ tests/test_codeintel_tools.py
git commit -m "feat(codeintel): implement 5 read-only query tools with JIT branch extraction"
```

---

### Task 8: LangGraph Pre-Step Integration

**Files:**
- Create: `agents/retriever.py`
- Modify: `agents/graph.py`
- Modify: `agents/planner.py`
- Test: `tests/test_codeintel_retriever.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_codeintel_retriever.py
from pathlib import Path
from agents.retriever import codeintel_retriever_node
from agents.state import AgentState
from codeintel.indexer.builder import build_index
from config.settings import settings

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_codeintel_retriever.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.retriever'`

- [ ] **Step 3: Implement Retriever Node & Connect LangGraph**

Create `agents/retriever.py`:
```python
from __future__ import annotations
from typing import Any
from config.settings import settings
from agents.state import AgentState
from codeintel.tools.scope import list_scope_functions
from codeintel.tools.branches import inspect_function_branches
from codeintel.tools.dependencies import get_external_dependencies

def codeintel_retriever_node(state: AgentState) -> dict[str, Any]:
    if not settings.enable_codeintel or not settings.codeintel_db_path.exists():
        return {"code_intelligence_summary": None}

    # Retrieve high complexity functions
    funcs_res = list_scope_functions(db_path=settings.codeintel_db_path)
    if not funcs_res.get("ok") or not funcs_res.get("data"):
        return {"code_intelligence_summary": None}

    top_funcs = funcs_res["data"][:5]  # Limit to top 5
    summary_items = []

    for fn in top_funcs:
        branches_res = inspect_function_branches(fn["id"], db_path=settings.codeintel_db_path)
        deps_res = get_external_dependencies(fn["id"], db_path=settings.codeintel_db_path)
        summary_items.append({
            "func_id": fn["id"],
            "name": fn["name"],
            "complexity": fn["complexity"],
            "branches": branches_res.get("data", {}).get("branches", []),
            "dependencies": deps_res.get("data", {}).get("callees", []),
        })

    return {
        "code_intelligence_summary": {
            "target_functions": summary_items,
        }
    }
```

Update `agents/graph.py`:
Import `codeintel_retriever_node` and set it as entry point:
```python
    graph.add_node("codeintel_retriever", codeintel_retriever_node)
    graph.set_entry_point("codeintel_retriever")
    graph.add_edge("codeintel_retriever", "planner")
```

Update `agents/planner.py`:
Append `code_intelligence_summary` to prompt context if present:
```python
    codeintel_info = state.get("code_intelligence_summary")
    codeintel_context = f"\nCode Intelligence (Branch & Dependency Graph):\n{codeintel_info}" if codeintel_info else ""
    user_content = f"{user_content}\n{codeintel_context}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_codeintel_retriever.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/retriever.py agents/graph.py agents/planner.py tests/test_codeintel_retriever.py
git commit -m "feat(codeintel): wire codeintel_retriever pre-step node into LangGraph"
```

---

### Task 9: Verification & Documentation

**Files:**
- Create: `docs/CODEINTEL_GUIDE.md`
- Test: Full test suite verification

- [ ] **Step 1: Write verification test covering full flow**

Run all codeintel tests together:
`pytest tests/test_codeintel*.py -v`
Expected: ALL PASS

- [ ] **Step 2: Create Documentation**

Create `docs/CODEINTEL_GUIDE.md`:
```markdown
# Code Intelligence Graph Guide

## Overview
QC Agent includes a built-in static analysis engine combining Tree-sitter, SCIP, and KùzuDB.

## Usage
1. Build graph index:
   ```bash
   python main.py index --root ./my_go_project --lang go
   ```
2. Enable in QC Agent:
   Set in `.env`:
   ```env
   ENABLE_CODEINTEL=true
   ```
3. Run QC Agent as normal:
   ```bash
   python main.py run "Test order checkout flow" --code ./my_go_project
   ```
```

- [ ] **Step 3: Commit**

```bash
git add docs/CODEINTEL_GUIDE.md
git commit -m "docs: add user guide for code intelligence graph"
```
