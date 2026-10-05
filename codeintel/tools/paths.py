from __future__ import annotations

from pathlib import Path
from typing import Any
from codeintel.store.db import execute_query
from config.settings import settings


def trace_execution_path(
    source_func_id: str,
    sink_func_id: str,
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
        check_src = execute_query(
            db_path,
            "MATCH (fn:Function {id: $id}) RETURN fn.id AS id",
            {"id": source_func_id},
        )
        if not check_src:
            return {
                "ok": False,
                "data": None,
                "error": f"Function {source_func_id} not found.",
            }

        check_sink = execute_query(
            db_path,
            "MATCH (fn:Function {id: $id}) RETURN fn.id AS id",
            {"id": sink_func_id},
        )
        if not check_sink:
            return {
                "ok": False,
                "data": None,
                "error": f"Function {sink_func_id} not found.",
            }

        query = """MATCH p = (src:Function {id: $src})-[c:CALLS* SHORTEST 1..10]->(sink:Function {id: $sink})
                   RETURN properties(nodes(p), 'id') AS path LIMIT 1"""
        rows = execute_query(db_path, query, {"src": source_func_id, "sink": sink_func_id})

        path: list[str] = []
        if rows and rows[0].get("path"):
            raw_path = rows[0]["path"]
            if isinstance(raw_path, list):
                path = [
                    item["id"] if isinstance(item, dict) and "id" in item else str(item)
                    for item in raw_path
                ]

        return {
            "ok": True,
            "data": {
                "source": source_func_id,
                "sink": sink_func_id,
                "path": path,
            },
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "data": None,
            "error": f"Database error: {exc}",
        }
