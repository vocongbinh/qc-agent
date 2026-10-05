from __future__ import annotations

from pathlib import Path
from typing import Any
import tree_sitter_go as tsgo
from tree_sitter import Language, Parser, Node
from codeintel.store.db import execute_query
from config.settings import settings

GO_LANGUAGE = Language(tsgo.language())


def inspect_function_branches(
    func_id: str,
    repo_root: Path | str = ".",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    db_target = Path(db_path or settings.codeintel_db_path)
    if str(db_target) != ":memory:" and not db_target.exists():
        return {
            "ok": False,
            "data": None,
            "error": f"Database does not exist at {db_target}",
        }

    try:
        rows = execute_query(
            db_path,
            """MATCH (fn:Function {id: $id})
               RETURN fn.file_path AS file_path, fn.start_byte AS start_byte,
                      fn.end_byte AS end_byte, fn.start_line AS start_line""",
            {"id": func_id},
        )
        if not rows:
            return {
                "ok": False,
                "data": None,
                "error": f"Function {func_id} not found in index.",
            }

        info = rows[0]
        root_path = Path(repo_root).resolve()
        target_file = (root_path / info["file_path"]).resolve()

        if not target_file.is_relative_to(root_path):
            return {
                "ok": False,
                "data": None,
                "error": f"Access denied: path traversal detected for {info['file_path']}",
            }

        if not target_file.is_file():
            return {
                "ok": False,
                "data": None,
                "error": f"File {target_file} not found on disk.",
            }

        start_byte = int(info["start_byte"])
        end_byte = int(info["end_byte"])
        size = max(0, end_byte - start_byte)

        with open(target_file, "rb") as f:
            f.seek(start_byte)
            slice_bytes = f.read(size)

        parser = Parser(GO_LANGUAGE)
        tree = parser.parse(slice_bytes)

        branches: list[dict[str, Any]] = []

        def traverse(node: Node) -> None:
            if node.type in ("if_statement", "for_statement", "expression_case"):
                cond = ""
                cond_node = node.child_by_field_name("condition")
                if cond_node:
                    cond = slice_bytes[cond_node.start_byte:cond_node.end_byte].decode("utf-8", errors="replace")
                else:
                    for child in node.children:
                        if "condition" in child.type or child.type in ("binary_expression", "for_clause", "expression_list"):
                            cond = slice_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                            break

                line_num = int(info["start_line"]) + node.start_point[0]
                branches.append({
                    "type": node.type,
                    "line": line_num,
                    "line_number": line_num,
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
    except Exception as exc:
        return {
            "ok": False,
            "data": None,
            "error": f"Database error: {exc}",
        }
