"""Tests for pattern matching (match/case) feature."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from saurav import Interpreter, tokenize, Parser


def run(code):
    """Run sauravcode and capture printed output."""
    tokens = tokenize(code)
    parser = Parser(tokens)
    ast = parser.parse()
    interp = Interpreter()
    import io
    old_stdout = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        for node in ast:
            if node:
                interp.interpret(node)
    finally:
        sys.stdout = old_stdout
    return buf.getvalue().strip()


# --- Literal matching ---

def test_match_int_literal():
    assert run('x = 3\nmatch x\n    case 1\n        print "one"\n    case 3\n        print "three"') == "three"

def test_match_int_no_match():
    assert run('x = 99\nmatch x\n    case 1\n        print "one"\n    case 2\n        print "two"') == ""

def test_match_float_literal():
    assert run('x = 3.14\nmatch x\n    case 3.14\n        print "pi"\n    case _\n        print "other"') == "pi"

def test_match_string_literal():
    assert run('x = "hello"\nmatch x\n    case "hello"\n        print "hi"\n    case _\n        print "bye"') == "hi"

def test_match_string_no_match():
    assert run('x = "foo"\nmatch x\n    case "bar"\n        print "bar"\n    case "baz"\n        print "baz"') == ""

def test_match_true():
    assert run('match true\n    case true\n        print "yes"\n    case false\n        print "no"') == "yes"

def test_match_false():
    assert run('match false\n    case true\n        print "yes"\n    case false\n        print "no"') == "no"

def test_match_zero():
    assert run('match 0\n    case 0\n        print "zero"\n    case _\n        print "other"') == "zero"

def test_match_negative():
    assert run('x = 5\nmatch x\n    case 5\n        print "five"\n    case _\n        print "nope"') == "five"


# --- Wildcard ---

def test_wildcard_default():
    assert run('x = 42\nmatch x\n    case 1\n        print "one"\n    case _\n        print "default"') == "default"

def test_wildcard_only():
    assert run('match 7\n    case _\n        print "always"') == "always"

def test_wildcard_not_reached():
    assert run('match 1\n    case 1\n        print "one"\n    case _\n        print "default"') == "one"


# --- Variable binding ---

def test_binding_simple():
    assert run('match 42\n    case n\n        print n') == "42"

def test_binding_string():
    assert run('match "hello"\n    case s\n        print s') == "hello"

def test_binding_after_literal():
    assert run('match 5\n    case 1\n        print "one"\n    case n\n        print n') == "5"

def test_binding_with_fstring():
    assert run('match 42\n    case n\n        print f"got {n}"') == "got 42"


# --- Guard clauses ---

def test_guard_match():
    assert run('match 85\n    case s if s >= 90\n        print "A"\n    case s if s >= 80\n        print "B"\n    case _\n        print "F"') == "B"

def test_guard_first():
    assert run('match 95\n    case s if s >= 90\n        print "A"\n    case s if s >= 80\n        print "B"\n    case _\n        print "F"') == "A"

def test_guard_fallthrough_to_wildcard():
    assert run('match 50\n    case s if s >= 90\n        print "A"\n    case s if s >= 80\n        print "B"\n    case _\n        print "F"') == "F"

def test_guard_no_match():
    assert run('match 50\n    case s if s >= 90\n        print "A"\n    case s if s >= 80\n        print "B"') == ""

def test_guard_with_equality():
    assert run('match 10\n    case x if x == 10\n        print "ten"') == "ten"

def test_guard_with_not_equal():
    assert run('match 5\n    case x if x != 10\n        print "not ten"') == "not ten"


# --- Multiple patterns with | ---

def test_multi_pattern():
    assert run('match "Saturday"\n    case "Saturday" | "Sunday"\n        print "weekend"\n    case _\n        print "weekday"') == "weekend"

def test_multi_pattern_second():
    assert run('match "Sunday"\n    case "Saturday" | "Sunday"\n        print "weekend"\n    case _\n        print "weekday"') == "weekend"

def test_multi_pattern_no_match():
    assert run('match "Monday"\n    case "Saturday" | "Sunday"\n        print "weekend"\n    case _\n        print "weekday"') == "weekday"

def test_multi_pattern_numbers():
    assert run('match 2\n    case 1 | 2 | 3\n        print "small"\n    case _\n        print "big"') == "small"

def test_multi_pattern_three():
    assert run('match 3\n    case 1 | 2 | 3\n        print "small"\n    case _\n        print "big"') == "small"


# --- First match wins ---

def test_first_match_wins():
    assert run('match 1\n    case 1\n        print "first"\n    case 1\n        print "second"') == "first"

def test_first_match_wins_wildcard():
    assert run('match 5\n    case _\n        print "wildcard"\n    case 5\n        print "five"') == "wildcard"


# --- Match with expressions ---

def test_match_arithmetic():
    assert run('x = 2 + 1\nmatch x\n    case 3\n        print "three"') == "three"

def test_match_len():
    assert run('match len [1, 2, 3]\n    case 3\n        print "three"\n    case _\n        print "other"') == "three"

def test_match_variable():
    assert run('a = 10\nb = 10\nmatch a\n    case 10\n        print "ten"') == "ten"


# --- Nested match ---

def test_nested_match():
    code = '''x = 1
y = "a"
match x
    case 1
        match y
            case "a"
                print "1a"
            case _
                print "1other"
    case _
        print "other"'''
    assert run(code) == "1a"

def test_nested_match_outer():
    code = '''x = 2
y = "a"
match x
    case 1
        print "one"
    case 2
        match y
            case "b"
                print "2b"
            case _
                print "2default"'''
    assert run(code) == "2default"


# --- Match in functions ---

def test_match_in_function():
    code = '''function describe x
    match x
        case 1
            return "one"
        case 2
            return "two"
        case _
            return "other"
print describe 1'''
    assert run(code) == "one"

def test_match_in_function_default():
    code = '''function describe x
    match x
        case 1
            return "one"
        case _
            return "unknown"
print describe 99'''
    assert run(code) == "unknown"


# --- Match with multiple statements in body ---

def test_multi_statement_body():
    code = '''x = 2
match x
    case 2
        a = "hello"
        print a'''
    assert run(code) == "hello"


# --- Boolean matching edge cases ---

def test_bool_true_match():
    assert run('x = true\nmatch x\n    case true\n        print "t"\n    case false\n        print "f"') == "t"

def test_bool_false_match():
    assert run('x = false\nmatch x\n    case true\n        print "t"\n    case false\n        print "f"') == "f"


# --- Edge cases ---

def test_single_case():
    assert run('match 1\n    case 1\n        print "one"') == "one"

def test_no_cases_match():
    assert run('match 999\n    case 1\n        print "one"\n    case 2\n        print "two"') == ""

def test_match_empty_string():
    assert run('match ""\n    case ""\n        print "empty"\n    case _\n        print "not"') == "empty"


# --- Match + try/catch ---

def test_match_in_try():
    code = '''try
    match 1
        case 1
            print "matched"
catch e
    print e'''
    assert run(code) == "matched"


# --- Match with lists ---

def test_match_list_length():
    code = '''mylist = [1, 2, 3]
match len mylist
    case 0
        print "empty"
    case 3
        print "three"
    case _
        print "other"'''
    assert run(code) == "three"


# --- More guard tests ---

def test_guard_less_than():
    assert run('match 3\n    case x if x < 5\n        print "small"\n    case _\n        print "big"') == "small"

def test_guard_lte():
    assert run('match 5\n    case x if x <= 5\n        print "ok"\n    case _\n        print "no"') == "ok"

def test_guard_gte():
    assert run('match 10\n    case x if x >= 10\n        print "yes"\n    case _\n        print "no"') == "yes"

def test_guard_multiple_cases():
    code = '''match 15
    case x if x > 20
        print "high"
    case x if x > 10
        print "medium"
    case x if x > 0
        print "low"
    case _
        print "zero"'''
    assert run(code) == "medium"


# --- Variable binding used in body ---

def test_binding_used_in_computation():
    code = '''match 10
    case n
        result = n + 5
        print result'''
    assert run(code) == "15"

def test_binding_with_guard_used():
    code = '''match 25
    case x if x > 20
        y = x + 1
        print y'''
    assert run(code) == "26"
