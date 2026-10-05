import sys
from types import ModuleType
from unittest.mock import MagicMock
from pathlib import Path
from codeintel.indexer.scip_reader import parse_scip_occurrences, ScipOccurrence


def test_parse_scip_occurrences_handles_missing_file(tmp_path):
    missing_file = tmp_path / "nonexistent.scip"
    results = parse_scip_occurrences(missing_file)
    assert results == []


def test_parse_scip_occurrences_handles_empty_zero_byte_file(tmp_path):
    empty_file = tmp_path / "empty.scip"
    empty_file.write_bytes(b"")
    results = parse_scip_occurrences(empty_file)
    assert results == []


def test_scip_occurrence_data_structure():
    occ_def = ScipOccurrence(
        file_path="service/order.go",
        start_line=13,
        start_col=6,
        symbol="example.com/sample_go/service/OrderService#ProcessOrder().",
        symbol_roles=1,  # Definition
    )
    assert occ_def.is_definition is True

    occ_non_def = ScipOccurrence(
        file_path="service/order.go",
        start_line=20,
        start_col=10,
        symbol="example.com/sample_go/service/OrderService#ProcessOrder().",
        symbol_roles=0,  # Reference / not definition
    )
    assert occ_non_def.is_definition is False


def test_parse_scip_occurrences_decoding_with_mock_pb2(tmp_path, monkeypatch):
    # Create mock occurrence
    mock_occ1 = MagicMock()
    mock_occ1.range = [15, 4, 15, 20]
    mock_occ1.symbol = "example.com/pkg#Func()."
    mock_occ1.symbol_roles = 1

    mock_occ2 = MagicMock()
    mock_occ2.range = []  # Empty range test fallback to (0, 0)
    mock_occ2.symbol = "example.com/pkg#Var."
    mock_occ2.symbol_roles = 0

    mock_doc = MagicMock()
    mock_doc.relative_path = "pkg/lib.go"
    mock_doc.occurrences = [mock_occ1, mock_occ2]

    mock_index = MagicMock()
    mock_index.documents = [mock_doc]

    mock_pb2 = ModuleType("codeintel.indexer.scip_pb2")
    mock_pb2.Index = MagicMock(return_value=mock_index)

    monkeypatch.setitem(sys.modules, "codeintel.indexer.scip_pb2", mock_pb2)

    scip_file = tmp_path / "test.scip"
    scip_file.write_bytes(b"dummy protobuf payload")

    results = parse_scip_occurrences(scip_file)

    assert len(results) == 2
    assert results[0] == ScipOccurrence(
        file_path="pkg/lib.go",
        start_line=15,
        start_col=4,
        symbol="example.com/pkg#Func().",
        symbol_roles=1,
    )
    assert results[0].is_definition is True

    assert results[1] == ScipOccurrence(
        file_path="pkg/lib.go",
        start_line=0,
        start_col=0,
        symbol="example.com/pkg#Var.",
        symbol_roles=0,
    )
    assert results[1].is_definition is False
    mock_index.ParseFromString.assert_called_once_with(b"dummy protobuf payload")


def test_parse_scip_occurrences_decode_failure_handled(tmp_path, monkeypatch):
    mock_index = MagicMock()
    mock_index.ParseFromString.side_effect = Exception("corrupt protobuf data")

    mock_pb2 = ModuleType("codeintel.indexer.scip_pb2")
    mock_pb2.Index = MagicMock(return_value=mock_index)
    monkeypatch.setitem(sys.modules, "codeintel.indexer.scip_pb2", mock_pb2)

    scip_file = tmp_path / "corrupt.scip"
    scip_file.write_bytes(b"corrupt data")

    results = parse_scip_occurrences(scip_file)
    assert results == []


def test_parse_scip_occurrences_missing_scip_pb2(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "codeintel.indexer.scip_pb2", None)

    scip_file = tmp_path / "test.scip"
    scip_file.write_bytes(b"data")

    results = parse_scip_occurrences(scip_file)
    assert results == []
