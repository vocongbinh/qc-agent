from pathlib import Path
import pytest
from codeintel.indexer.ast_parser import parse_go_file, ExtractedFunction


def test_parse_go_file_extracts_functions():
    code = """package service

func Add(a, b int) int {
	if a > 0 {
		return a + b
	}
	return b
}
"""
    funcs = parse_go_file("service/math.go", code)
    assert len(funcs) == 1
    fn = funcs[0]
    assert fn.name == "Add"
    assert fn.package == "service"
    assert fn.start_line == 3
    assert fn.end_line == 8
    assert fn.cyclomatic_complexity == 2  # 1 base + 1 if
    assert fn.branch_count == 1
    assert fn.signature == "func Add(a, b int) int"
    assert fn.file_path == "service/math.go"
    assert fn.id == "service/math.go::Add::3"


def test_parse_go_file_method_declaration_with_receiver():
    code = """package service

type OrderService struct{}

func (s *OrderService) ProcessOrder(orderID string) error {
	if orderID == "" {
		return errors.New("invalid order id")
	}
	return nil
}
"""
    funcs = parse_go_file("service/order.go", code)
    assert len(funcs) == 1
    fn = funcs[0]
    assert fn.name == "ProcessOrder"
    assert fn.package == "service"
    assert fn.signature == "func (s *OrderService) ProcessOrder(orderID string) error"
    assert fn.start_line == 5
    assert fn.end_line == 10
    assert fn.cyclomatic_complexity == 2
    assert fn.branch_count == 1
    assert fn.id == "service/order.go::ProcessOrder::5"
    assert fn.file_path == "service/order.go"


def test_parse_go_file_loops_switches_boolean_operators():
    code = """package logic

func CheckLogic(x int, vals []int) int {
	count := 0
	for _, v := range vals {
		if v > 0 && v < 100 || v == -1 {
			count++
		}
	}
	switch x {
	case 1:
		count += 10
	case 2:
		count += 20
	default:
		count += 30
	}
	return count
}
"""
    funcs = parse_go_file("logic/calc.go", code)
    assert len(funcs) == 1
    fn = funcs[0]
    assert fn.name == "CheckLogic"
    # branches:
    # 1 (for)
    # 1 (if) + 1 (&&) + 1 (||)
    # 1 (switch) + 2 (case 1, case 2)
    # total branches = 7, cyclomatic complexity = 1 + 7 = 8
    assert fn.branch_count == 7
    assert fn.cyclomatic_complexity == 8


def test_parse_go_file_multiple_functions():
    code = """package math

func Add(a, b int) int {
	return a + b
}

func Subtract(a, b int) int {
	return a - b
}

type Calculator struct{}

func (c *Calculator) Multiply(a, b int) int {
	return a * b
}
"""
    funcs = parse_go_file("pkg/math.go", code)
    assert len(funcs) == 3

    assert funcs[0].name == "Add"
    assert funcs[0].signature == "func Add(a, b int) int"
    assert funcs[0].start_line == 3
    assert funcs[0].id == "pkg/math.go::Add::3"
    assert funcs[0].cyclomatic_complexity == 1
    assert funcs[0].branch_count == 0

    assert funcs[1].name == "Subtract"
    assert funcs[1].signature == "func Subtract(a, b int) int"
    assert funcs[1].start_line == 7
    assert funcs[1].id == "pkg/math.go::Subtract::7"
    assert funcs[1].cyclomatic_complexity == 1
    assert funcs[1].branch_count == 0

    assert funcs[2].name == "Multiply"
    assert funcs[2].signature == "func (c *Calculator) Multiply(a, b int) int"
    assert funcs[2].start_line == 13
    assert funcs[2].id == "pkg/math.go::Multiply::13"
    assert funcs[2].cyclomatic_complexity == 1
    assert funcs[2].branch_count == 0

    for fn in funcs:
        assert fn.package == "math"
        assert fn.file_path == "pkg/math.go"


def test_parse_go_file_empty_or_package_only():
    assert parse_go_file("empty.go", "") == []
    assert parse_go_file("pkg.go", "package main\n") == []
    assert parse_go_file("comments.go", "// Just a comment\npackage api\n\nimport \"fmt\"\n") == []


def test_parse_go_file_without_package_clause_defaults_to_main():
    code = """func Standalone() {}"""
    funcs = parse_go_file("snippet.go", code)
    assert len(funcs) == 1
    assert funcs[0].name == "Standalone"
    assert funcs[0].package == "main"


def test_parse_go_file_type_switch_and_select():
    code = """package concurrency

func ProcessItem(val interface{}, ch chan int) string {
    switch v := val.(type) {
    case int:
        return "int"
    case string:
        return "string"
    default:
        return "other"
    }

    select {
    case x := <-ch:
        return "received"
    default:
        return "empty"
    }
}
"""
    funcs = parse_go_file("concurrency/worker.go", code)
    assert len(funcs) == 1
    fn = funcs[0]
    assert fn.name == "ProcessItem"
    # type switch: 1 (type_switch_statement) + 2 (case int, case string) = 3
    # select: 1 (communication_case x := <-ch) = 1
    # total branches = 4, cyclomatic complexity = 1 + 4 = 5
    assert fn.branch_count >= 3
    assert fn.cyclomatic_complexity >= 4


def test_parse_go_file_generic_function():
    code = """package util

func Find[T comparable](slice []T, target T) int {
    for i, v := range slice {
        if v == target {
            return i
        }
    }
    return -1
}
"""
    funcs = parse_go_file("util/generic.go", code)
    assert len(funcs) == 1
    fn = funcs[0]
    assert fn.name == "Find"
    assert "Find[T comparable]" in fn.signature
    assert fn.branch_count == 2
    assert fn.cyclomatic_complexity == 3
