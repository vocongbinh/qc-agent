from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
import kuzu

from codeintel.indexer.ast_parser import ExtractedFunction, parse_go_file
from codeintel.indexer.ast_parser_ts import parse_ts_file
from codeintel.indexer.scip_reader import parse_scip_occurrences
from codeintel.indexer.stitcher import stitch_calls_and_types
from codeintel.store.ddl import init_schema
from codeintel.store.db import reset_connection

logger = logging.getLogger(__name__)


def build_index(
    repo_root: Path | str,
    db_path: Path | str,
    lang: str = "go",
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Thư mục repo không tồn tại: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Đường dẫn không phải là thư mục: {root}")

    db_p = Path(db_path).resolve()

    # 1. Initialize schema in write mode
    init_schema(str(db_p))

    all_functions: list[ExtractedFunction] = []
    files_indexed = 0
    endpoints_indexed = 0
    lang_lower = lang.lower()

    ignored_parts = {".git", ".venv", "venv", "vendor", "node_modules", "dist", "build"}

    if lang_lower in ("ts", "typescript", "js"):
        code_files = [
            f for f in sorted(list(root.rglob("*")))
            if f.suffix in (".ts", ".tsx", ".js")
            and not f.name.endswith((".spec.ts", ".test.ts", ".d.ts", ".spec.js", ".test.js"))
            and not any(part in f.parts for part in ignored_parts)
        ]
        parse_fn = parse_ts_file
        file_lang = "typescript"
    else:
        code_files = [
            f for f in sorted(list(root.rglob("*.go")))
            if not any(part in f.parts for part in ignored_parts)
        ]
        parse_fn = parse_go_file
        file_lang = "go"

    for cf in code_files:
        rel_path = cf.relative_to(root).as_posix()
        try:
            source = cf.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Failed to read %s: %s", cf, exc)
            continue
        funcs = parse_fn(rel_path, source)
        all_functions.extend(funcs)
        files_indexed += 1
    # 3. Read SCIP index if present
    module_prefix = ""
    go_mod_file = root / "go.mod"
    if go_mod_file.exists():
        for line in go_mod_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("module "):
                module_prefix = line.split(maxsplit=1)[1].strip()
                break

    scip_file = root / "index.scip"
    occurrences = parse_scip_occurrences(scip_file)
    calls = stitch_calls_and_types(all_functions, occurrences, internal_module_prefix=module_prefix)
    # 4. Insert into KùzuDB using writer connection
    db = kuzu.Database(str(db_p), read_only=False)
    conn = kuzu.Connection(db)

    try:
        # Insert all discovered files
        for cf in code_files:
            rel_path = cf.relative_to(root).as_posix()
            conn.execute(
                """MERGE (f:File {path: $path}) ON CREATE SET f.language = $lang""",
                {"path": rel_path, "lang": file_lang},
            )

        # Insert functions
        for f in all_functions:
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
                                 fn.branch_count = $branches
                   ON MATCH SET fn.name = $name,
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
        # Insert CALLS relationships if both caller and callee exist or store callee
        for c in calls:
            # Check if callee is known in index
            conn.execute(
                """MATCH (caller:Function {id: $caller_id}), (callee:Function)
                   WHERE $callee_symbol CONTAINS callee.name
                   MERGE (caller)-[rel:CALLS {line_number: $line, is_external: $is_ext}]->(callee)""",
                {
                    "caller_id": c["caller_id"],
                    "callee_symbol": c["callee_symbol"],
                    "line": c["line_number"],
                    "is_ext": c["is_external"],
                },
            )

        # 5. Ingest OpenAPI endpoints if available
        openapi_candidates = [root / "openapi.json", root / "openapi.yaml", root / "swagger.json"]
        for oac in openapi_candidates:
            if oac.exists():
                try:
                    raw_text = oac.read_text(encoding="utf-8", errors="replace")
                    spec_data = json.loads(raw_text)
                    paths = spec_data.get("paths", {})
                    for path_tpl, methods in paths.items():
                        if not isinstance(methods, dict):
                            continue
                        for method_name, op_details in methods.items():
                            if method_name.lower() in ("get", "post", "put", "delete", "patch", "options", "head"):
                                op_id = op_details.get("operationId", "") if isinstance(op_details, dict) else ""
                                ep_id = f"{method_name.upper()}:{path_tpl}"
                                conn.execute(
                                    """MERGE (ep:Endpoint {id: $id})
                                       ON CREATE SET ep.method = $method,
                                                     ep.path_template = $path_template,
                                                     ep.handler_func_id = $op_id""",
                                    {
                                        "id": ep_id,
                                        "method": method_name.upper(),
                                        "path_template": path_tpl,
                                        "op_id": op_id or None,
                                    },
                                )
                                endpoints_indexed += 1
                                if op_id:
                                    conn.execute(
                                        """MATCH (ep:Endpoint {id: $ep_id}), (fn:Function)
                                           WHERE fn.name = $op_id
                                           MERGE (ep)-[:HANDLES]->(fn)""",
                                        {"ep_id": ep_id, "op_id": op_id},
                                    )
                    logger.info("Indexed %d endpoints from %s", endpoints_indexed, oac.name)
                    break
                except Exception as exc:
                    logger.warning("Failed to parse OpenAPI spec %s: %s", oac, exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass
        del conn
        del db
        reset_connection()
    stats = {
        "files_indexed": files_indexed,
        "functions_indexed": len(all_functions),
        "calls_recorded": len(calls),
        "endpoints_indexed": endpoints_indexed,
    }

    if db_p.is_dir():
        stats_file = db_p / "index_stats.json"
    else:
        db_p.parent.mkdir(parents=True, exist_ok=True)
        stats_file = db_p.parent / "index_stats.json"
    stats_file.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats
