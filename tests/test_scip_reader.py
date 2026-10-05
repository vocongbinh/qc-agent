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
