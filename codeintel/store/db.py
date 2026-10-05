from __future__ import annotations
import threading
from pathlib import Path
from typing import Any
import kuzu
from config.settings import settings

_db_instance: kuzu.Database | None = None
_current_db_path: str | None = None
_db_lock = threading.Lock()

def reset_connection() -> None:
    global _db_instance, _current_db_path
    with _db_lock:
        _db_instance = None
        _current_db_path = None

def get_codeintel_db(db_path: str | Path | None = None) -> kuzu.Database:
    global _db_instance, _current_db_path
    target_path = str(db_path or settings.codeintel_db_path)
    with _db_lock:
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
