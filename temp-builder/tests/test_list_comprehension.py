"""Tests for list comprehensions in sauravcode.

Syntax:
    [expr for var in iterable]
    [expr for var in iterable if condition]
"""
import pytest
import sys
import os
import io
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from saurav import (
    tokenize, Parser, Interpreter, ListComprehensionNode,
    FunctionNode, FunctionCallNode
)


def run(code):
    """Helper: tokenize → parse → interpret all nodes, return interpreter."""
    tokens = list(tokenize(code))
    parser = Parser(tokens)
    ast_nodes = parser.parse()
    interp = Interpreter()
    buf = io.StringIO()
    with redirect_stdout(buf):
        for node in ast_nodes:
            if isinstance(node, FunctionNode):
                interp.interpret(node)
            elif isinstance(node, FunctionCallNode):
                interp.execute_function(node)
            else:
                interp.interpret(node)
    interp._stdout = buf.getvalue()
    return interp


def evaluate_expr(code):
    """Helper: evaluate a single expression and return the value."""
    full = f"result = {code}"
    interp = run(full)
    return interp.variables["result"]


# ── Basic list comprehensions ──────────────────────────────────────

class TestBasicComprehension:
    def test_simple_identity(self):
        result = evaluate_expr("[x for x in [1, 2, 3]]")
        assert result == [1.0, 2.0, 3.0]

    def test_double(self):
        result = evaluate_expr("[x * 2 for x in [1, 2, 3, 4]]")
        assert result == [2.0, 4.0, 6.0, 8.0]

    def test_square(self):
        result = evaluate_expr("[x * x for x in [1, 2, 3, 4, 5]]")
        assert result == [1.0, 4.0, 9.0, 16.0, 25.0]

    def test_addition(self):
        result = evaluate_expr("[x + 10 for x in [1, 2, 3]]")
        assert result == [11.0, 12.0, 13.0]

    def test_subtraction(self):
        result = evaluate_expr("[x - 1 for x in [5, 10, 15]]")
        assert result == [4.0, 9.0, 14.0]

    def test_empty_source(self):
        result = evaluate_expr("[x for x in []]")
        assert result == []

    def test_single_element(self):
        result = evaluate_expr("[x * 3 for x in [7]]")
        assert result == [21.0]


# ── With filter (if clause) ───────────────────────────────────────

class TestFilteredComprehension:
    def test_filter_even(self):
        result = evaluate_expr("[x for x in [1, 2, 3, 4, 5, 6] if x % 2 == 0]")
        assert result == [2.0, 4.0, 6.0]

    def test_filter_odd(self):
        result = evaluate_expr("[x for x in [1, 2, 3, 4, 5] if x % 2 != 0]")
        assert result == [1.0, 3.0, 5.0]

    def test_filter_greater_than(self):
        result = evaluate_expr("[x for x in [1, 5, 10, 15, 20] if x > 8]")
        assert result == [10.0, 15.0, 20.0]

    def test_filter_with_transform(self):
        result = evaluate_expr("[x * 2 for x in [1, 2, 3, 4, 5] if x > 2]")
        assert result == [6.0, 8.0, 10.0]

    def test_filter_none_pass(self):
        result = evaluate_expr("[x for x in [1, 2, 3] if x > 100]")
        assert result == []

    def test_filter_all_pass(self):
        result = evaluate_expr("[x for x in [10, 20, 30] if x > 0]")
        assert result == [10.0, 20.0, 30.0]

    def test_filter_equality(self):
        result = evaluate_expr("[x for x in [1, 2, 3, 2, 1] if x == 2]")
        assert result == [2.0, 2.0]


# ── With range() builtin ─────────────────────────────────────────

class TestRangeComprehension:
    def test_range_basic(self):
        result = evaluate_expr("[x for x in range 5]")
        assert result == [0.0, 1.0, 2.0, 3.0, 4.0]

    def test_range_squared(self):
        result = evaluate_expr("[x * x for x in range 6]")
        assert result == [0.0, 1.0, 4.0, 9.0, 16.0, 25.0]

    def test_range_filtered(self):
        result = evaluate_expr("[x for x in range 10 if x > 5]")
        assert result == [6.0, 7.0, 8.0, 9.0]

    def test_range_transform_and_filter(self):
        result = evaluate_expr("[x * 3 for x in range 8 if x % 2 == 0]")
        assert result == [0.0, 6.0, 12.0, 18.0]


# ── With variables ────────────────────────────────────────────────

class TestVariableComprehension:
    def test_iterate_over_variable(self):
        interp = run('nums = [10, 20, 30]\nresult = [x + 1 for x in nums]')
        assert interp.variables["result"] == [11.0, 21.0, 31.0]

    def test_use_outer_variable(self):
        interp = run('factor = 5\nresult = [x * factor for x in [1, 2, 3]]')
        assert interp.variables["result"] == [5.0, 10.0, 15.0]

    def test_comprehension_does_not_leak_var(self):
        """Loop variable should not leak into outer scope."""
        interp = run('x = 999\nresult = [x for x in [1, 2, 3]]\nfinal = x')
        assert interp.variables["result"] == [1.0, 2.0, 3.0]
        assert interp.variables["final"] == 999.0

    def test_comprehension_var_not_defined_before(self):
        """Loop var should be cleaned up if not previously defined."""
        interp = run('result = [item for item in [4, 5, 6]]')
        assert interp.variables["result"] == [4.0, 5.0, 6.0]
        assert "item" not in interp.variables


# ── String iteration ──────────────────────────────────────────────

class TestStringComprehension:
    def test_string_chars(self):
        result = evaluate_expr('[c for c in "abc"]')
        assert result == ["a", "b", "c"]

    def test_string_filter(self):
        result = evaluate_expr('[c for c in "hello" if c != "l"]')
        assert result == ["h", "e", "o"]


# ── Map iteration ─────────────────────────────────────────────────

class TestMapComprehension:
    def test_map_keys(self):
        interp = run('m = {"a": 1, "b": 2, "c": 3}\nresult = [k for k in m]')
        assert sorted(interp.variables["result"]) == ["a", "b", "c"]


# ── With builtins ─────────────────────────────────────────────────

class TestBuiltinComprehension:
    def test_with_len(self):
        interp = run('words = ["hi", "hello", "hey"]\nresult = [len w for w in words]')
        assert interp.variables["result"] == [2.0, 5.0, 3.0]

    def test_with_upper(self):
        interp = run('words = ["hi", "world"]\nresult = [upper w for w in words]')
        assert interp.variables["result"] == ["HI", "WORLD"]

    def test_with_type_of(self):
        interp = run('items = [1, "hi", true]\nresult = [type_of x for x in items]')
        assert interp.variables["result"] == ["number", "string", "bool"]

    def test_with_abs(self):
        result = evaluate_expr("[abs x for x in [-3, -1, 0, 2, -5]]")
        assert result == [3.0, 1.0, 0.0, 2.0, 5.0]


# ── Nested comprehensions ─────────────────────────────────────────

class TestNestedComprehension:
    def test_comprehension_in_comprehension_flat(self):
        """Outer comprehension uses result of inner function."""
        interp = run(
            'lists = [[1, 2], [3, 4], [5, 6]]\n'
            'result = [len sub for sub in lists]'
        )
        assert interp.variables["result"] == [2.0, 2.0, 2.0]


# ── Used in expressions ──────────────────────────────────────────

class TestComprehensionInExpressions:
    def test_len_of_comprehension(self):
        interp = run('result = len [x for x in [1, 2, 3, 4, 5] if x > 2]')
        assert interp.variables["result"] == 3.0

    def test_index_into_comprehension(self):
        interp = run('result = [x * 10 for x in [1, 2, 3]][1]')
        assert interp.variables["result"] == 20.0

    def test_assign_and_iterate(self):
        interp = run(
            'squares = [x * x for x in range 5]\n'
            'total = 0\n'
            'for s in squares\n'
            '    total = total + s'
        )
        # 0 + 1 + 4 + 9 + 16 = 30
        assert interp.variables["total"] == 30.0


# ── In functions ──────────────────────────────────────────────────

class TestComprehensionInFunctions:
    def test_return_comprehension(self):
        interp = run(
            'function doubles nums\n'
            '    return [x * 2 for x in nums]\n'
            'items = [3, 6, 9]\n'
            'result = doubles items'
        )
        assert interp.variables["result"] == [6.0, 12.0, 18.0]

    def test_comprehension_uses_function_param(self):
        interp = run(
            'function scale items\n'
            '    return [x * 10 for x in items]\n'
            'data = [1, 2, 3]\n'
            'result = scale data'
        )
        assert interp.variables["result"] == [10.0, 20.0, 30.0]


# ── With lambdas ──────────────────────────────────────────────────

class TestComprehensionWithLambda:
    def test_apply_lambda_via_map(self):
        interp = run(
            'fn = lambda x -> x + 100\n'
            'result = map fn [1, 2, 3]'
        )
        assert interp.variables["result"] == [101.0, 102.0, 103.0]

    def test_comprehension_vs_map_equivalence(self):
        """Comprehension and map+lambda should produce same results."""
        interp = run(
            'a = [x * 2 for x in [1, 2, 3]]\n'
            'b = map (lambda x -> x * 2) [1, 2, 3]\n'
        )
        assert interp.variables["a"] == interp.variables["b"]


# ── With try/catch ────────────────────────────────────────────────

class TestComprehensionWithTryCatch:
    def test_comprehension_in_try(self):
        interp = run(
            'try\n'
            '    result = [x * 2 for x in [1, 2, 3]]\n'
            'catch e\n'
            '    result = []'
        )
        assert interp.variables["result"] == [2.0, 4.0, 6.0]


# ── Error cases ───────────────────────────────────────────────────

class TestComprehensionErrors:
    def test_iterate_over_number(self):
        with pytest.raises(RuntimeError, match="Cannot iterate"):
            evaluate_expr("[x for x in 42]")

    def test_iterate_over_bool(self):
        with pytest.raises(RuntimeError, match="Cannot iterate"):
            evaluate_expr("[x for x in true]")


# ── AST node ──────────────────────────────────────────────────────

class TestListComprehensionNode:
    def test_repr_no_condition(self):
        from saurav import NumberNode, IdentifierNode
        node = ListComprehensionNode(
            IdentifierNode("x"), "x", IdentifierNode("items")
        )
        assert "ListComprehensionNode" in repr(node)
        assert "condition" not in repr(node)

    def test_repr_with_condition(self):
        from saurav import NumberNode, IdentifierNode, CompareNode
        cond = CompareNode(IdentifierNode("x"), ">", NumberNode(0))
        node = ListComprehensionNode(
            IdentifierNode("x"), "x", IdentifierNode("items"), cond
        )
        r = repr(node)
        assert "ListComprehensionNode" in r
        assert "condition=" in r


# ── Regular list literals still work ──────────────────────────────

class TestListLiteralUnchanged:
    def test_empty_list(self):
        assert evaluate_expr("[]") == []

    def test_single_element(self):
        assert evaluate_expr("[42]") == [42.0]

    def test_multiple_elements(self):
        assert evaluate_expr("[1, 2, 3]") == [1.0, 2.0, 3.0]

    def test_trailing_comma(self):
        assert evaluate_expr("[1, 2, 3,]") == [1.0, 2.0, 3.0]

    def test_nested_list(self):
        assert evaluate_expr("[[1, 2], [3, 4]]") == [[1.0, 2.0], [3.0, 4.0]]

    def test_mixed_types(self):
        result = evaluate_expr('[1, "hello", true]')
        assert result == [1.0, "hello", True]


# ── _is_truthy consistency (bug fix) ─────────────────────────────

class TestComprehensionIsTruthyConsistency:
    """Verify list comprehension 'if' uses sauravcode's _is_truthy,
    not Python's native truthiness."""

    def test_empty_list_is_truthy(self):
        """Empty list [] is truthy in sauravcode — should NOT filter."""
        result = evaluate_expr("[x for x in [1, 2, 3] if []]")
        assert result == [1.0, 2.0, 3.0], "Empty list should be truthy in sauravcode"

    def test_zero_is_falsy(self):
        """Zero is falsy in both sauravcode and Python — should filter all."""
        result = evaluate_expr("[x for x in [1, 2, 3] if 0]")
        assert result == []

    def test_nonempty_list_is_truthy(self):
        """Non-empty list is truthy — should keep all elements."""
        result = evaluate_expr("[x for x in [1, 2, 3] if [99]]")
        assert result == [1.0, 2.0, 3.0]
