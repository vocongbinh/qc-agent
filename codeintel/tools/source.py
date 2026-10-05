from __future__ import annotations

from pathlib import Path
from typing import Any
from codeintel.store.db import execute_query
from config.settings import settings


def get_function_source(
    func_id: str,
    max_lines: int = 150,
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
               RETURN fn.file_path AS file_path, fn.start_line AS start_line, fn.end_line AS end_line""",
            {"id": func_id},
        )
        if not rows:
            return {
                "ok": False,
                "data": None,
                "error": f"Function {func_id} not found.",
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
                "error": f"File {target_file} not found.",
            }

        lines = target_file.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, int(info["start_line"]) - 1)
        end = min(len(lines), int(info["end_line"]))
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
    except Exception as exc:
        return {
            "ok": False,
            "data": None,
            "error": f"Database error: {exc}",
        }
