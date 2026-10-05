# Code Intelligence Graph (Tree-sitter + SCIP + KùzuDB) for QC Agent

## 1. Overview & Objective

### 1.1 Goal
Provide `qc-agent` with accurate, scope-first code intelligence without reading entire repositories into LLM contexts. By utilizing a hybrid static analysis pipeline (Tree-sitter + SCIP) and an embedded property graph database (KùzuDB), the agent queries exact functions, call paths, external dependencies, and execution branches to generate high-coverage test cases and perform root-cause bug localization.

### 1.2 Non-Goals (Phase 1)
- Multi-language support out of the box (Phase 1 is Go-first).
- Heavy graphical UI for graph visualization or Neo4j server hosting.
- Arbitrary Cypher execution by LLMs.
- AST-level framework route extractors (OpenAPI parsing is used for API endpoints).

---

## 2. Architecture & Ingestion Pipeline

### 2.1 Ingestion Flow (Single-Writer CLI)
The indexing phase runs as a standalone CLI command (`qc-index build --root <path> --lang go`).

```text
Target Go Repository
   │
   ├──> [1. scip-go index] ───> index.scip (Protobuf: Definitions, References, Occurrences)
   │
   └──> [2. Tree-sitter Go] ──> AST parsing per file:
                                  - Function boundary: start_line, end_line, start_byte, end_byte
                                  - Metrics: cyclomatic_complexity, branch_count
   │
   ▼
[3. Stitcher & Graph Ingestion] (Python)
   - Nodes: File, Function, StructOrClass, Endpoint (if OpenAPI exists)
   - Project SCIP References onto Function byte ranges to create CALLS edges
   - Bulk insert into KùzuDB (.codeintel_db)
   - Export summary: index_stats.json
```

### 2.2 Schema Definition (KùzuDB)
Optimized property graph model with Branch details omitted from node storage to avoid combinatorial explosion:

#### Nodes
- **`File`**:
  - `path`: STRING (Primary Key)
  - `language`: STRING
- **`Function`**:
  - `id`: STRING (Primary Key, format: `<rel_path>::<name>::<start_line>`)
  - `name`: STRING
  - `package`: STRING
  - `signature`: STRING
  - `file_path`: STRING
  - `start_line`: INT64
  - `end_line`: INT64
  - `start_byte`: INT64
  - `end_byte`: INT64
  - `cyclomatic_complexity`: INT64
  - `branch_count`: INT64
- **`StructOrClass`**:
  - `id`: STRING (Primary Key, format: `<rel_path>::<name>`)
  - `name`: STRING
  - `file_path`: STRING
  - `kind`: STRING (`struct`, `interface`, `type`)
- **`Endpoint`** (Optional, derived from OpenAPI):
  - `id`: STRING (Primary Key, format: `<METHOD>:<path_template>`)
  - `method`: STRING
  - `path_template`: STRING
  - `handler_func_id`: STRING (Nullable)

#### Edges
- **`CONTAINS`**: `File` -> `Function` | `StructOrClass`
- **`CALLS`**: `Function` -> `Function` (`line_number`: INT64, `is_external`: BOOL)
- **`USES_TYPE`**: `Function` -> `StructOrClass`
- **`HANDLES`**: `Endpoint` -> `Function`

---

## 3. Querying & Agent Tools (Read-Only)

### 3.1 Concurrency & File Lock Architecture
- **Writer:** Single-writer batch CLI process (`read_only=False`), cleanly terminates and releases system file locks.
- **Readers:** LangGraph worker threads use a thread-safe singleton connection opened with `read_only=True`, completely avoiding locks and UI blocking.

### 3.2 Standard Agent Tools
All tools return a consistent JSON response envelope: `{"ok": bool, "data": Any, "error": str | None}`.

1. **`inspect_function_branches(func_id: str) -> dict`**:
   - Fetches `file_path`, `start_byte`, `end_byte`, `start_line` from KùzuDB.
   - Reads the byte slice directly from disk: `file.seek(start_byte)` and `read(end_byte - start_byte)`.
   - Executes in-memory Tree-sitter query to extract branch details with offset correction:
     $$\text{line\_in\_file} = \text{start\_line} + \text{line\_in\_slice} - 1$$
   - Returns branch types, line numbers, and conditional expressions.
2. **`get_external_dependencies(func_id: str) -> dict`**:
   - Queries direct `CALLS` and `USES_TYPE` relations.
   - Categorizes callee functions into internal package vs. external standard/third-party packages for mock/stub guidance.
3. **`trace_execution_path(source_func_id: str, sink_func_id: str) -> dict`**:
   - Executes Cypher shortest path between entry point and target/failing function.
   - Returns sequential list of functions and files for bug localization and cascade failure analysis.
4. **`get_function_source(func_id: str, max_lines: int = 150) -> dict`**:
   - Confines paths within the project root to prevent path traversal attacks.
   - Reads exact line span and safely truncates if length exceeds token budget.
5. **`list_scope_functions(package: str = "", min_complexity: int = 1) -> dict`**:
   - Filters functions by package path and complexity threshold, returning prioritized targets for testing.

---

## 4. LangGraph Integration

### 4.1 Pipeline Enhancement
A pre-step node `codeintel_retriever` is inserted before `planner_node`:

```text
[User Request] + [code_paths]
       │
       ▼
[codeintel_retriever] (Feature flag: settings.enable_codeintel)
   ├── Check if codeintel DB exists. If not, pass-through cleanly.
   ├── Query list_scope_functions based on target paths.
   ├── Retrieve branches and dependencies for top-N complex functions (token budget <= 4k).
   ├── Populate state["code_intelligence_summary"].
   └── Emit event: emitter.emit("node_start", {"step": "codeintel_retrieved"}).
       │
       ▼
[planner_node]
   └── Ingests code_intelligence_summary to construct high-branch-coverage TestPlan.
       │
       ▼
[human_review] ──> [generator] ──> [executors]
```

### 4.2 State Schema Additions
In `agents/state.py`:
- `code_intelligence_summary: NotRequired[dict[str, Any] | None]`

In `config/settings.py`:
- `enable_codeintel: bool = False`
- `codeintel_db_path: Path = project_root / ".codeintel_db"`

---

## 5. Error Handling & Edge Cases

1. **SCIP Generation Failures:**
   - If `scip-go` fails due to uncompilable code or missing `go.mod`, CLI logs diagnostic compiler errors and falls back to Tree-sitter heuristic symbol extraction.
2. **Missing Database File:**
   - If an agent tool is called without an existing index, it returns `{"ok": False, "data": None, "error": "Index not found. Run qc-index build first."}` rather than raising uncaught exceptions.
3. **Out-of-Sync Source Files:**
   - If source code is modified after indexing, JIT slice reading catches `IndexError` / `IOError` and returns a fallback message prompting re-indexing.

---

## 6. Testing Strategy

1. **Test Fixtures:**
   - Create `testdata/sample_go_pkg/` containing structs, interfaces, nested control flows, and cross-package calls.
2. **Unit Tests:**
   - `test_indexer.py`: Validate AST slicing, complexity calculation, and SCIP stitching.
   - `test_store.py`: Verify KùzuDB DDL, idempotent rebuilds, and `read_only=True` concurrent queries.
   - `test_tools.py`: Test all 5 tools against a pre-built fixture database without requiring LLMs.
3. **Integration Tests:**
   - Test LangGraph workflow with `enable_codeintel=True` and `enable_codeintel=False` to ensure backwards compatibility and zero regression on existing suites.
