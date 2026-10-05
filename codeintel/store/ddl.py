from __future__ import annotations
from pathlib import Path
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

def init_schema(db_path: str | Path) -> None:
    db = kuzu.Database(str(db_path), read_only=False)
    conn = kuzu.Connection(db)
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)
