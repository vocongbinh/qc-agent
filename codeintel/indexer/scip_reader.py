from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
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

    try:
        from codeintel.indexer import scip_pb2  # type: ignore
    except ImportError:
        logger.warning("scip_pb2 is not available; SCIP index decoding skipped")
        return []

    try:
        raw_bytes = path.read_bytes()
        if not raw_bytes:
            return []
        index = scip_pb2.Index()
        index.ParseFromString(raw_bytes)
    except Exception as exc:  # DecodeError / protobuf decode issues / OSError
        logger.warning("Failed to decode SCIP index from %s: %s", path, exc)
        return []

    results: list[ScipOccurrence] = []
    for doc in index.documents:
        for occ in doc.occurrences:
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
