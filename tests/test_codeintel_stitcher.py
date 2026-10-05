from __future__ import annotations

import pytest
from codeintel.indexer.ast_parser import ExtractedFunction
from codeintel.indexer.scip_reader import ROLE_DEFINITION, ScipOccurrence
from codeintel.indexer.stitcher import stitch_calls_and_types


def test_stitch_calls_correlates_occurrences_to_caller():
    caller = ExtractedFunction(
        id="main.go::main::5",
        name="main",
        package="main",
        signature="func main()",
        file_path="main.go",
        start_line=5,
        end_line=12,
        start_byte=50,
        end_byte=150,
        cyclomatic_complexity=1,
        branch_count=0,
    )
    occ = ScipOccurrence(
        file_path="main.go",
        start_line=7,  # inside main
        start_col=4,
        symbol="example.com/sample_go/service/OrderService#ProcessOrder().",
        symbol_roles=0,  # Reference
    )
    calls = stitch_calls_and_types([caller], [occ])
    assert len(calls) == 1
    assert calls[0]["caller_id"] == caller.id
    assert "ProcessOrder" in calls[0]["callee_symbol"]
    assert calls[0]["line_number"] == 7


def test_definitions_are_ignored():
    caller = ExtractedFunction(
        id="main.go::main::5",
        name="main",
        package="main",
        signature="func main()",
        file_path="main.go",
        start_line=5,
        end_line=12,
        start_byte=50,
        end_byte=150,
        cyclomatic_complexity=1,
        branch_count=0,
    )
    # Definition occurrence should not generate calls
    def_occ = ScipOccurrence(
        file_path="main.go",
        start_line=5,
        start_col=5,
        symbol="example.com/sample_go/main#main().",
        symbol_roles=ROLE_DEFINITION,
    )
    calls = stitch_calls_and_types([caller], [def_occ])
    assert len(calls) == 0


def test_occurrences_outside_function_scope_are_ignored():
    caller = ExtractedFunction(
        id="main.go::main::10",
        name="main",
        package="main",
        signature="func main()",
        file_path="main.go",
        start_line=10,
        end_line=20,
        start_byte=100,
        end_byte=200,
        cyclomatic_complexity=1,
        branch_count=0,
    )
    # Occurrence before function start line
    occ_before = ScipOccurrence(
        file_path="main.go",
        start_line=3,
        start_col=0,
        symbol="example.com/sample_go/config#Config.",
        symbol_roles=0,
    )
    # Occurrence after function end line
    occ_after = ScipOccurrence(
        file_path="main.go",
        start_line=25,
        start_col=0,
        symbol="example.com/sample_go/config#Other.",
        symbol_roles=0,
    )
    calls = stitch_calls_and_types([caller], [occ_before, occ_after])
    assert len(calls) == 0


def test_multiple_callers_and_occurrences_across_multiple_files():
    fn1_file1 = ExtractedFunction(
        id="pkg/a.go::FuncA1::10",
        name="FuncA1",
        package="pkg",
        signature="func FuncA1()",
        file_path="pkg/a.go",
        start_line=10,
        end_line=20,
        start_byte=100,
        end_byte=200,
        cyclomatic_complexity=1,
        branch_count=0,
    )
    fn2_file1 = ExtractedFunction(
        id="pkg/a.go::FuncA2::25",
        name="FuncA2",
        package="pkg",
        signature="func FuncA2()",
        file_path="pkg/a.go",
        start_line=25,
        end_line=35,
        start_byte=250,
        end_byte=350,
        cyclomatic_complexity=1,
        branch_count=0,
    )
    fn1_file2 = ExtractedFunction(
        id="pkg/b.go::FuncB1::5",
        name="FuncB1",
        package="pkg",
        signature="func FuncB1()",
        file_path="pkg/b.go",
        start_line=5,
        end_line=15,
        start_byte=50,
        end_byte=150,
        cyclomatic_complexity=1,
        branch_count=0,
    )

    occ_a1 = ScipOccurrence(
        file_path="pkg/a.go",
        start_line=15,
        start_col=2,
        symbol="example.com/pkg#Helper1().",
        symbol_roles=0,
    )
    occ_a2 = ScipOccurrence(
        file_path="pkg/a.go",
        start_line=30,
        start_col=2,
        symbol="example.com/pkg#Helper2().",
        symbol_roles=0,
    )
    occ_b1 = ScipOccurrence(
        file_path="pkg/b.go",
        start_line=10,
        start_col=2,
        symbol="example.com/pkg#Helper3().",
        symbol_roles=0,
    )
    occ_unmatched_file = ScipOccurrence(
        file_path="pkg/c.go",
        start_line=10,
        start_col=2,
        symbol="example.com/pkg#Helper4().",
        symbol_roles=0,
    )

    functions = [fn1_file1, fn2_file1, fn1_file2]
    occurrences = [occ_a1, occ_a2, occ_b1, occ_unmatched_file]

    calls = stitch_calls_and_types(functions, occurrences)
    assert len(calls) == 3
    assert calls[0]["caller_id"] == "pkg/a.go::FuncA1::10"
    assert calls[0]["callee_symbol"] == "example.com/pkg#Helper1()."
    assert calls[0]["line_number"] == 15

    assert calls[1]["caller_id"] == "pkg/a.go::FuncA2::25"
    assert calls[1]["callee_symbol"] == "example.com/pkg#Helper2()."
    assert calls[1]["line_number"] == 30

    assert calls[2]["caller_id"] == "pkg/b.go::FuncB1::5"
    assert calls[2]["callee_symbol"] == "example.com/pkg#Helper3()."
    assert calls[2]["line_number"] == 10


def test_distinguishing_internal_vs_external_calls():
    caller = ExtractedFunction(
        id="main.go::main::5",
        name="main",
        package="main",
        signature="func main()",
        file_path="main.go",
        start_line=5,
        end_line=20,
        start_byte=50,
        end_byte=200,
        cyclomatic_complexity=1,
        branch_count=0,
    )

    internal_occ = ScipOccurrence(
        file_path="main.go",
        start_line=8,
        start_col=4,
        symbol="example.com/myrepo/service/Order#Create().",
        symbol_roles=0,
    )
    external_occ = ScipOccurrence(
        file_path="main.go",
        start_line=12,
        start_col=4,
        symbol="github.com/gin-gonic/gin#Default().",
        symbol_roles=0,
    )

    # With internal_module_prefix specified
    calls_with_prefix = stitch_calls_and_types(
        [caller],
        [internal_occ, external_occ],
        internal_module_prefix="example.com/myrepo",
    )
    assert len(calls_with_prefix) == 2
    assert calls_with_prefix[0]["is_external"] is False
    assert calls_with_prefix[1]["is_external"] is True

    # Without internal_module_prefix (defaults: builtin is external, others internal)
    builtin_occ = ScipOccurrence(
        file_path="main.go",
        start_line=15,
        start_col=4,
        symbol="builtin#println().",
        symbol_roles=0,
    )
    calls_no_prefix = stitch_calls_and_types(
        [caller],
        [internal_occ, builtin_occ],
        internal_module_prefix="",
    )
    assert len(calls_no_prefix) == 2
    assert calls_no_prefix[0]["is_external"] is False
    assert calls_no_prefix[1]["is_external"] is True
