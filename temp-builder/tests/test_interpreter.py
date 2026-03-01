"""
Comprehensive tests for the sauravcode interpreter (saurav.py).

Tests tokenizer, parser, and interpreter with coverage for all
language features: arithmetic, functions, control flow, lists,
strings, booleans, logical operators, error handling, and edge cases.
"""

import pytest
import sys
import os
import io
from contextlib import redirect_stdout

# Add repo root to path so we can import saurav
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from saurav import (
    tokenize,
    Parser,
    Interpreter,
    ReturnSignal,
    NumberNode,
    StringNode,
    BoolNode,
    IdentifierNode,
    BinaryOpNode,
    UnaryOpNode,
    CompareNode,
    LogicalNode,
    format_value,
    _repl_execute,
    FunctionNode,
    FunctionCallNode,
    AssignmentNode,
    IndexedAssignmentNode,
    PrintNode,
    ReturnNode,
    IfNode,
    WhileNode,
    ForNode,
    ListNode,
    IndexNode,
    AppendNode,
    LenNode,
    MapNode,
    FStringNode,
    ASTNode,
    TryCatchNode,
    ThrowNode,
    ThrowSignal,
    ForEachNode,
)


# ============================================================
# Helpers
# ============================================================

def run_code(code: str) -> str:
    """Tokenize, parse, interpret sauravcode and capture stdout."""
    tokens = list(tokenize(code))
    parser = Parser(tokens)
    ast_nodes = parser.parse()
    interpreter = Interpreter()

    buf = io.StringIO()
    with redirect_stdout(buf):
        for node in ast_nodes:
            if isinstance(node, FunctionNode):
                interpreter.interpret(node)
            elif isinstance(node, FunctionCallNode):
                result = interpreter.execute_function(node)
            else:
                interpreter.interpret(node)
    return buf.getvalue()


def run_code_result(code: str):
    """Run code and return the interpreter's last result (for function calls)."""
    tokens = list(tokenize(code))
    parser = Parser(tokens)
    ast_nodes = parser.parse()
    interpreter = Interpreter()

    result = None
    for node in ast_nodes:
        if isinstance(node, FunctionNode):
            interpreter.interpret(node)
        elif isinstance(node, FunctionCallNode):
            result = interpreter.execute_function(node)
        else:
            interpreter.interpret(node)
    return result


# ============================================================
# Tokenizer Tests
# ============================================================

class TestTokenizer:
    def test_number_tokens(self):
        tokens = list(tokenize("42\n"))
        number_tokens = [t for t in tokens if t[0] == "NUMBER"]
        assert len(number_tokens) == 1
        assert number_tokens[0][1] == "42"

    def test_float_tokens(self):
        tokens = list(tokenize("3.14\n"))
        number_tokens = [t for t in tokens if t[0] == "NUMBER"]
        assert number_tokens[0][1] == "3.14"

    def test_string_tokens(self):
        tokens = list(tokenize('"hello"\n'))
        string_tokens = [t for t in tokens if t[0] == "STRING"]
        assert len(string_tokens) == 1
        assert string_tokens[0][1] == '"hello"'

    def test_operator_tokens(self):
        tokens = list(tokenize("+ - * / %\n"))
        op_tokens = [t for t in tokens if t[0] == "OP"]
        assert len(op_tokens) == 5
        assert [t[1] for t in op_tokens] == ["+", "-", "*", "/", "%"]

    def test_comparison_tokens(self):
        tokens = list(tokenize("== != < > <= >=\n"))
        types = [t[0] for t in tokens if t[0] not in ("NEWLINE", "SKIP")]
        assert types == ["EQ", "NEQ", "LT", "GT", "LTE", "GTE"]

    def test_keyword_tokens(self):
        tokens = list(tokenize("if else while for function return print true false and or not\n"))
        kw_tokens = [t for t in tokens if t[0] == "KEYWORD"]
        assert len(kw_tokens) >= 10

    def test_indent_dedent(self):
        code = "if true\n    x = 1\nx = 2\n"
        tokens = list(tokenize(code))
        types = [t[0] for t in tokens]
        assert "INDENT" in types
        assert "DEDENT" in types

    def test_comment_token(self):
        """Comments are now stripped at the tokenizer level for efficiency."""
        tokens = list(tokenize("# this is a comment\n"))
        comment_tokens = [t for t in tokens if t[0] == "COMMENT"]
        assert len(comment_tokens) == 0, "Comments should be stripped during tokenization"
        # Verify comments don't interfere with subsequent code
        tokens = list(tokenize("x = 5\n# comment\ny = 10\n"))
        idents = [t[1] for t in tokens if t[0] == "IDENT"]
        assert "x" in idents
        assert "y" in idents

    def test_mismatch_raises(self):
        with pytest.raises(RuntimeError, match="Unexpected character"):
            list(tokenize("@\n"))

    def test_assign_token(self):
        tokens = list(tokenize("x = 5\n"))
        types = [t[0] for t in tokens if t[0] not in ("NEWLINE",)]
        assert "IDENT" in types
        assert "ASSIGN" in types
        assert "NUMBER" in types

    def test_brackets(self):
        tokens = list(tokenize("[1, 2]\n"))
        types = [t[0] for t in tokens if t[0] != "NEWLINE"]
        assert "LBRACKET" in types
        assert "RBRACKET" in types
        assert "COMMA" in types

    def test_parens(self):
        tokens = list(tokenize("(1 + 2)\n"))
        types = [t[0] for t in tokens if t[0] != "NEWLINE"]
        assert "LPAREN" in types
        assert "RPAREN" in types

    def test_multiple_dedents_at_eof(self):
        code = "if true\n    if true\n        x = 1\n"
        tokens = list(tokenize(code))
        dedent_count = sum(1 for t in tokens if t[0] == "DEDENT")
        assert dedent_count >= 2


# ============================================================
# Parser Tests
# ============================================================

class TestParser:
    def test_parse_assignment(self):
        tokens = list(tokenize("x = 42\n"))
        parser = Parser(tokens)
        ast = parser.parse()
        assignments = [n for n in ast if isinstance(n, AssignmentNode)]
        assert len(assignments) == 1
        assert assignments[0].name == "x"

    def test_parse_print(self):
        tokens = list(tokenize('print "hello"\n'))
        parser = Parser(tokens)
        ast = parser.parse()
        prints = [n for n in ast if isinstance(n, PrintNode)]
        assert len(prints) == 1

    def test_parse_function(self):
        code = "function add x y\n    return x + y\n"
        tokens = list(tokenize(code))
        parser = Parser(tokens)
        ast = parser.parse()
        funcs = [n for n in ast if isinstance(n, FunctionNode)]
        assert len(funcs) == 1
        assert funcs[0].name == "add"
        assert funcs[0].params == ["x", "y"]

    def test_parse_if(self):
        code = "if true\n    x = 1\n"
        tokens = list(tokenize(code))
        parser = Parser(tokens)
        ast = parser.parse()
        ifs = [n for n in ast if isinstance(n, IfNode)]
        assert len(ifs) == 1

    def test_parse_while(self):
        code = "while true\n    x = 1\n"
        tokens = list(tokenize(code))
        parser = Parser(tokens)
        ast = parser.parse()
        whiles = [n for n in ast if isinstance(n, WhileNode)]
        assert len(whiles) == 1

    def test_parse_for(self):
        code = "for i 0 10\n    print i\n"
        tokens = list(tokenize(code))
        parser = Parser(tokens)
        ast = parser.parse()
        fors = [n for n in ast if isinstance(n, ForNode)]
        assert len(fors) == 1
        assert fors[0].var == "i"

    def test_parse_list_literal(self):
        tokens = list(tokenize("x = [1, 2, 3]\n"))
        parser = Parser(tokens)
        ast = parser.parse()
        assignments = [n for n in ast if isinstance(n, AssignmentNode)]
        assert len(assignments) == 1
        assert isinstance(assignments[0].expression, ListNode)

    def test_parse_function_call(self):
        code = "function f x\n    return x\nf 5\n"
        tokens = list(tokenize(code))
        parser = Parser(tokens)
        ast = parser.parse()
        calls = [n for n in ast if isinstance(n, FunctionCallNode)]
        assert len(calls) == 1
        assert calls[0].name == "f"

    def test_parse_binary_expression(self):
        tokens = list(tokenize("x = 3 + 4 * 2\n"))
        parser = Parser(tokens)
        ast = parser.parse()
        assign = [n for n in ast if isinstance(n, AssignmentNode)][0]
        # The expression should be a BinaryOpNode
        assert isinstance(assign.expression, BinaryOpNode)

    def test_parse_unexpected_token_raises(self):
        tokens = [("MISMATCH", "@", 1, 0)]
        parser = Parser(tokens)
        with pytest.raises(SyntaxError):
            parser.parse()


# ============================================================
# Interpreter Tests — Arithmetic
# ============================================================

class TestArithmetic:
    def test_addition(self):
        output = run_code("print 3 + 5\n")
        assert output.strip() == "8"

    def test_subtraction(self):
        output = run_code("print 10 - 3\n")
        assert output.strip() == "7"

    def test_multiplication(self):
        output = run_code("print 4 * 6\n")
        assert output.strip() == "24"

    def test_division(self):
        output = run_code("print 10 / 4\n")
        assert output.strip() == "2.5"

    def test_modulo(self):
        output = run_code("print 15 % 4\n")
        assert output.strip() == "3"

    def test_division_by_zero(self):
        with pytest.raises(RuntimeError, match="Division by zero"):
            run_code("print 1 / 0\n")

    def test_modulo_by_zero(self):
        with pytest.raises(RuntimeError, match="Modulo by zero"):
            run_code("print 1 % 0\n")

    def test_negative_numbers(self):
        output = run_code("x = -42\nprint x\n")
        assert output.strip() == "-42"

    def test_parenthesized_expression(self):
        output = run_code("print (2 + 3) * 4\n")
        assert output.strip() == "20"

    def test_nested_arithmetic(self):
        output = run_code("print (2 + 3) * (4 - 1)\n")
        assert output.strip() == "15"

    def test_operator_precedence(self):
        # Multiplication before addition
        output = run_code("print 2 + 3 * 4\n")
        assert output.strip() == "14"


# ============================================================
# Interpreter Tests — Variables
# ============================================================

class TestVariables:
    def test_assignment_and_print(self):
        output = run_code("x = 10\nprint x\n")
        assert output.strip() == "10"

    def test_reassignment(self):
        output = run_code("x = 5\nx = 10\nprint x\n")
        assert output.strip() == "10"

    def test_variable_in_expression(self):
        output = run_code("x = 3\ny = 7\nprint x + y\n")
        assert output.strip() == "10"

    def test_undefined_variable_raises(self):
        with pytest.raises(RuntimeError, match="not defined"):
            run_code("print z\n")


# ============================================================
# Interpreter Tests — Functions
# ============================================================

class TestFunctions:
    def test_simple_function(self):
        code = "function add x y\n    return x + y\nprint add 3 5\n"
        output = run_code(code)
        assert output.strip() == "8"

    def test_nested_function_call(self):
        code = """function square x
    return x * x

function hypotenuse a b
    sa = square a
    sb = square b
    return sa + sb

print hypotenuse 3 4
"""
        output = run_code(code)
        assert output.strip() == "25"

    def test_recursion(self):
        code = """function factorial n
    if n <= 1
        return 1
    return n * factorial (n - 1)

print factorial 5
"""
        output = run_code(code)
        assert output.strip() == "120"

    def test_function_with_print(self):
        code = """function greet name
    print name
    return 0

greet "hello"
"""
        output = run_code(code)
        assert output.strip() == "hello"

    def test_undefined_function_raises(self):
        with pytest.raises(RuntimeError, match="not defined"):
            run_code("nonexistent 5\n")

    def test_fibonacci(self):
        code = """function fib n
    if n <= 1
        return n
    return fib (n - 1) + fib (n - 2)

print fib 10
"""
        output = run_code(code)
        assert output.strip() == "55"

    def test_function_scope_isolation(self):
        """Function vars shouldn't leak to outer scope."""
        code = """x = 100
function f a
    x = 999
    return x

f 0
print x
"""
        output = run_code(code)
        assert output.strip() == "100"


# ============================================================
# Interpreter Tests — Control Flow
# ============================================================

class TestControlFlow:
    def test_if_true(self):
        output = run_code("if true\n    print 1\n")
        assert output.strip() == "1"

    def test_if_false(self):
        output = run_code("if false\n    print 1\n")
        assert output.strip() == ""

    def test_if_else(self):
        code = "if false\n    print 1\nelse\n    print 2\n"
        output = run_code(code)
        assert output.strip() == "2"

    def test_if_elif_else(self):
        code = """score = 85
if score >= 90
    print "A"
else if score >= 80
    print "B"
else
    print "C"
"""
        output = run_code(code)
        assert output.strip() == "B"

    def test_while_loop(self):
        code = """counter = 0
while counter < 3
    print counter
    counter = counter + 1
"""
        output = run_code(code)
        assert output.strip() == "0\n1\n2"

    def test_for_loop(self):
        code = """for i 1 4
    print i
"""
        output = run_code(code)
        assert output.strip() == "1\n2\n3"

    def test_nested_if(self):
        code = """x = 5
if x > 0
    if x < 10
        print "single digit positive"
"""
        output = run_code(code)
        assert output.strip() == "single digit positive"


# ============================================================
# Interpreter Tests — Comparisons
# ============================================================

class TestComparisons:
    def test_equality(self):
        output = run_code("if 5 == 5\n    print 1\n")
        assert output.strip() == "1"

    def test_inequality(self):
        output = run_code("if 5 != 3\n    print 1\n")
        assert output.strip() == "1"

    def test_less_than(self):
        output = run_code("if 3 < 5\n    print 1\n")
        assert output.strip() == "1"

    def test_greater_than(self):
        output = run_code("if 5 > 3\n    print 1\n")
        assert output.strip() == "1"

    def test_less_than_or_equal(self):
        output = run_code("if 5 <= 5\n    print 1\n")
        assert output.strip() == "1"

    def test_greater_than_or_equal(self):
        output = run_code("if 5 >= 5\n    print 1\n")
        assert output.strip() == "1"


# ============================================================
# Interpreter Tests — Logical Operators
# ============================================================

class TestLogicalOperators:
    def test_and_true(self):
        output = run_code("if true and true\n    print 1\n")
        assert output.strip() == "1"

    def test_and_false(self):
        output = run_code("if true and false\n    print 1\n")
        assert output.strip() == ""

    def test_or_true(self):
        output = run_code("if false or true\n    print 1\n")
        assert output.strip() == "1"

    def test_or_false(self):
        output = run_code("if false or false\n    print 1\n")
        assert output.strip() == ""

    def test_not(self):
        output = run_code("if not false\n    print 1\n")
        assert output.strip() == "1"

    def test_combined_logical(self):
        output = run_code("if true and not false\n    print 1\n")
        assert output.strip() == "1"


# ============================================================
# Interpreter Tests — Strings
# ============================================================

class TestStrings:
    def test_string_print(self):
        output = run_code('print "hello world"\n')
        assert output.strip() == "hello world"

    def test_string_variable(self):
        output = run_code('x = "test"\nprint x\n')
        assert output.strip() == "test"

    def test_string_concatenation(self):
        output = run_code('print "hello" + " world"\n')
        assert output.strip() == "hello world"


# ============================================================
# Interpreter Tests — Booleans
# ============================================================

class TestBooleans:
    def test_true_print(self):
        output = run_code("print true\n")
        assert output.strip() == "true"

    def test_false_print(self):
        output = run_code("print false\n")
        assert output.strip() == "false"

    def test_bool_variable(self):
        output = run_code("x = true\nprint x\n")
        assert output.strip() == "true"


# ============================================================
# Interpreter Tests — Lists
# ============================================================

class TestLists:
    def test_list_creation(self):
        output = run_code("nums = [10, 20, 30]\nprint nums[0]\n")
        assert output.strip() == "10"

    def test_list_index(self):
        output = run_code("nums = [10, 20, 30]\nprint nums[2]\n")
        assert output.strip() == "30"

    def test_list_len(self):
        output = run_code("nums = [10, 20, 30]\nprint len nums\n")
        assert output.strip() == "3"

    def test_list_append(self):
        code = """nums = [10, 20]
append nums 30
print len nums
print nums[2]
"""
        output = run_code(code)
        lines = output.strip().split("\n")
        assert lines[0] == "3"
        assert lines[1] == "30"

    def test_list_out_of_bounds(self):
        with pytest.raises(RuntimeError, match="out of bounds"):
            run_code("nums = [1, 2]\nprint nums[5]\n")

    def test_append_to_non_list_raises(self):
        with pytest.raises(RuntimeError, match="not a list"):
            run_code("x = 5\nappend x 10\n")

    def test_empty_list(self):
        output = run_code("nums = []\nprint len nums\n")
        assert output.strip() == "0"

    def test_len_of_string(self):
        output = run_code('print len "hello"\n')
        assert output.strip() == "5"


# ============================================================
# Interpreter Tests — Print Formatting
# ============================================================

class TestPrintFormatting:
    def test_integer_no_decimal(self):
        """Integers should print without .0"""
        output = run_code("print 42\n")
        assert output.strip() == "42"

    def test_float_with_decimal(self):
        output = run_code("print 3.5\n")
        assert output.strip() == "3.5"

    def test_expression_result_integer(self):
        output = run_code("print 6 / 2\n")
        assert output.strip() == "3"


# ============================================================
# Interpreter Tests — Truthiness
# ============================================================

class TestTruthiness:
    def test_zero_is_falsy(self):
        output = run_code("if 0\n    print 1\n")
        assert output.strip() == ""

    def test_nonzero_is_truthy(self):
        output = run_code("if 42\n    print 1\n")
        assert output.strip() == "1"

    def test_empty_string_is_falsy(self):
        output = run_code('if ""\n    print 1\n')
        assert output.strip() == ""

    def test_nonempty_string_is_truthy(self):
        output = run_code('if "x"\n    print 1\n')
        assert output.strip() == "1"


# ============================================================
# Interpreter Tests — Edge Cases & Error Handling
# ============================================================

class TestEdgeCases:
    def test_return_signal(self):
        """ReturnSignal should carry a value."""
        sig = ReturnSignal(42)
        assert sig.value == 42

    def test_ast_node_repr(self):
        """AST nodes should have useful repr."""
        node = NumberNode(42.0)
        assert "42" in repr(node)
        node = StringNode("hello")
        assert "hello" in repr(node)

    def test_comments_ignored(self):
        output = run_code("# this is a comment\nprint 1\n")
        assert output.strip() == "1"

    def test_empty_program(self):
        output = run_code("\n")
        assert output.strip() == ""

    def test_multiple_statements(self):
        code = "print 1\nprint 2\nprint 3\n"
        output = run_code(code)
        assert output.strip() == "1\n2\n3"

    def test_len_non_iterable_raises(self):
        with pytest.raises(RuntimeError, match="Cannot get length"):
            run_code("x = 5\nprint len x\n")

    def test_index_non_list_raises(self):
        with pytest.raises(RuntimeError, match="Cannot index"):
            run_code("x = 5\nprint x[0]\n")

    def test_unknown_node_type_raises(self):
        """Unknown node in interpreter should raise."""
        interp = Interpreter()
        with pytest.raises(ValueError, match="Unknown AST node"):
            interp.interpret("not_a_node")

    def test_unknown_expression_type_raises(self):
        interp = Interpreter()
        with pytest.raises(ValueError, match="Unknown node type"):
            interp.evaluate("not_a_node")


# ============================================================
# Integration: run the .srv test files
# ============================================================

class TestSrvFiles:
    def test_test_srv_runs(self):
        """test.srv should run without errors."""
        test_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test.srv")
        if not os.path.isfile(test_file):
            pytest.skip("test.srv not found")
        with open(test_file) as f:
            code = f.read()
        # Should not raise
        run_code(code)

    def test_test_all_srv_runs(self):
        """test_all.srv should run and produce expected output."""
        test_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test_all.srv")
        if not os.path.isfile(test_file):
            pytest.skip("test_all.srv not found")
        with open(test_file) as f:
            code = f.read()
        output = run_code(code)
        assert "=== all tests passed ===" in output


# ============================================================
# REPL Tests
# ============================================================

class TestFormatValue:
    def test_format_integer_float(self):
        assert format_value(5.0) == "5"

    def test_format_float(self):
        assert format_value(3.14) == "3.14"

    def test_format_string(self):
        assert format_value("hello") == '"hello"'

    def test_format_bool_true(self):
        assert format_value(True) == "true"

    def test_format_bool_false(self):
        assert format_value(False) == "false"

    def test_format_list(self):
        assert format_value([1.0, 2.0, 3.0]) == "[1, 2, 3]"

    def test_format_nested_list(self):
        result = format_value([1.0, [2.0, 3.0]])
        assert result == "[1, [2, 3]]"

    def test_format_none(self):
        assert format_value(None) is None

    def test_format_empty_list(self):
        assert format_value([]) == "[]"

    def test_format_mixed_list(self):
        result = format_value([1.0, "hi", True])
        assert '"hi"' in result
        assert "true" in result


class TestReplExecute:
    def test_simple_print(self):
        interp = Interpreter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            _repl_execute("print 42\n", interp)
        assert buf.getvalue().strip() == "42"

    def test_variable_persists(self):
        interp = Interpreter()
        _repl_execute("x = 10\n", interp)
        assert interp.variables.get("x") == 10.0

    def test_function_persists(self):
        interp = Interpreter()
        _repl_execute("function double x\n    return x * 2\n", interp)
        assert "double" in interp.functions

    def test_function_call_after_define(self):
        interp = Interpreter()
        _repl_execute("function add a b\n    return a + b\n", interp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            _repl_execute("print add 3 5\n", interp)
        assert buf.getvalue().strip() == "8"

    def test_if_statement(self):
        interp = Interpreter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            _repl_execute("if true\n    print 1\n", interp)
        assert buf.getvalue().strip() == "1"

    def test_while_loop(self):
        interp = Interpreter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            _repl_execute("x = 0\nwhile x < 3\n    print x\n    x = x + 1\n", interp)
        assert buf.getvalue().strip() == "0\n1\n2"

    def test_for_loop(self):
        interp = Interpreter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            _repl_execute("for i 1 4\n    print i\n", interp)
        assert buf.getvalue().strip() == "1\n2\n3"

    def test_syntax_error_does_not_crash(self):
        """Syntax errors in REPL should raise, not crash."""
        interp = Interpreter()
        with pytest.raises(SyntaxError):
            _repl_execute("if\n", interp)

    def test_list_operations(self):
        interp = Interpreter()
        _repl_execute("nums = [10, 20, 30]\n", interp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            _repl_execute("print nums[1]\n", interp)
        assert buf.getvalue().strip() == "20"

    def test_multiple_sessions(self):
        """State persists across multiple _repl_execute calls."""
        interp = Interpreter()
        _repl_execute("x = 5\n", interp)
        _repl_execute("y = x + 10\n", interp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            _repl_execute("print y\n", interp)
        assert buf.getvalue().strip() == "15"

    def test_function_call_result_printed(self):
        """Standalone function call results should be printed in REPL."""
        interp = Interpreter()
        _repl_execute("function sq x\n    return x * x\n", interp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            _repl_execute("sq 7\n", interp)
        output = buf.getvalue().strip()
        assert output == "49"

    def test_string_expression(self):
        interp = Interpreter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            _repl_execute('print "hello world"\n', interp)
        assert buf.getvalue().strip() == "hello world"


# ============================================================
# Built-in Standard Library Tests
# ============================================================

class TestBuiltinStringFunctions:
    def test_upper(self):
        output = run_code('print upper "hello"\n')
        assert output.strip() == "HELLO"

    def test_upper_with_spaces(self):
        output = run_code('print upper "hello world"\n')
        assert output.strip() == "HELLO WORLD"

    def test_lower(self):
        output = run_code('print lower "HELLO"\n')
        assert output.strip() == "hello"

    def test_trim(self):
        output = run_code('print trim "  hello  "\n')
        assert output.strip() == "hello"

    def test_replace(self):
        output = run_code('print replace "hello world" "world" "sauravcode"\n')
        assert output.strip() == "hello sauravcode"

    def test_split(self):
        code = 'words = split "a-b-c" "-"\nprint words[0]\nprint words[1]\nprint words[2]\n'
        output = run_code(code)
        lines = output.strip().split('\n')
        assert lines == ['a', 'b', 'c']

    def test_join(self):
        code = 'words = split "a-b-c" "-"\nprint join ", " words\n'
        output = run_code(code)
        assert output.strip() == "a, b, c"

    def test_contains_string_true(self):
        output = run_code('print contains "hello" "ell"\n')
        assert output.strip() == "true"

    def test_contains_string_false(self):
        output = run_code('print contains "hello" "xyz"\n')
        assert output.strip() == "false"

    def test_starts_with_true(self):
        output = run_code('print starts_with "sauravcode" "saurav"\n')
        assert output.strip() == "true"

    def test_starts_with_false(self):
        output = run_code('print starts_with "sauravcode" "code"\n')
        assert output.strip() == "false"

    def test_ends_with_true(self):
        output = run_code('print ends_with "sauravcode" "code"\n')
        assert output.strip() == "true"

    def test_ends_with_false(self):
        output = run_code('print ends_with "sauravcode" "saurav"\n')
        assert output.strip() == "false"

    def test_substring(self):
        output = run_code('print substring "hello world" 0 5\n')
        assert output.strip() == "hello"

    def test_index_of_found(self):
        output = run_code('print index_of "hello" "ll"\n')
        assert output.strip() == "2"

    def test_index_of_not_found(self):
        output = run_code('print index_of "hello" "xyz"\n')
        assert output.strip() == "-1"

    def test_char_at(self):
        output = run_code('print char_at "hello" 0\n')
        assert output.strip() == "h"

    def test_char_at_last(self):
        output = run_code('print char_at "hello" 4\n')
        assert output.strip() == "o"

    def test_char_at_out_of_bounds(self):
        with pytest.raises(RuntimeError, match="out of bounds"):
            run_code('print char_at "hi" 5\n')

    def test_upper_non_string_raises(self):
        with pytest.raises(RuntimeError, match="expects a string"):
            run_code('print upper 42\n')

    def test_lower_non_string_raises(self):
        with pytest.raises(RuntimeError, match="expects a string"):
            run_code('print lower 42\n')


class TestBuiltinMathFunctions:
    def test_abs_positive(self):
        output = run_code('print abs 42\n')
        assert output.strip() == "42"

    def test_abs_negative(self):
        output = run_code('print abs (-42)\n')
        assert output.strip() == "42"

    def test_round_integer(self):
        output = run_code('print round 3.7\n')
        assert output.strip() == "4"

    def test_round_with_places(self):
        output = run_code('print round 3.14159 2\n')
        assert output.strip() == "3.14"

    def test_floor(self):
        output = run_code('print floor 3.7\n')
        assert output.strip() == "3"

    def test_ceil(self):
        output = run_code('print ceil 3.2\n')
        assert output.strip() == "4"

    def test_sqrt(self):
        output = run_code('print sqrt 16\n')
        assert output.strip() == "4"

    def test_sqrt_irrational(self):
        output = run_code('print sqrt 2\n')
        assert output.strip().startswith("1.41421")

    def test_sqrt_negative_raises(self):
        with pytest.raises(RuntimeError, match="negative"):
            run_code('print sqrt (-4)\n')

    def test_power(self):
        output = run_code('print power 2 10\n')
        assert output.strip() == "1024"

    def test_power_fractional(self):
        output = run_code('print power 9 0.5\n')
        assert output.strip() == "3"


class TestBuiltinUtilityFunctions:
    def test_type_of_number(self):
        output = run_code('print type_of 42\n')
        assert output.strip() == "number"

    def test_type_of_string(self):
        output = run_code('print type_of "hello"\n')
        assert output.strip() == "string"

    def test_type_of_bool(self):
        output = run_code('print type_of true\n')
        assert output.strip() == "bool"

    def test_to_string_number(self):
        output = run_code('x = to_string 42\nprint x\n')
        assert output.strip() == "42"

    def test_to_number_string(self):
        output = run_code('x = to_number "3.14"\nprint x\n')
        assert output.strip() == "3.14"

    def test_to_number_invalid_raises(self):
        with pytest.raises(RuntimeError, match="Cannot convert"):
            run_code('x = to_number "abc"\nprint x\n')

    def test_range_one_arg(self):
        output = run_code('nums = range 5\nprint len nums\n')
        assert output.strip() == "5"

    def test_range_two_args(self):
        output = run_code('nums = range 1 4\nprint len nums\n')
        assert output.strip() == "3"

    def test_range_three_args(self):
        output = run_code('nums = range 0 10 2\nprint len nums\n')
        assert output.strip() == "5"

    def test_reverse_string(self):
        output = run_code('print reverse "hello"\n')
        assert output.strip() == "olleh"

    def test_reverse_list(self):
        code = 'nums = [1, 2, 3]\nresult = reverse nums\nprint result[0]\n'
        output = run_code(code)
        assert output.strip() == "3"

    def test_sort_list(self):
        code = 'nums = [3, 1, 2]\nresult = sort nums\nprint result[0]\nprint result[1]\nprint result[2]\n'
        output = run_code(code)
        lines = output.strip().split('\n')
        assert lines == ['1', '2', '3']

    def test_sort_non_list_raises(self):
        with pytest.raises(RuntimeError, match="expects a list"):
            run_code('print sort "hello"\n')

    def test_reverse_non_iterable_raises(self):
        with pytest.raises(RuntimeError, match="expects a list or string"):
            run_code('print reverse 42\n')

    def test_wrong_arg_count_raises(self):
        with pytest.raises(RuntimeError, match="expects"):
            run_code('print upper "a" "b"\n')

    def test_user_function_overrides_builtin(self):
        """User-defined functions should override builtins."""
        code = """function upper x
    return "custom"

print upper "hello"
"""
        output = run_code(code)
        assert output.strip() == "custom"


class TestBuiltinStdlibDemo:
    def test_stdlib_demo_runs(self):
        """stdlib_demo.srv should run without errors."""
        test_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "stdlib_demo.srv")
        if not os.path.isfile(test_file):
            pytest.skip("stdlib_demo.srv not found")
        with open(test_file) as f:
            code = f.read()
        output = run_code(code)
        assert "=== all stdlib tests passed ===" in output


# ============================================================
# Map/Dictionary Tests
# ============================================================

class TestMapLiteral:
    """Test map literal parsing and creation."""

    def test_empty_map(self):
        code = 'x = {}\nprint len x\n'
        output = run_code(code)
        assert output.strip() == "0"

    def test_single_pair(self):
        code = 'x = {"name": "Alice"}\nprint x["name"]\n'
        output = run_code(code)
        assert output.strip() == "Alice"

    def test_multiple_pairs(self):
        code = 'x = {"a": 1, "b": 2, "c": 3}\nprint len x\n'
        output = run_code(code)
        assert output.strip() == "3"

    def test_numeric_values(self):
        code = 'x = {"x": 10, "y": 20}\nprint x["x"]\nprint x["y"]\n'
        output = run_code(code)
        assert output.strip() == "10\n20"

    def test_boolean_values(self):
        code = 'x = {"flag": true, "off": false}\nprint x["flag"]\nprint x["off"]\n'
        output = run_code(code)
        assert output.strip() == "true\nfalse"

    def test_mixed_value_types(self):
        code = 'x = {"name": "Bob", "age": 25, "active": true}\nprint x["name"]\nprint x["age"]\nprint x["active"]\n'
        output = run_code(code)
        assert output.strip() == "Bob\n25\ntrue"

    def test_nested_map(self):
        code = 'inner = {"x": 1}\nouter = {"data": inner}\nd = outer["data"]\nprint d["x"]\n'
        output = run_code(code)
        assert output.strip() == "1"

    def test_list_value_in_map(self):
        code = 'x = {"nums": [10, 20, 30]}\nnums = x["nums"]\nprint nums[1]\n'
        output = run_code(code)
        assert output.strip() == "20"

    def test_numeric_keys(self):
        code = 'x = {1: "one", 2: "two"}\nprint x[1]\nprint x[2]\n'
        output = run_code(code)
        assert output.strip() == "one\ntwo"


class TestMapAccess:
    """Test map key access and assignment."""

    def test_key_access(self):
        code = 'x = {"greeting": "hello"}\nprint x["greeting"]\n'
        output = run_code(code)
        assert output.strip() == "hello"

    def test_key_assignment_update(self):
        code = 'x = {"a": 1}\nx["a"] = 99\nprint x["a"]\n'
        output = run_code(code)
        assert output.strip() == "99"

    def test_key_assignment_add_new(self):
        code = 'x = {"a": 1}\nx["b"] = 2\nprint x["b"]\nprint len x\n'
        output = run_code(code)
        assert output.strip() == "2\n2"

    def test_key_not_found_error(self):
        code = 'x = {"a": 1}\nprint x["missing"]\n'
        with pytest.raises(RuntimeError, match="Key.*not found"):
            run_code(code)

    def test_multiple_assignments(self):
        code = 'x = {}\nx["a"] = 1\nx["b"] = 2\nx["c"] = 3\nprint len x\nprint x["b"]\n'
        output = run_code(code)
        assert output.strip() == "3\n2"

    def test_overwrite_value(self):
        code = 'x = {"key": "old"}\nx["key"] = "new"\nprint x["key"]\n'
        output = run_code(code)
        assert output.strip() == "new"


class TestMapBuiltins:
    """Test built-in functions for maps."""

    def test_keys(self):
        code = 'x = {"a": 1, "b": 2}\nk = keys x\nprint len k\n'
        output = run_code(code)
        assert output.strip() == "2"

    def test_values(self):
        code = 'x = {"a": 10, "b": 20}\nv = values x\nprint len v\n'
        output = run_code(code)
        assert output.strip() == "2"

    def test_has_key_true(self):
        code = 'x = {"name": "test"}\nresult = has_key (x) "name"\nprint result\n'
        output = run_code(code)
        assert output.strip() == "true"

    def test_has_key_false(self):
        code = 'x = {"name": "test"}\nresult = has_key (x) "missing"\nprint result\n'
        output = run_code(code)
        assert output.strip() == "false"

    def test_contains_map_key_true(self):
        code = 'x = {"a": 1}\nresult = contains (x) "a"\nprint result\n'
        output = run_code(code)
        assert output.strip() == "true"

    def test_contains_map_key_false(self):
        code = 'x = {"a": 1}\nresult = contains (x) "b"\nprint result\n'
        output = run_code(code)
        assert output.strip() == "false"

    def test_len_map(self):
        code = 'x = {"a": 1, "b": 2, "c": 3}\nprint len x\n'
        output = run_code(code)
        assert output.strip() == "3"

    def test_len_empty_map(self):
        code = 'x = {}\nprint len x\n'
        output = run_code(code)
        assert output.strip() == "0"

    def test_type_of_map(self):
        code = 'x = {"a": 1}\nprint type_of x\n'
        output = run_code(code)
        assert output.strip() == "map"

    def test_to_string_map(self):
        code = 'x = {"key": "val"}\ns = to_string x\nprint s\n'
        output = run_code(code)
        assert '"key": "val"' in output

    def test_keys_error_on_non_map(self):
        code = 'x = [1, 2, 3]\nk = keys x\n'
        with pytest.raises(RuntimeError, match="keys expects a map"):
            run_code(code)

    def test_values_error_on_non_map(self):
        code = 'x = "hello"\nv = values x\n'
        with pytest.raises(RuntimeError, match="values expects a map"):
            run_code(code)

    def test_has_key_error_on_non_map(self):
        code = 'x = 42\nresult = has_key (x) "key"\n'
        with pytest.raises(RuntimeError, match="has_key expects a map"):
            run_code(code)


class TestMapPrint:
    """Test map printing and formatting."""

    def test_print_empty_map(self):
        code = 'x = {}\nprint x\n'
        output = run_code(code)
        assert output.strip() == "{}"

    def test_print_string_values(self):
        code = 'x = {"name": "Alice"}\nprint x\n'
        output = run_code(code)
        assert '"name": "Alice"' in output

    def test_print_numeric_values(self):
        code = 'x = {"count": 42}\nprint x\n'
        output = run_code(code)
        assert '"count": 42' in output

    def test_print_nested_map(self):
        code = 'inner = {"x": 1}\nouter = {"data": inner}\nprint outer\n'
        output = run_code(code)
        assert '"data": {"x": 1}' in output

    def test_print_map_with_list(self):
        code = 'x = {"nums": [1, 2, 3]}\nprint x\n'
        output = run_code(code)
        assert '"nums": [1, 2, 3]' in output


class TestMapInControlFlow:
    """Test maps in control flow constructs."""

    def test_map_in_if(self):
        code = '''x = {"status": "active"}
if x["status"] == "active"
    print "yes"
else
    print "no"
'''
        output = run_code(code)
        assert output.strip() == "yes"

    def test_map_in_while(self):
        code = '''x = {"count": 0}
while x["count"] < 3
    x["count"] = x["count"] + 1
print x["count"]
'''
        output = run_code(code)
        assert output.strip() == "3"

    def test_map_in_for_loop(self):
        code = '''x = {}
for i 0 5
    x[i] = i * i
print x[3]
print len x
'''
        output = run_code(code)
        assert output.strip() == "9\n5"

    def test_map_in_function(self):
        code = '''function get_value m key
    return m[key]

data = {"x": 42}
result = get_value (data) "x"
print result
'''
        output = run_code(code)
        assert output.strip() == "42"

    def test_map_returned_from_function(self):
        code = '''function make_point x y
    p = {"x": x, "y": y}
    return p

pt = make_point 3 4
print pt["x"]
print pt["y"]
'''
        output = run_code(code)
        assert output.strip() == "3\n4"


class TestMapWordFrequency:
    """Test practical word frequency counting with maps."""

    def test_word_frequency(self):
        code = '''words = split "a b a c b a" " "
freq = {}
for i 0 len words
    word = words[i]
    if contains (freq) word
        freq[word] = freq[word] + 1
    else
        freq[word] = 1
print freq["a"]
print freq["b"]
print freq["c"]
'''
        output = run_code(code)
        assert output.strip() == "3\n2\n1"


class TestMapDemo:
    """Test that map_demo.srv runs successfully."""

    def test_map_demo_runs(self):
        test_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "map_demo.srv")
        if not os.path.isfile(test_file):
            pytest.skip("map_demo.srv not found")
        with open(test_file) as f:
            code = f.read()
        output = run_code(code)
        assert "=== all map tests passed ===" in output


class TestIndexedAssignment:
    """Test indexed assignment for both lists and maps."""

    def test_list_indexed_assignment(self):
        code = 'x = [10, 20, 30]\nx[1] = 99\nprint x[1]\n'
        output = run_code(code)
        assert output.strip() == "99"

    def test_list_indexed_assignment_first(self):
        code = 'x = [1, 2, 3]\nx[0] = 100\nprint x[0]\n'
        output = run_code(code)
        assert output.strip() == "100"

    def test_list_indexed_assignment_last(self):
        code = 'x = [1, 2, 3]\nx[2] = 300\nprint x[2]\n'
        output = run_code(code)
        assert output.strip() == "300"

    def test_list_indexed_assignment_out_of_bounds(self):
        code = 'x = [1, 2, 3]\nx[5] = 99\n'
        with pytest.raises(RuntimeError, match="out of bounds"):
            run_code(code)

    def test_map_indexed_assignment(self):
        code = 'x = {"a": 1}\nx["b"] = 2\nprint x["a"]\nprint x["b"]\n'
        output = run_code(code)
        assert output.strip() == "1\n2"

    def test_indexed_assignment_non_collection(self):
        code = 'x = 42\nx["a"] = 1\n'
        with pytest.raises(RuntimeError, match="not a list or map"):
            run_code(code)


class TestFormatMap:
    """Test map formatting helper."""

    def test_format_value_map(self):
        result = format_value({"a": 1.0, "b": "hello"})
        assert '"a": 1' in result
        assert '"b": "hello"' in result

    def test_format_value_empty_map(self):
        result = format_value({})
        assert result == "{}"

    def test_format_value_nested_map(self):
        result = format_value({"inner": {"x": 1.0}})
        assert '"inner": {"x": 1}' in result


# ============================================================
# F-String (String Interpolation) Tests
# ============================================================

class TestFStringTokenizer:
    """Test that f-strings are tokenized correctly."""

    def test_fstring_token_type(self):
        tokens = list(tokenize('f"hello"\n'))
        fstring_tokens = [t for t in tokens if t[0] == "FSTRING"]
        assert len(fstring_tokens) == 1
        assert fstring_tokens[0][1] == 'f"hello"'

    def test_fstring_with_expression(self):
        tokens = list(tokenize('f"Hello {name}"\n'))
        fstring_tokens = [t for t in tokens if t[0] == "FSTRING"]
        assert len(fstring_tokens) == 1

    def test_fstring_not_regular_string(self):
        """f-strings should NOT be tokenized as regular STRING."""
        tokens = list(tokenize('f"test"\n'))
        string_tokens = [t for t in tokens if t[0] == "STRING"]
        assert len(string_tokens) == 0

    def test_regular_string_unchanged(self):
        """Regular strings should still work."""
        tokens = list(tokenize('"hello"\n'))
        string_tokens = [t for t in tokens if t[0] == "STRING"]
        assert len(string_tokens) == 1


class TestFStringParser:
    """Test f-string parsing into FStringNode."""

    def test_parse_fstring_literal_only(self):
        tokens = list(tokenize('x = f"hello world"\n'))
        parser = Parser(tokens)
        ast = parser.parse()
        assignments = [n for n in ast if isinstance(n, AssignmentNode)]
        assert len(assignments) == 1
        assert isinstance(assignments[0].expression, FStringNode)

    def test_parse_fstring_with_variable(self):
        tokens = list(tokenize('x = f"Hello {name}"\n'))
        parser = Parser(tokens)
        ast = parser.parse()
        assign = [n for n in ast if isinstance(n, AssignmentNode)][0]
        fstr = assign.expression
        assert isinstance(fstr, FStringNode)
        assert len(fstr.parts) == 2  # "Hello " + name expression

    def test_parse_fstring_repr(self):
        node = FStringNode([StringNode("hello")])
        assert "FStringNode" in repr(node)

    def test_parse_fstring_empty_expression_raises(self):
        tokens = list(tokenize('x = f"Hello {}"\n'))
        parser = Parser(tokens)
        with pytest.raises(SyntaxError, match="Empty expression"):
            parser.parse()

    def test_parse_fstring_unmatched_brace_raises(self):
        tokens = list(tokenize('x = f"Hello {name"\n'))
        parser = Parser(tokens)
        with pytest.raises(SyntaxError, match="Unmatched"):
            parser.parse()


class TestFStringBasic:
    """Test basic f-string evaluation."""

    def test_fstring_no_interpolation(self):
        output = run_code('print f"hello world"\n')
        assert output.strip() == "hello world"

    def test_fstring_single_variable(self):
        output = run_code('name = "Alice"\nprint f"Hello {name}!"\n')
        assert output.strip() == "Hello Alice!"

    def test_fstring_two_variables(self):
        output = run_code('first = "John"\nlast = "Doe"\nprint f"{first} {last}"\n')
        assert output.strip() == "John Doe"

    def test_fstring_number_variable(self):
        output = run_code('x = 42\nprint f"x is {x}"\n')
        assert output.strip() == "x is 42"

    def test_fstring_float_variable(self):
        output = run_code('pi = 3.14\nprint f"pi is {pi}"\n')
        assert output.strip() == "pi is 3.14"

    def test_fstring_boolean_true(self):
        output = run_code('flag = true\nprint f"flag is {flag}"\n')
        assert output.strip() == "flag is true"

    def test_fstring_boolean_false(self):
        output = run_code('flag = false\nprint f"flag is {flag}"\n')
        assert output.strip() == "flag is false"

    def test_fstring_empty_text(self):
        output = run_code('x = "hello"\nprint f"{x}"\n')
        assert output.strip() == "hello"


class TestFStringExpressions:
    """Test f-strings with expressions inside { }."""

    def test_fstring_arithmetic(self):
        output = run_code('x = 10\nprint f"x + 5 = {x + 5}"\n')
        assert output.strip() == "x + 5 = 15"

    def test_fstring_multiplication(self):
        output = run_code('n = 7\nprint f"n * 3 = {n * 3}"\n')
        assert output.strip() == "n * 3 = 21"

    def test_fstring_comparison(self):
        output = run_code('x = 10\nprint f"x > 5? {x > 5}"\n')
        assert output.strip() == "x > 5? true"

    def test_fstring_function_call(self):
        output = run_code('s = "hello"\nprint f"upper: {upper s}"\n')
        assert output.strip() == "upper: HELLO"

    def test_fstring_len(self):
        output = run_code('items = [1, 2, 3]\nprint f"count: {len items}"\n')
        assert output.strip() == "count: 3"

    def test_fstring_complex_expression(self):
        output = run_code('a = 3\nb = 4\nprint f"sum={a + b}, product={a * b}"\n')
        assert output.strip() == "sum=7, product=12"

    def test_fstring_nested_parens(self):
        output = run_code('print f"result={(2 + 3) * 4}"\n')
        assert output.strip() == "result=20"


class TestFStringWithCollections:
    """Test f-strings with lists and maps."""

    def test_fstring_list(self):
        output = run_code('nums = [1, 2, 3]\nprint f"list: {nums}"\n')
        # Lists should print as their formatted representation inside f-strings
        assert "list: [1, 2, 3]" in output.strip()

    def test_fstring_list_index(self):
        output = run_code('nums = [10, 20, 30]\nprint f"first: {nums[0]}"\n')
        assert output.strip() == "first: 10"

    def test_fstring_map(self):
        output = run_code('data = {"key": "val"}\nprint f"map: {data}"\n')
        assert '"key": "val"' in output.strip()

    def test_fstring_map_access(self):
        output = run_code('user = {"name": "Bob"}\nkey = "name"\nprint f"user: {user[key]}"\n')
        assert output.strip() == "user: Bob"

    def test_fstring_len_of_list(self):
        output = run_code('items = [1, 2, 3, 4, 5]\nprint f"{len items} items"\n')
        assert output.strip() == "5 items"


class TestFStringInControlFlow:
    """Test f-strings inside if/while/for constructs."""

    def test_fstring_in_if(self):
        code = '''status = "active"
if status == "active"
    print f"Status: {status}"
'''
        output = run_code(code)
        assert output.strip() == "Status: active"

    def test_fstring_in_for_loop(self):
        code = '''for i 1 4
    print f"item {i}"
'''
        output = run_code(code)
        assert output.strip() == "item 1\nitem 2\nitem 3"

    def test_fstring_in_while_loop(self):
        code = '''n = 3
while n > 0
    print f"countdown: {n}"
    n = n - 1
'''
        output = run_code(code)
        assert output.strip() == "countdown: 3\ncountdown: 2\ncountdown: 1"


class TestFStringInFunctions:
    """Test f-strings inside function definitions."""

    def test_fstring_in_function(self):
        code = '''function greet name
    return f"Hello, {name}!"

result = greet "World"
print result
'''
        output = run_code(code)
        assert output.strip() == "Hello, World!"

    def test_fstring_with_function_params(self):
        code = '''function describe name age
    return f"{name} is {age} years old"

print describe "Alice" 30
'''
        output = run_code(code)
        assert output.strip() == "Alice is 30 years old"

    def test_fstring_with_computed_value(self):
        code = '''function rectangle_info w h
    area = w * h
    return f"Rectangle {w}x{h}, area={area}"

print rectangle_info 5 3
'''
        output = run_code(code)
        assert output.strip() == "Rectangle 5x3, area=15"


class TestFStringAssignment:
    """Test assigning f-strings to variables."""

    def test_assign_fstring(self):
        code = 'name = "Alice"\nmsg = f"Hi {name}"\nprint msg\n'
        output = run_code(code)
        assert output.strip() == "Hi Alice"

    def test_fstring_concatenation(self):
        code = 'x = 1\ny = 2\na = f"x={x}"\nb = f"y={y}"\nprint a + ", " + b\n'
        output = run_code(code)
        assert output.strip() == "x=1, y=2"


class TestFStringEscaping:
    """Test escaped braces in f-strings."""

    def test_escaped_open_brace(self):
        output = run_code('print f"use {{braces}}"\n')
        assert output.strip() == "use {braces}"

    def test_escaped_close_brace(self):
        output = run_code('print f"open{{ close}}"\n')
        assert output.strip() == "open{ close}"

    def test_escaped_braces_with_expression(self):
        output = run_code('x = 42\nprint f"value: {{x}} = {x}"\n')
        assert output.strip() == "value: {x} = 42"


class TestFStringEdgeCases:
    """Test edge cases for f-strings."""

    def test_fstring_only_expression(self):
        output = run_code('x = "hello"\nprint f"{x}"\n')
        assert output.strip() == "hello"

    def test_fstring_adjacent_expressions(self):
        output = run_code('a = "hello"\nb = "world"\nprint f"{a}{b}"\n')
        assert output.strip() == "helloworld"

    def test_fstring_expression_with_spaces(self):
        output = run_code('x = 10\nprint f"{ x + 5 }"\n')
        assert output.strip() == "15"

    def test_fstring_used_as_function_arg(self):
        code = '''function echo msg
    return msg

x = 42
result = echo f"value is {x}"
print result
'''
        output = run_code(code)
        assert output.strip() == "value is 42"

    def test_fstring_in_repl(self):
        interp = Interpreter()
        _repl_execute('name = "Test"\n', interp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            _repl_execute('print f"Hello {name}"\n', interp)
        assert buf.getvalue().strip() == "Hello Test"

    def test_fstring_type_is_string(self):
        output = run_code('x = f"hello"\nprint type_of x\n')
        assert output.strip() == "string"

    def test_fstring_unmatched_closing_brace_raises(self):
        tokens = list(tokenize('x = f"hello }"\n'))
        parser = Parser(tokens)
        with pytest.raises(SyntaxError, match="Unmatched"):
            parser.parse()


class TestFStringDemo:
    """Test that fstring_demo.srv runs successfully."""

    def test_fstring_demo_runs(self):
        test_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fstring_demo.srv")
        if not os.path.isfile(test_file):
            pytest.skip("fstring_demo.srv not found")
        with open(test_file) as f:
            code = f.read()
        output = run_code(code)
        assert "=== all f-string tests passed ===" in output


# ============================================================
# Try/Catch/Throw Tests
# ============================================================

class TestTryCatchTokenizer:
    """Test tokenization of try/catch/throw keywords."""

    def test_try_keyword_token(self):
        tokens = list(tokenize("try\n"))
        kw_tokens = [t for t in tokens if t[0] == "KEYWORD"]
        assert any(t[1] == "try" for t in kw_tokens)

    def test_catch_keyword_token(self):
        tokens = list(tokenize("catch\n"))
        kw_tokens = [t for t in tokens if t[0] == "KEYWORD"]
        assert any(t[1] == "catch" for t in kw_tokens)

    def test_throw_keyword_token(self):
        tokens = list(tokenize("throw\n"))
        kw_tokens = [t for t in tokens if t[0] == "KEYWORD"]
        assert any(t[1] == "throw" for t in kw_tokens)

    def test_try_catch_throw_are_keywords_not_idents(self):
        for word in ("try", "catch", "throw"):
            tokens = list(tokenize(f"{word}\n"))
            kw_tokens = [t for t in tokens if t[0] == "KEYWORD" and t[1] == word]
            ident_tokens = [t for t in tokens if t[0] == "IDENT" and t[1] == word]
            assert len(kw_tokens) == 1
            assert len(ident_tokens) == 0


class TestTryCatchParser:
    """Test parsing of try/catch/throw AST nodes."""

    def test_parse_try_catch_block(self):
        code = 'try\n    x = 1\ncatch err\n    x = 2\n'
        tokens = list(tokenize(code))
        parser = Parser(tokens)
        ast = parser.parse()
        assert len(ast) == 1
        node = ast[0]
        assert isinstance(node, TryCatchNode)
        assert node.error_var == "err"
        assert len(node.body) == 1
        assert len(node.handler) == 1

    def test_parse_throw_string(self):
        code = 'throw "error"\n'
        tokens = list(tokenize(code))
        parser = Parser(tokens)
        ast = parser.parse()
        assert len(ast) == 1
        node = ast[0]
        assert isinstance(node, ThrowNode)
        assert isinstance(node.expression, StringNode)

    def test_parse_throw_number(self):
        code = 'throw 42\n'
        tokens = list(tokenize(code))
        parser = Parser(tokens)
        ast = parser.parse()
        assert len(ast) == 1
        assert isinstance(ast[0], ThrowNode)
        assert isinstance(ast[0].expression, NumberNode)

    def test_parse_throw_fstring(self):
        code = 'throw f"error: {x}"\n'
        tokens = list(tokenize(code))
        parser = Parser(tokens)
        ast = parser.parse()
        assert len(ast) == 1
        assert isinstance(ast[0], ThrowNode)
        assert isinstance(ast[0].expression, FStringNode)

    def test_parse_throw_expression(self):
        code = 'throw 1 + 2\n'
        tokens = list(tokenize(code))
        parser = Parser(tokens)
        ast = parser.parse()
        assert len(ast) == 1
        assert isinstance(ast[0], ThrowNode)
        assert isinstance(ast[0].expression, BinaryOpNode)

    def test_parse_try_catch_with_multiple_statements(self):
        code = 'try\n    x = 1\n    y = 2\ncatch e\n    z = 3\n    w = 4\n'
        tokens = list(tokenize(code))
        parser = Parser(tokens)
        ast = parser.parse()
        assert len(ast) == 1
        node = ast[0]
        assert isinstance(node, TryCatchNode)
        assert len(node.body) == 2
        assert len(node.handler) == 2


class TestTryCatchNodeRepr:
    """Test AST node string representations."""

    def test_try_catch_repr(self):
        node = TryCatchNode([AssignmentNode("x", NumberNode(1))], "err",
                           [PrintNode(IdentifierNode("err"))])
        r = repr(node)
        assert "TryCatchNode" in r
        assert "err" in r

    def test_throw_repr(self):
        node = ThrowNode(StringNode("bad"))
        r = repr(node)
        assert "ThrowNode" in r


class TestTryCatchInterpreter:
    """Test try/catch/throw runtime behavior."""

    def test_try_no_error_runs_body(self):
        code = 'try\n    x = 42\n    print x\ncatch err\n    print "caught"\n'
        output = run_code(code)
        assert output.strip() == "42"

    def test_try_catches_division_by_zero(self):
        code = 'try\n    x = 10 / 0\ncatch err\n    print err\n'
        output = run_code(code)
        assert "Division by zero" in output.strip()

    def test_try_catches_index_out_of_bounds(self):
        code = 'items = [1, 2, 3]\ntry\n    x = items[10]\ncatch err\n    print err\n'
        output = run_code(code)
        assert "out of bounds" in output.strip()

    def test_try_catches_undefined_variable(self):
        code = 'try\n    print undefined_var\ncatch err\n    print "caught"\n'
        output = run_code(code)
        assert "caught" in output.strip()

    def test_throw_string(self):
        code = 'try\n    throw "boom"\ncatch err\n    print err\n'
        output = run_code(code)
        assert output.strip() == "boom"

    def test_throw_number(self):
        code = 'try\n    throw 404\ncatch err\n    print err\n'
        output = run_code(code)
        assert output.strip() == "404"

    def test_throw_fstring(self):
        code = 'x = 42\ntry\n    throw f"bad value: {x}"\ncatch err\n    print err\n'
        output = run_code(code)
        assert output.strip() == "bad value: 42"

    def test_throw_expression(self):
        code = 'try\n    throw 1 + 2\ncatch err\n    print err\n'
        output = run_code(code)
        assert output.strip() == "3"

    def test_catch_binds_error_variable(self):
        code = 'try\n    throw "test error"\ncatch my_err\n    print my_err\n'
        output = run_code(code)
        assert output.strip() == "test error"

    def test_execution_continues_after_catch(self):
        code = 'try\n    throw "oops"\ncatch err\n    print "caught"\nprint "continued"\n'
        output = run_code(code)
        lines = output.strip().split('\n')
        assert lines[0] == "caught"
        assert lines[1] == "continued"

    def test_variable_set_in_catch_persists(self):
        code = 'status = "init"\ntry\n    throw "fail"\ncatch err\n    status = "recovered"\nprint status\n'
        output = run_code(code)
        assert output.strip() == "recovered"

    def test_try_body_stops_at_error(self):
        code = 'try\n    print "before"\n    throw "stop"\n    print "after"\ncatch err\n    print "caught"\n'
        output = run_code(code)
        lines = output.strip().split('\n')
        assert lines == ["before", "caught"]

    def test_throw_from_function(self):
        code = (
            'function fail\n'
            '    throw "function error"\n'
            'try\n'
            '    fail\n'
            'catch err\n'
            '    print err\n'
        )
        output = run_code(code)
        assert output.strip() == "function error"

    def test_throw_from_nested_function(self):
        code = (
            'function inner\n'
            '    throw "deep error"\n'
            'function outer\n'
            '    inner\n'
            '    return 1\n'
            'try\n'
            '    outer\n'
            'catch err\n'
            '    print err\n'
        )
        output = run_code(code)
        assert output.strip() == "deep error"

    def test_throw_in_if_branch(self):
        code = (
            'x = 0 - 1\n'
            'try\n'
            '    if x < 0\n'
            '        throw "negative"\n'
            'catch err\n'
            '    print err\n'
        )
        output = run_code(code)
        assert output.strip() == "negative"

    def test_throw_in_while_loop(self):
        code = (
            'i = 0\n'
            'try\n'
            '    while i < 10\n'
            '        if i == 5\n'
            '            throw "stopped at 5"\n'
            '        i = i + 1\n'
            'catch err\n'
            '    print err\n'
            'print i\n'
        )
        output = run_code(code)
        lines = output.strip().split('\n')
        assert lines[0] == "stopped at 5"
        assert lines[1] == "5"

    def test_try_catch_in_loop(self):
        code = (
            'for i 0 3\n'
            '    try\n'
            '        if i == 1\n'
            '            throw "skip"\n'
            '        print i\n'
            '    catch err\n'
            '        print f"skipped {i}"\n'
        )
        output = run_code(code)
        lines = output.strip().split('\n')
        assert lines == ["0", "skipped 1", "2"]

    def test_nested_try_catch(self):
        code = (
            'try\n'
            '    try\n'
            '        throw "inner"\n'
            '    catch e1\n'
            '        print f"inner caught: {e1}"\n'
            '        throw "outer"\n'
            'catch e2\n'
            '    print f"outer caught: {e2}"\n'
        )
        output = run_code(code)
        lines = output.strip().split('\n')
        assert lines[0] == "inner caught: inner"
        assert lines[1] == "outer caught: outer"

    def test_try_catch_no_error_skips_handler(self):
        code = 'try\n    print "ok"\ncatch err\n    print "should not run"\n'
        output = run_code(code)
        assert output.strip() == "ok"

    def test_catch_runtime_error_from_builtin(self):
        code = 'try\n    x = sqrt (0 - 1)\ncatch err\n    print "caught sqrt error"\n'
        output = run_code(code)
        assert "caught sqrt error" in output.strip()

    def test_catch_key_not_found(self):
        code = 'm = {"a": 1}\ntry\n    x = m["z"]\ncatch err\n    print "missing key"\n'
        output = run_code(code)
        assert "missing key" in output.strip()

    def test_throw_bool_value(self):
        code = 'try\n    throw true\ncatch err\n    print err\n'
        output = run_code(code)
        assert output.strip() == "True"

    def test_throw_list_value(self):
        code = 'try\n    throw [1, 2, 3]\ncatch err\n    print err\n'
        output = run_code(code)
        # List converted to string via Python str() — contains the values
        assert "1" in output.strip()
        assert "2" in output.strip()
        assert "3" in output.strip()

    def test_uncaught_throw_raises(self):
        code = 'throw "uncaught"\n'
        tokens = list(tokenize(code))
        parser = Parser(tokens)
        ast_nodes = parser.parse()
        interpreter = Interpreter()
        with pytest.raises(ThrowSignal):
            for node in ast_nodes:
                interpreter.interpret(node)

    def test_function_scope_restored_after_throw(self):
        code = (
            'x = "outer"\n'
            'function breaker\n'
            '    x = "inner"\n'
            '    throw "bang"\n'
            'try\n'
            '    breaker\n'
            'catch err\n'
            '    print x\n'
        )
        output = run_code(code)
        assert output.strip() == "outer"

    def test_multiple_try_catch_sequential(self):
        code = (
            'try\n'
            '    throw "first"\n'
            'catch e\n'
            '    print e\n'
            'try\n'
            '    throw "second"\n'
            'catch e\n'
            '    print e\n'
        )
        output = run_code(code)
        lines = output.strip().split('\n')
        assert lines == ["first", "second"]

    def test_try_catch_preserves_variables(self):
        code = (
            'x = 10\n'
            'try\n'
            '    x = 20\n'
            '    throw "err"\n'
            'catch e\n'
            '    print x\n'
        )
        output = run_code(code)
        assert output.strip() == "20"

    def test_throw_empty_string(self):
        code = 'try\n    throw ""\ncatch err\n    print f"error: [{err}]"\n'
        output = run_code(code)
        assert output.strip() == "error: []"

    def test_throw_concatenated_string(self):
        code = 'try\n    throw "err" + "or"\ncatch err\n    print err\n'
        output = run_code(code)
        assert output.strip() == "error"

    def test_catch_modulo_by_zero(self):
        code = 'try\n    x = 10 % 0\ncatch err\n    print err\n'
        output = run_code(code)
        assert "Modulo by zero" in output.strip()

    def test_try_catch_with_return_in_function(self):
        code = (
            'function safe_op x\n'
            '    try\n'
            '        if x == 0\n'
            '            throw "zero"\n'
            '        return 100 / x\n'
            '    catch err\n'
            '        return 0 - 1\n'
            'print safe_op 5\n'
            'print safe_op 0\n'
        )
        output = run_code(code)
        lines = output.strip().split('\n')
        assert lines[0] == "20"
        assert lines[1] == "-1"

    def test_error_in_catch_handler_propagates(self):
        code = (
            'try\n'
            '    try\n'
            '        throw "first"\n'
            '    catch e\n'
            '        x = 1 / 0\n'
            'catch e2\n'
            '    print f"second: {e2}"\n'
        )
        output = run_code(code)
        assert "second: Division by zero" in output.strip()

    def test_throw_in_for_loop_body(self):
        code = (
            'result = 0\n'
            'for i 1 6\n'
            '    try\n'
            '        if i == 3\n'
            '            throw "skip"\n'
            '        result = result + i\n'
            '    catch err\n'
            '        result = result + 0\n'
            'print result\n'
        )
        output = run_code(code)
        # 1 + 2 + 0 + 4 + 5 = 12
        assert output.strip() == "12"

    def test_throw_float_value(self):
        code = 'try\n    throw 3.14\ncatch err\n    print err\n'
        output = run_code(code)
        assert output.strip() == "3.14"

    def test_throw_computed_value(self):
        code = 'x = 10\ntry\n    throw x * 2 + 1\ncatch err\n    print err\n'
        output = run_code(code)
        assert output.strip() == "21"


class TestTryCatchDemo:
    """Test that try_catch_demo.srv runs successfully."""

    def test_try_catch_demo_runs(self):
        test_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "try_catch_demo.srv")
        if not os.path.isfile(test_file):
            pytest.skip("try_catch_demo.srv not found")
        with open(test_file) as f:
            code = f.read()
        output = run_code(code)
        assert "Caught error: Division by zero" in output
        assert "Validation failed: Age cannot be negative" in output
        assert "Age: 25" in output
        assert "Array error:" in output
        assert "Cannot divide by zero!" in output
        assert "10 / 5 = 2" in output
        assert "Status: recovered" in output
        assert "Error code: 404" in output


# ============================================================
# For-Each Iteration Tests
# ============================================================

class TestForEachIteration:
    """Tests for the for-each loop: for item in collection."""

    def test_foreach_list_basic(self):
        output = run_code('nums = [10, 20, 30]\nfor x in nums\n    print x\n')
        assert output.strip() == "10\n20\n30"

    def test_foreach_list_empty(self):
        output = run_code('items = []\nfor x in items\n    print x\n')
        assert output.strip() == ""

    def test_foreach_list_single_element(self):
        output = run_code('items = [42]\nfor x in items\n    print x\n')
        assert output.strip() == "42"

    def test_foreach_string_chars(self):
        output = run_code('for ch in "abc"\n    print ch\n')
        assert output.strip() == "a\nb\nc"

    def test_foreach_string_empty(self):
        output = run_code('for ch in ""\n    print ch\n')
        assert output.strip() == ""

    def test_foreach_map_keys(self):
        output = run_code('m = {"x": 1, "y": 2}\nfor k in m\n    print k\n')
        lines = output.strip().split("\n")
        assert set(lines) == {"x", "y"}

    def test_foreach_map_empty(self):
        output = run_code('m = {}\nfor k in m\n    print k\n')
        assert output.strip() == ""

    def test_foreach_accumulate_sum(self):
        output = run_code(
            'nums = [1, 2, 3, 4, 5]\n'
            'total = 0\n'
            'for n in nums\n'
            '    total = total + n\n'
            'print total\n'
        )
        assert output.strip() == "15"

    def test_foreach_with_if(self):
        output = run_code(
            'ages = [15, 22, 17, 30]\n'
            'for age in ages\n'
            '    if age >= 18\n'
            '        print age\n'
        )
        assert output.strip() == "22\n30"

    def test_foreach_nested_lists(self):
        output = run_code(
            'matrix = [[1, 2], [3, 4]]\n'
            'for row in matrix\n'
            '    for val in row\n'
            '        print val\n'
        )
        assert output.strip() == "1\n2\n3\n4"

    def test_foreach_with_function_call(self):
        output = run_code(
            'function double x\n'
            '    return x * 2\n'
            'nums = [1, 2, 3]\n'
            'for n in nums\n'
            '    print double n\n'
        )
        assert output.strip() == "2\n4\n6"

    def test_foreach_string_length(self):
        output = run_code(
            'count = 0\n'
            'for ch in "hello"\n'
            '    count = count + 1\n'
            'print count\n'
        )
        assert output.strip() == "5"

    def test_foreach_modifies_variable(self):
        output = run_code(
            'last = 0\n'
            'for x in [10, 20, 30]\n'
            '    last = x\n'
            'print last\n'
        )
        assert output.strip() == "30"

    def test_foreach_with_index_access(self):
        output = run_code(
            'pairs = [[1, 2], [3, 4], [5, 6]]\n'
            'for pair in pairs\n'
            '    print pair[0] + pair[1]\n'
        )
        assert output.strip() == "3\n7\n11"

    def test_foreach_invalid_type_raises(self):
        with pytest.raises(RuntimeError, match="Cannot iterate"):
            run_code('for x in 42\n    print x\n')

    def test_foreach_invalid_bool_raises(self):
        with pytest.raises(RuntimeError, match="Cannot iterate"):
            run_code('for x in true\n    print x\n')

    def test_foreach_preserves_outer_variable(self):
        output = run_code(
            'x = 99\n'
            'for x in [1, 2, 3]\n'
            '    print x\n'
            'print x\n'
        )
        # x is overwritten by for-each (no new scope)
        assert output.strip() == "1\n2\n3\n3"

    def test_foreach_list_of_strings(self):
        output = run_code(
            'for name in ["Alice", "Bob"]\n'
            '    print name\n'
        )
        assert output.strip() == "Alice\nBob"

    def test_foreach_with_break_via_return(self):
        output = run_code(
            'function find_first items\n'
            '    for x in items\n'
            '        if x > 5\n'
            '            return x\n'
            '    return 0\n'
            'data = [1, 3, 7, 9]\n'
            'print find_first data\n'
        )
        assert output.strip() == "7"

    def test_foreach_fstring_body(self):
        output = run_code(
            'for name in ["Alice", "Bob"]\n'
            '    print f"Hello {name}"\n'
        )
        assert output.strip() == "Hello Alice\nHello Bob"

    def test_legacy_for_range_still_works(self):
        """Ensure old range-based for loop is backward compatible."""
        output = run_code('for i 1 4\n    print i\n')
        assert output.strip() == "1\n2\n3"

    def test_foreach_with_append(self):
        output = run_code(
            'result = []\n'
            'for x in [1, 2, 3]\n'
            '    append result x * 2\n'
            'print result\n'
        )
        assert output.strip() == "[2, 4, 6]"

    def test_foreach_try_catch_in_body(self):
        output = run_code(
            'for x in [2, 0, 5]\n'
            '    try\n'
            '        print 10 / x\n'
            '    catch e\n'
            '        print "error"\n'
        )
        assert output.strip() == "5\nerror\n2"

    def test_foreach_map_access_values(self):
        output = run_code(
            'm = {"a": 10, "b": 20}\n'
            'total = 0\n'
            'for k in m\n'
            '    total = total + m[k]\n'
            'print total\n'
        )
        assert output.strip() == "30"

    def test_foreach_demo_runs(self):
        """End-to-end: foreach_demo.srv runs without error."""
        import os
        demo = os.path.join(os.path.dirname(__file__), '..', 'foreach_demo.srv')
        if os.path.isfile(demo):
            with open(demo) as f:
                code = f.read()
            output = run_code(code)
            assert "all for-each tests passed" in output


# ============================================================
# Higher-Order Function Tests (map, filter, reduce, each)
# ============================================================

class TestHigherOrderFunctions:
    """Tests for map, filter, reduce, each built-in functions."""

    # --- map ---

    def test_map_basic(self):
        output = run_code(
            'function double x\n'
            '    return x * 2\n'
            'result = map (double) [1, 2, 3]\n'
            'print result\n'
        )
        assert output.strip() == "[2, 4, 6]"

    def test_map_empty_list(self):
        output = run_code(
            'function double x\n'
            '    return x * 2\n'
            'result = map (double) []\n'
            'print result\n'
        )
        assert output.strip() == "[]"

    def test_map_single_element(self):
        output = run_code(
            'function negate x\n'
            '    return 0 - x\n'
            'result = map (negate) [5]\n'
            'print result\n'
        )
        assert output.strip() == "[-5]"

    def test_map_with_builtin(self):
        output = run_code(
            'result = map (upper) ["hello", "world"]\n'
            'print result\n'
        )
        assert output.strip() == '["HELLO", "WORLD"]'

    def test_map_squares(self):
        output = run_code(
            'function sq x\n'
            '    return x * x\n'
            'result = map (sq) [1, 2, 3, 4]\n'
            'print result\n'
        )
        assert output.strip() == "[1, 4, 9, 16]"

    def test_map_not_function_raises(self):
        with pytest.raises(RuntimeError, match="map expects a function name"):
            run_code('result = map 42 [1, 2, 3]\n')

    def test_map_not_list_raises(self):
        with pytest.raises(RuntimeError, match="map expects a list"):
            run_code(
                'function f x\n'
                '    return x\n'
                'result = map (f) "hello"\n'
            )

    def test_map_preserves_original(self):
        output = run_code(
            'function double x\n'
            '    return x * 2\n'
            'nums = [1, 2, 3]\n'
            'doubled = map (double) (nums)\n'
            'print nums\n'
            'print doubled\n'
        )
        lines = output.strip().split("\n")
        assert lines[0] == "[1, 2, 3]"
        assert lines[1] == "[2, 4, 6]"

    def test_map_chained(self):
        output = run_code(
            'function double x\n'
            '    return x * 2\n'
            'function add_one x\n'
            '    return x + 1\n'
            'step1 = map (double) [1, 2, 3]\n'
            'step2 = map (add_one) (step1)\n'
            'print step2\n'
        )
        assert output.strip() == "[3, 5, 7]"

    # --- filter ---

    def test_filter_basic(self):
        output = run_code(
            'function is_even x\n'
            '    return x % 2 == 0\n'
            'result = filter (is_even) [1, 2, 3, 4, 5, 6]\n'
            'print result\n'
        )
        assert output.strip() == "[2, 4, 6]"

    def test_filter_empty_list(self):
        output = run_code(
            'function yes x\n'
            '    return true\n'
            'result = filter (yes) []\n'
            'print result\n'
        )
        assert output.strip() == "[]"

    def test_filter_none_match(self):
        output = run_code(
            'function is_negative x\n'
            '    return x < 0\n'
            'result = filter (is_negative) [1, 2, 3]\n'
            'print result\n'
        )
        assert output.strip() == "[]"

    def test_filter_all_match(self):
        output = run_code(
            'function is_positive x\n'
            '    return x > 0\n'
            'result = filter (is_positive) [1, 2, 3]\n'
            'print result\n'
        )
        assert output.strip() == "[1, 2, 3]"

    def test_filter_strings(self):
        output = run_code(
            'function is_long x\n'
            '    return len x > 3\n'
            'result = filter (is_long) ["hi", "hello", "yo", "world"]\n'
            'print result\n'
        )
        assert output.strip() == '["hello", "world"]'

    def test_filter_not_function_raises(self):
        with pytest.raises(RuntimeError, match="filter expects a function name"):
            run_code('result = filter 42 [1, 2, 3]\n')

    def test_filter_not_list_raises(self):
        with pytest.raises(RuntimeError, match="filter expects a list"):
            run_code(
                'function f x\n'
                '    return true\n'
                'result = filter (f) 42\n'
            )

    # --- reduce ---

    def test_reduce_sum(self):
        output = run_code(
            'function add a b\n'
            '    return a + b\n'
            'result = reduce (add) [1, 2, 3, 4, 5] 0\n'
            'print result\n'
        )
        assert output.strip() == "15"

    def test_reduce_product(self):
        output = run_code(
            'function mul a b\n'
            '    return a * b\n'
            'result = reduce (mul) [1, 2, 3, 4] 1\n'
            'print result\n'
        )
        assert output.strip() == "24"

    def test_reduce_empty_list(self):
        output = run_code(
            'function add a b\n'
            '    return a + b\n'
            'result = reduce (add) [] 0\n'
            'print result\n'
        )
        assert output.strip() == "0"

    def test_reduce_single_element(self):
        output = run_code(
            'function add a b\n'
            '    return a + b\n'
            'result = reduce (add) [42] 0\n'
            'print result\n'
        )
        assert output.strip() == "42"

    def test_reduce_max(self):
        output = run_code(
            'function max_of a b\n'
            '    if b > a\n'
            '        return b\n'
            '    return a\n'
            'result = reduce (max_of) [3, 7, 2, 9, 1] 0\n'
            'print result\n'
        )
        assert output.strip() == "9"

    def test_reduce_string_concat(self):
        output = run_code(
            'function concat a b\n'
            '    return a + b\n'
            'result = reduce (concat) ["hello", " ", "world"] ""\n'
            'print result\n'
        )
        assert output.strip() == "hello world"

    def test_reduce_not_function_raises(self):
        with pytest.raises(RuntimeError, match="reduce expects a function name"):
            run_code('result = reduce 42 [1, 2] 0\n')

    def test_reduce_not_list_raises(self):
        with pytest.raises(RuntimeError, match="reduce expects a list"):
            run_code(
                'function f a b\n'
                '    return a + b\n'
                'result = reduce (f) 42 0\n'
            )

    # --- each ---

    def test_each_basic(self):
        output = run_code(
            'function show x\n'
            '    print x\n'
            '_ = each (show) [10, 20, 30]\n'
        )
        assert output.strip() == "10\n20\n30"

    def test_each_returns_original_list(self):
        output = run_code(
            'function noop x\n'
            '    x = x\n'
            'result = each (noop) [1, 2, 3]\n'
            'print result\n'
        )
        assert output.strip() == "[1, 2, 3]"

    def test_each_empty_list(self):
        output = run_code(
            'function show x\n'
            '    print x\n'
            '_ = each (show) []\n'
        )
        assert output.strip() == ""

    def test_each_not_function_raises(self):
        with pytest.raises(RuntimeError, match="each expects a function name"):
            run_code('_ = each 42 [1, 2, 3]\n')

    def test_each_not_list_raises(self):
        with pytest.raises(RuntimeError, match="each expects a list"):
            run_code(
                'function f x\n'
                '    print x\n'
                '_ = each (f) "hello"\n'
            )

    # --- Chaining higher-order functions ---

    def test_filter_then_map(self):
        output = run_code(
            'function is_even x\n'
            '    return x % 2 == 0\n'
            'function double x\n'
            '    return x * 2\n'
            'step1 = filter (is_even) [1, 2, 3, 4, 5, 6]\n'
            'step2 = map (double) (step1)\n'
            'print step2\n'
        )
        assert output.strip() == "[4, 8, 12]"

    def test_map_then_reduce(self):
        output = run_code(
            'function square x\n'
            '    return x * x\n'
            'function add a b\n'
            '    return a + b\n'
            'squares = map (square) [1, 2, 3]\n'
            'total = reduce (add) (squares) 0\n'
            'print total\n'
        )
        assert output.strip() == "14"

    def test_map_with_closureless_recursion(self):
        """map should correctly handle functions that use recursion."""
        output = run_code(
            'function factorial n\n'
            '    if n <= 1\n'
            '        return 1\n'
            '    return n * factorial (n - 1)\n'
            'result = map (factorial) [1, 2, 3, 4, 5]\n'
            'print result\n'
        )
        assert output.strip() == "[1, 2, 6, 24, 120]"

    def test_higher_order_with_foreach(self):
        """Combine for-each with higher-order functions."""
        output = run_code(
            'function double x\n'
            '    return x * 2\n'
            'nums = [1, 2, 3]\n'
            'doubled = map (double) (nums)\n'
            'for x in doubled\n'
            '    print x\n'
        )
        assert output.strip() == "2\n4\n6"

    def test_call_function_with_args_builtin(self):
        """_call_function_with_args works with builtins too."""
        output = run_code(
            'result = map (abs) [-1, -2, 3]\n'
            'print result\n'
        )
        assert output.strip() == "[1, 2, 3]"

    def test_foreach_with_inline_list(self):
        """For-each over inline list literal."""
        output = run_code(
            'for x in [10, 20, 30]\n'
            '    print x\n'
        )
        assert output.strip() == "10\n20\n30"

    def test_map_keyword_removed(self):
        """map is no longer a keyword - it is an identifier (function name)."""
        output = run_code(
            'function double x\n'
            '    return x * 2\n'
            'result = map (double) [5]\n'
            'print result\n'
        )
        assert output.strip() == "[10]"

    def test_map_literal_still_works(self):
        """Map/dict literal syntax {key: value} still works after removing map keyword."""
        output = run_code(
            'm = {"a": 1, "b": 2}\n'
            'print m["a"]\n'
        )
        assert output.strip() == "1"


# ==================== Lambda Expression Tests ====================

class TestLambdaBasics:
    """Test basic lambda expression parsing and evaluation."""

    def test_lambda_assign_and_call_via_map(self):
        """Lambda stored in variable and used with map."""
        output = run_code(
            'double = lambda x -> x * 2\n'
            'result = map (double) [1, 2, 3]\n'
            'print result\n'
        )
        assert output.strip() == "[2, 4, 6]"

    def test_lambda_inline_with_map(self):
        """Lambda passed directly inline to map."""
        output = run_code(
            'result = map (lambda x -> x * 2) [1, 2, 3]\n'
            'print result\n'
        )
        assert output.strip() == "[2, 4, 6]"

    def test_lambda_inline_with_filter(self):
        """Lambda passed directly inline to filter."""
        output = run_code(
            'result = filter (lambda x -> x > 3) [1, 2, 3, 4, 5]\n'
            'print result\n'
        )
        assert output.strip() == "[4, 5]"

    def test_lambda_inline_with_reduce(self):
        """Lambda passed directly inline to reduce."""
        output = run_code(
            'result = reduce (lambda acc x -> acc + x) [1, 2, 3, 4] 0\n'
            'print result\n'
        )
        assert output.strip() == "10"

    def test_lambda_multiply_reduce(self):
        """Lambda for multiplication in reduce."""
        output = run_code(
            'result = reduce (lambda acc x -> acc * x) [1, 2, 3, 4] 1\n'
            'print result\n'
        )
        assert output.strip() == "24"

    def test_lambda_with_each(self):
        """Lambda used with each for side effects."""
        output = run_code(
            'function show x\n'
            '    print x\n'
            '\n'
            'each (show) [10, 20, 30]\n'
        )
        assert output.strip() == "10\n20\n30"

    def test_lambda_no_params_error(self):
        """Lambda with no parameters errors when map passes an arg."""
        with pytest.raises(RuntimeError, match="expects 0 argument"):
            run_code(
                'answer = lambda -> 42\n'
                'map (answer) [1, 2, 3]\n'
            )

        """Lambda with arithmetic expression."""
        output = run_code(
            'result = map (lambda x -> x + 10) [1, 2, 3]\n'
            'print result\n'
        )
        assert output.strip() == "[11, 12, 13]"

    def test_lambda_two_params(self):
        """Lambda with two parameters for reduce."""
        output = run_code(
            'result = reduce (lambda a b -> a + b) [5, 10, 15] 0\n'
            'print result\n'
        )
        assert output.strip() == "30"

    def test_lambda_string_operations(self):
        """Lambda with string operations."""
        output = run_code(
            'result = map (lambda s -> upper s) ["hello", "world"]\n'
            'print result\n'
        )
        assert output.strip() == '["HELLO", "WORLD"]'

    def test_lambda_boolean_filter(self):
        """Lambda returning boolean for filter."""
        output = run_code(
            'result = filter (lambda x -> x > 0) [3, -1, 4, -5, 2]\n'
            'print result\n'
        )
        assert output.strip() == "[3, 4, 2]"

    def test_lambda_modulo_filter(self):
        """Lambda using modulo for even number filter."""
        output = run_code(
            'result = filter (lambda x -> x % 2 == 0) [1, 2, 3, 4, 5, 6]\n'
            'print result\n'
        )
        assert output.strip() == "[2, 4, 6]"

    def test_lambda_negate(self):
        """Lambda for negation."""
        output = run_code(
            'result = map (lambda x -> 0 - x) [1, 2, 3]\n'
            'print result\n'
        )
        assert output.strip() == "[-1, -2, -3]"


class TestLambdaClosure:
    """Test lambda closure (capturing variables from enclosing scope)."""

    def test_lambda_captures_variable(self):
        """Lambda captures a variable from its defining scope."""
        output = run_code(
            'factor = 3\n'
            'result = map (lambda x -> x * factor) [1, 2, 3]\n'
            'print result\n'
        )
        assert output.strip() == "[3, 6, 9]"

    def test_lambda_captures_at_definition_time(self):
        """Lambda captures value at definition time, not call time."""
        output = run_code(
            'n = 10\n'
            'add_n = lambda x -> x + n\n'
            'n = 999\n'
            'result = map (add_n) [1, 2, 3]\n'
            'print result\n'
        )
        assert output.strip() == "[11, 12, 13]"

    def test_lambda_captures_multiple_variables(self):
        """Lambda captures multiple variables."""
        output = run_code(
            'low = 2\n'
            'high = 4\n'
            'result = filter (lambda x -> x >= low and x <= high) [1, 2, 3, 4, 5]\n'
            'print result\n'
        )
        assert output.strip() == "[2, 3, 4]"

    def test_lambda_inside_function(self):
        """Lambda defined inside a function captures function locals."""
        output = run_code(
            'function make_adder n\n'
            '    return map (lambda x -> x + n) [1, 2, 3]\n'
            '\n'
            'result = make_adder 10\n'
            'print result\n'
        )
        assert output.strip() == "[11, 12, 13]"


class TestLambdaTypeOf:
    """Test type_of and to_string with lambda values."""

    def test_type_of_lambda(self):
        """type_of returns 'lambda' for lambda values."""
        output = run_code(
            'f = lambda x -> x\n'
            'print type_of f\n'
        )
        assert output.strip() == "lambda"

    def test_to_string_lambda(self):
        """to_string returns readable representation."""
        output = run_code(
            'f = lambda x y -> x + y\n'
            'print to_string f\n'
        )
        assert "<lambda" in output.strip()

    def test_print_lambda(self):
        """Printing a lambda value shows its representation."""
        output = run_code(
            'f = lambda x -> x * 2\n'
            'print f\n'
        )
        assert "<lambda" in output.strip()


class TestLambdaEdgeCases:
    """Test lambda edge cases and error handling."""

    def test_lambda_empty_list(self):
        """Lambda with empty list produces empty result."""
        output = run_code(
            'result = map (lambda x -> x * 2) []\n'
            'print result\n'
        )
        assert output.strip() == "[]"

    def test_lambda_single_element(self):
        """Lambda with single-element list."""
        output = run_code(
            'result = map (lambda x -> x * 10) [7]\n'
            'print result\n'
        )
        assert output.strip() == "[70]"

    def test_lambda_with_string_concat(self):
        """Lambda that concatenates strings."""
        output = run_code(
            'result = map (lambda s -> s + "!") ["hi", "bye"]\n'
            'print result\n'
        )
        assert output.strip() == '["hi!", "bye!"]'

    def test_lambda_wrong_arg_count(self):
        """Lambda called with wrong number of arguments raises error."""
        with pytest.raises(RuntimeError, match="expects 2 argument"):
            run_code(
                'add = lambda x y -> x + y\n'
                'map (add) [1, 2, 3]\n'
            )

    def test_lambda_chained_map_filter(self):
        """Chain map and filter with lambdas."""
        output = run_code(
            'doubled = map (lambda x -> x * 2) [1, 2, 3, 4, 5]\n'
            'big = filter (lambda x -> x > 5) doubled\n'
            'print big\n'
        )
        assert output.strip() == "[6, 8, 10]"

    def test_lambda_in_variable_reassignment(self):
        """Lambda stored in a variable can be reassigned."""
        output = run_code(
            'f = lambda x -> x + 1\n'
            'r1 = map (f) [1, 2]\n'
            'f = lambda x -> x * 10\n'
            'r2 = map (f) [1, 2]\n'
            'print r1\n'
            'print r2\n'
        )
        lines = output.strip().split('\n')
        assert lines[0] == "[2, 3]"
        assert lines[1] == "[10, 20]"

    def test_lambda_with_conditional(self):
        """Lambda body with comparison expression."""
        output = run_code(
            'result = filter (lambda x -> x != 3) [1, 2, 3, 4, 5]\n'
            'print result\n'
        )
        assert output.strip() == "[1, 2, 4, 5]"

    def test_lambda_with_len(self):
        """Lambda that uses len builtin."""
        output = run_code(
            'result = map (lambda s -> len s) ["hi", "hello", "hey"]\n'
            'print result\n'
        )
        assert output.strip() == "[2, 5, 3]"

    def test_lambda_preserve_named_function(self):
        """Named functions still work alongside lambdas."""
        output = run_code(
            'function triple x\n'
            '    return x * 3\n'
            '\n'
            'r1 = map (triple) [1, 2, 3]\n'
            'r2 = map (lambda x -> x * 3) [1, 2, 3]\n'
            'print r1\n'
            'print r2\n'
        )
        lines = output.strip().split('\n')
        assert lines[0] == "[3, 6, 9]"
        assert lines[1] == "[3, 6, 9]"

    def test_lambda_reduce_max(self):
        """Lambda to find maximum via reduce."""
        output = run_code(
            'function bigger a b\n'
            '    if a > b\n'
            '        return a\n'
            '    else\n'
            '        return b\n'
            '\n'
            'result = reduce (bigger) [3, 7, 2, 9, 1] 0\n'
            'print result\n'
        )
        assert output.strip() == "9"

    def test_lambda_fstring_body(self):
        """Lambda that returns an f-string."""
        output = run_code(
            'result = map (lambda x -> f"num:{x}") [1, 2, 3]\n'
            'print result\n'
        )
        assert output.strip() == '["num:1", "num:2", "num:3"]'

    def test_lambda_complex_expression(self):
        """Lambda with complex arithmetic expression."""
        output = run_code(
            'result = map (lambda x -> x * x + 2 * x + 1) [0, 1, 2, 3]\n'
            'print result\n'
        )
        # (0+1)=1, (1+2+1)=4, (4+4+1)=9, (9+6+1)=16
        assert output.strip() == "[1, 4, 9, 16]"

    def test_lambda_filter_strings(self):
        """Lambda filtering strings by length."""
        output = run_code(
            'result = filter (lambda s -> len s > 3) ["hi", "hello", "yo", "world"]\n'
            'print result\n'
        )
        assert output.strip() == '["hello", "world"]'

    def test_lambda_reduce_string_concat(self):
        """Lambda to concatenate strings via reduce."""
        output = run_code(
            'result = reduce (lambda acc s -> acc + s) ["a", "b", "c"] ""\n'
            'print result\n'
        )
        assert output.strip() == "abc"

    def test_lambda_mixed_named_and_lambda(self):
        """Mix named functions and lambdas in same program."""
        output = run_code(
            'function square x\n'
            '    return x * x\n'
            '\n'
            'nums = map (square) [1, 2, 3, 4]\n'
            'result = filter (lambda x -> x % 2 == 0) nums\n'
            'print result\n'
        )
        # squares = [1, 4, 9, 16], evens = [4, 16]
        assert output.strip() == "[4, 16]"
