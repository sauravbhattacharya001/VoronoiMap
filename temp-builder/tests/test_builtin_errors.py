"""
Tests for builtin function error paths in sauravcode interpreter.

Covers type validation, argument count, and boundary errors for all
builtin string, math, and utility functions.
"""

import pytest
import sys
import os
import io
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from saurav import tokenize, Parser, Interpreter, FunctionNode, FunctionCallNode


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
                interpreter.execute_function(node)
            else:
                interpreter.interpret(node)
    return buf.getvalue()


# ================================================================
# String builtin error paths
# ================================================================

class TestUpperErrors:
    def test_upper_with_number(self):
        with pytest.raises(RuntimeError, match="upper expects a string"):
            run_code('x = upper 42\n')

    def test_upper_with_list(self):
        with pytest.raises(RuntimeError, match="upper expects a string"):
            run_code('xs = [1]\nprint upper xs\n')

    def test_upper_with_bool(self):
        with pytest.raises(RuntimeError, match="upper expects a string"):
            run_code('x = upper true\n')


class TestLowerErrors:
    def test_lower_with_number(self):
        with pytest.raises(RuntimeError, match="lower expects a string"):
            run_code('x = lower 42\n')

    def test_lower_with_list(self):
        with pytest.raises(RuntimeError, match="lower expects a string"):
            run_code('xs = [1]\nprint lower xs\n')


class TestTrimErrors:
    def test_trim_with_number(self):
        with pytest.raises(RuntimeError, match="trim expects a string"):
            run_code('x = trim 42\n')

    def test_trim_with_list(self):
        with pytest.raises(RuntimeError, match="trim expects a string"):
            run_code('xs = [1]\nprint trim xs\n')


class TestReplaceErrors:
    def test_replace_with_number_first_arg(self):
        with pytest.raises(RuntimeError, match="replace expects a string"):
            run_code('x = replace 42 "a" "b"\n')


class TestSplitErrors:
    def test_split_with_number(self):
        with pytest.raises(RuntimeError, match="split expects a string"):
            run_code('x = split 42 ","\n')


class TestJoinErrors:
    def test_join_with_string_second_arg(self):
        with pytest.raises(RuntimeError, match="join expects a list"):
            run_code('x = join ", " "hello"\n')

    def test_join_with_number_second_arg(self):
        with pytest.raises(RuntimeError, match="join expects a list"):
            run_code('x = join ", " 42\n')


class TestContainsErrors:
    def test_contains_with_number(self):
        with pytest.raises(RuntimeError, match="contains expects a string"):
            run_code('x = contains 42 "a"\n')

    def test_contains_with_bool(self):
        with pytest.raises(RuntimeError, match="contains expects a string"):
            run_code('x = contains true "a"\n')


class TestStartsWithErrors:
    def test_starts_with_with_number(self):
        with pytest.raises(RuntimeError, match="starts_with expects a string"):
            run_code('x = starts_with 42 "a"\n')


class TestEndsWithErrors:
    def test_ends_with_with_number(self):
        with pytest.raises(RuntimeError, match="ends_with expects a string"):
            run_code('x = ends_with 42 "a"\n')


class TestSubstringErrors:
    def test_substring_with_number(self):
        with pytest.raises(RuntimeError, match="substring expects a string"):
            run_code('x = substring 42 0 2\n')


class TestIndexOfErrors:
    def test_index_of_with_number(self):
        with pytest.raises(RuntimeError, match="index_of expects a string"):
            run_code('x = index_of 42 "a"\n')


class TestCharAtErrors:
    def test_char_at_with_number(self):
        with pytest.raises(RuntimeError, match="char_at expects a string"):
            run_code('x = char_at 42 0\n')

    def test_char_at_out_of_bounds(self):
        with pytest.raises(RuntimeError, match="char_at index .* out of bounds"):
            run_code('x = char_at "abc" 5\n')

    def test_char_at_negative_out_of_bounds(self):
        with pytest.raises(RuntimeError, match="char_at index .* out of bounds"):
            run_code('n = 0 - 5\nx = char_at "abc" n\n')


# ================================================================
# Math builtin error paths
# ================================================================

class TestSqrtErrors:
    def test_sqrt_negative(self):
        with pytest.raises(RuntimeError, match="sqrt of negative"):
            run_code('n = 0 - 1\nprint sqrt n\n')

    def test_sqrt_negative_large(self):
        with pytest.raises(RuntimeError, match="sqrt of negative"):
            run_code('n = 0 - 100\nprint sqrt n\n')


class TestRoundErrors:
    def test_round_too_many_args(self):
        with pytest.raises(RuntimeError, match="round expects 1 or 2"):
            run_code('x = round 3.14 2 5\n')


class TestAbsErrors:
    def test_abs_with_string(self):
        with pytest.raises((RuntimeError, TypeError)):
            run_code('x = abs "hello"\n')

    def test_abs_with_list(self):
        with pytest.raises((RuntimeError, TypeError, SyntaxError)):
            run_code('xs = [1]\nprint abs xs\n')


class TestPowerErrors:
    def test_power_wrong_arg_count(self):
        with pytest.raises(RuntimeError, match="power expects 2"):
            run_code('x = power 2\n')


# ================================================================
# Utility builtin error paths
# ================================================================

class TestReverseErrors:
    def test_reverse_with_number(self):
        with pytest.raises(RuntimeError, match="reverse expects a list or string"):
            run_code('x = reverse 42\n')

    def test_reverse_with_bool(self):
        with pytest.raises(RuntimeError, match="reverse expects a list or string"):
            run_code('x = reverse true\n')


class TestSortErrors:
    def test_sort_with_number(self):
        with pytest.raises(RuntimeError, match="sort expects a list"):
            run_code('x = sort 42\n')

    def test_sort_with_string(self):
        with pytest.raises(RuntimeError, match="sort expects a list"):
            run_code('x = sort "abc"\n')


class TestRangeErrors:
    def test_range_too_many_args(self):
        with pytest.raises(RuntimeError, match="range expects 1-3"):
            run_code('x = range 1 10 2 5\n')


class TestToNumberErrors:
    def test_to_number_non_numeric_string(self):
        with pytest.raises(RuntimeError, match="Cannot convert"):
            run_code('x = to_number "abc"\n')


# ================================================================
# Successful edge cases (not errors, but untested boundaries)
# ================================================================

class TestBuiltinEdgeCases:
    def test_sqrt_zero(self):
        output = run_code('print sqrt 0\n')
        assert output.strip() in ("0", "0.0")

    def test_sqrt_one(self):
        output = run_code('print sqrt 1\n')
        assert output.strip() in ("1", "1.0")

    def test_sqrt_four(self):
        output = run_code('print sqrt 4\n')
        assert output.strip() in ("2", "2.0")

    def test_abs_zero(self):
        output = run_code('print abs 0\n')
        assert output.strip() == "0"

    def test_abs_negative(self):
        output = run_code('n = 0 - 5\nprint abs n\n')
        assert output.strip() == "5"

    def test_round_no_places(self):
        output = run_code('print round 3.7\n')
        assert output.strip() == "4"

    def test_round_to_places(self):
        output = run_code('print round 3.14159 2\n')
        assert output.strip() == "3.14"

    def test_reverse_string(self):
        output = run_code('print reverse "hello"\n')
        assert output.strip() == "olleh"

    def test_reverse_list(self):
        output = run_code('xs = [1, 2, 3]\nprint reverse xs\n')
        assert output.strip() == "[3, 2, 1]"

    def test_reverse_empty_string(self):
        output = run_code('print reverse ""\n')
        assert output.strip() == ""

    def test_sort_list(self):
        output = run_code('xs = [3, 1, 2]\nprint sort xs\n')
        assert output.strip() == "[1, 2, 3]"

    def test_sort_single_element(self):
        output = run_code('xs = [42]\nprint sort xs\n')
        assert output.strip() == "[42]"

    def test_range_single_arg(self):
        output = run_code('print range 3\n')
        assert output.strip() == "[0, 1, 2]"

    def test_range_two_args(self):
        output = run_code('print range 1 4\n')
        assert output.strip() == "[1, 2, 3]"

    def test_range_with_step(self):
        output = run_code('print range 0 10 3\n')
        assert output.strip() == "[0, 3, 6, 9]"

    def test_range_zero(self):
        output = run_code('print range 0\n')
        assert output.strip() == "[]"

    def test_char_at_last(self):
        output = run_code('print char_at "hello" 4\n')
        assert output.strip() == "o"

    def test_char_at_first(self):
        output = run_code('print char_at "hello" 0\n')
        assert output.strip() == "h"

    def test_index_of_not_found(self):
        output = run_code('print index_of "hello" "xyz"\n')
        assert output.strip() == "-1"

    def test_index_of_found(self):
        output = run_code('print index_of "hello" "ell"\n')
        assert output.strip() == "1"

    def test_substring_basic(self):
        output = run_code('print substring "hello" 1 3\n')
        assert output.strip() == "el"

    def test_contains_string_true(self):
        output = run_code('print contains "hello world" "world"\n')
        assert output.strip() == "true"

    def test_contains_string_false(self):
        output = run_code('print contains "hello" "xyz"\n')
        assert output.strip() == "false"

    def test_contains_string_empty(self):
        output = run_code('print contains "hello" ""\n')
        assert output.strip() == "true"

    def test_join_list(self):
        output = run_code('xs = ["a", "b", "c"]\nprint join ", " xs\n')
        assert output.strip() == "a, b, c"

    def test_join_empty_list(self):
        output = run_code('xs = []\nprint join ", " xs\n')
        assert output.strip() == ""

    def test_split_basic(self):
        output = run_code('print split "a,b,c" ","\n')
        assert output.strip() == '["a", "b", "c"]'

    def test_to_number_float_string(self):
        output = run_code('print to_number "3.14"\n')
        assert output.strip() == "3.14"

    def test_to_number_int_string(self):
        output = run_code('print to_number "42"\n')
        assert output.strip() == "42"

    def test_to_string_number(self):
        output = run_code('print to_string 42\n')
        assert output.strip() == "42"

    def test_to_string_bool(self):
        output = run_code('print to_string true\n')
        assert output.strip() == "true"

    def test_type_of_string(self):
        output = run_code('print type_of "hello"\n')
        assert output.strip() == "string"

    def test_type_of_number(self):
        output = run_code('print type_of 42\n')
        assert output.strip() == "number"

    def test_type_of_bool(self):
        output = run_code('print type_of true\n')
        assert output.strip() == "bool"

    def test_type_of_list(self):
        output = run_code('xs = [1, 2]\nprint type_of xs\n')
        assert output.strip() == "list"

    def test_power_basic(self):
        output = run_code('print power 2 10\n')
        assert output.strip() == "1024"

    def test_power_zero_exponent(self):
        output = run_code('print power 5 0\n')
        assert output.strip() == "1"

    def test_floor_basic(self):
        output = run_code('print floor 3.7\n')
        assert output.strip() == "3"

    def test_ceil_basic(self):
        output = run_code('print ceil 3.2\n')
        assert output.strip() == "4"

    def test_floor_negative(self):
        output = run_code('n = 0 - 1.5\nprint floor n\n')
        assert output.strip() == "-2"

    def test_ceil_negative(self):
        output = run_code('n = 0 - 1.5\nprint ceil n\n')
        assert output.strip() == "-1"

    def test_upper_basic(self):
        output = run_code('print upper "hello"\n')
        assert output.strip() == "HELLO"

    def test_lower_basic(self):
        output = run_code('print lower "HELLO"\n')
        assert output.strip() == "hello"

    def test_trim_basic(self):
        output = run_code('print trim "  hello  "\n')
        assert output.strip() == "hello"

    def test_replace_basic(self):
        output = run_code('print replace "hello" "l" "r"\n')
        assert output.strip() == "herro"

    def test_starts_with_true(self):
        output = run_code('print starts_with "hello" "hel"\n')
        assert output.strip() == "true"

    def test_starts_with_false(self):
        output = run_code('print starts_with "hello" "xyz"\n')
        assert output.strip() == "false"

    def test_ends_with_true(self):
        output = run_code('print ends_with "hello" "llo"\n')
        assert output.strip() == "true"

    def test_ends_with_false(self):
        output = run_code('print ends_with "hello" "xyz"\n')
        assert output.strip() == "false"
