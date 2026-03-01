"""Tests for the pipe operator (|>) in sauravcode.

The pipe operator passes a value as the LAST argument to the function
on the right side, enabling functional composition:

    5 |> double                     → double(5) → 10
    [1, 2, 3] |> map (lambda x -> x * 2)  → map(lambda, [1,2,3]) → [2, 4, 6]
    " hello " |> trim |> upper     → "HELLO"
"""
import pytest
import sys
import os
import io
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from saurav import (
    tokenize, Parser, Interpreter, PipeNode,
    FunctionNode, FunctionCallNode, AssignmentNode
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
    full = f"result = {code}\n"
    interp = run(full)
    return interp.variables["result"]


def run_program(code):
    """Helper: run a full program and return (interpreter, stdout)."""
    interp = run(code)
    return interp, interp._stdout


# ── Tokenizer tests ──────────────────────────────────────────────


class TestPipeTokenizer:
    def test_pipe_token(self):
        """'|>' tokenizes as a PIPE token."""
        tokens = list(tokenize("a |> b\n"))
        types = [t[0] for t in tokens if t[0] not in ('SKIP', 'NEWLINE', 'EOF')]
        assert 'PIPE' in types

    def test_pipe_doesnt_interfere(self):
        """PIPE token doesn't interfere with other tokens."""
        tokens = list(tokenize("x = 5 |> double\n"))
        types = [t[0] for t in tokens if t[0] not in ('SKIP', 'NEWLINE', 'EOF')]
        assert types == ['IDENT', 'ASSIGN', 'NUMBER', 'PIPE', 'IDENT']

    def test_chained_pipe_tokens(self):
        """Multiple |> produce correct token sequence."""
        tokens = list(tokenize("a |> b |> c\n"))
        types = [t[0] for t in tokens if t[0] not in ('SKIP', 'NEWLINE', 'EOF')]
        assert types == ['IDENT', 'PIPE', 'IDENT', 'PIPE', 'IDENT']


# ── Basic pipe tests ─────────────────────────────────────────────


class TestBasicPipe:
    def test_pipe_to_user_function(self):
        """5 |> double → double(5) → 10"""
        code = """function double x
    return x * 2

result = 5 |> double
"""
        interp = run(code)
        assert interp.variables["result"] == 10

    def test_pipe_to_upper(self):
        """'hello' |> upper → 'HELLO'"""
        result = evaluate_expr('"hello" |> upper')
        assert result == "HELLO"

    def test_pipe_to_trim(self):
        """'  hello  ' |> trim → 'hello'"""
        result = evaluate_expr('"  hello  " |> trim')
        assert result == "hello"

    def test_pipe_to_sort(self):
        """[3, 1, 2] |> sort → [1, 2, 3]"""
        result = evaluate_expr("[3, 1, 2] |> sort")
        assert result == [1.0, 2.0, 3.0]

    def test_pipe_to_reverse(self):
        """[1, 2, 3] |> reverse → [3, 2, 1]"""
        result = evaluate_expr("[1, 2, 3] |> reverse")
        assert result == [3.0, 2.0, 1.0]

    def test_pipe_to_lower(self):
        """'HELLO' |> lower → 'hello'"""
        result = evaluate_expr('"HELLO" |> lower')
        assert result == "hello"


# ── Pipe with lambdas ────────────────────────────────────────────


class TestPipeWithLambda:
    def test_pipe_lambda_multiply(self):
        """5 |> lambda x -> x * 2 → 10"""
        result = evaluate_expr("5 |> lambda x -> x * 2")
        assert result == 10

    def test_pipe_lambda_add(self):
        """10 |> lambda x -> x + 5 → 15"""
        result = evaluate_expr("10 |> lambda x -> x + 5")
        assert result == 15

    def test_pipe_lambda_subtract(self):
        """20 |> lambda x -> x - 3 → 17"""
        result = evaluate_expr("20 |> lambda x -> x - 3")
        assert result == 17

    def test_pipe_lambda_square(self):
        """4 |> lambda x -> x * x → 16"""
        result = evaluate_expr("4 |> lambda x -> x * x")
        assert result == 16

    def test_pipe_lambda_negate(self):
        """5 |> lambda x -> 0 - x → -5"""
        result = evaluate_expr("5 |> lambda x -> 0 - x")
        assert result == -5


# ── Pipe chaining ────────────────────────────────────────────────


class TestPipeChaining:
    def test_chain_trim_upper(self):
        """'  Hello  ' |> trim |> upper → 'HELLO'"""
        result = evaluate_expr('"  Hello  " |> trim |> upper')
        assert result == "HELLO"

    def test_chain_trim_lower(self):
        """'  Hello  ' |> trim |> lower → 'hello'"""
        result = evaluate_expr('"  Hello  " |> trim |> lower')
        assert result == "hello"

    def test_chain_sort_reverse(self):
        """[3, 1, 2] |> sort |> reverse → [3, 2, 1]"""
        result = evaluate_expr("[3, 1, 2] |> sort |> reverse")
        assert result == [3.0, 2.0, 1.0]

    def test_chain_lambdas(self):
        """5 |> lambda x -> x + 1 |> lambda y -> y * 2 → 12
        Left-associative: (5 |> λ1) |> λ2 = 6 |> λ2 = 12"""
        result = evaluate_expr("5 |> lambda x -> x + 1 |> lambda y -> y * 2")
        assert result == 12

    def test_chain_three_lambdas(self):
        """2 |> λ1 |> λ2 |> λ3 → ((2+3)*2)-1 = 9"""
        result = evaluate_expr("2 |> lambda x -> x + 3 |> lambda x -> x * 2 |> lambda x -> x - 1")
        assert result == 9

    def test_chain_user_functions(self):
        """3 |> add_one |> square → 16"""
        code = """function add_one x
    return x + 1

function square x
    return x * x

result = 3 |> add_one |> square
"""
        interp = run(code)
        assert interp.variables["result"] == 16


# ── Pipe with higher-order functions ─────────────────────────────


class TestPipeHigherOrder:
    def test_pipe_map(self):
        """[1, 2, 3] |> map (lambda x -> x * 2) → [2, 4, 6]"""
        result = evaluate_expr("[1, 2, 3] |> map (lambda x -> x * 2)")
        assert result == [2.0, 4.0, 6.0]

    def test_pipe_filter(self):
        """[1, 2, 3, 4, 5] |> filter (lambda x -> x > 3) → [4, 5]"""
        result = evaluate_expr("[1, 2, 3, 4, 5] |> filter (lambda x -> x > 3)")
        assert result == [4.0, 5.0]

    def test_pipe_map_then_filter(self):
        """[1, 2, 3] |> map (λ) |> filter (λ)"""
        result = evaluate_expr("[1, 2, 3] |> map (lambda x -> x * 10) |> filter (lambda x -> x > 15)")
        assert result == [20.0, 30.0]

    def test_pipe_filter_then_map(self):
        """[1,2,3,4,5] |> filter |> map"""
        result = evaluate_expr("[1, 2, 3, 4, 5] |> filter (lambda x -> x > 2) |> map (lambda x -> x * 10)")
        assert result == [30.0, 40.0, 50.0]

    def test_pipe_range_map(self):
        """range 1 4 |> map (lambda x -> x * x) → [1, 4, 9]"""
        result = evaluate_expr("range 1 4 |> map (lambda x -> x * x)")
        assert result == [1.0, 4.0, 9.0]

    def test_pipe_range_reverse_map(self):
        """range 1 4 |> reverse |> map (λ) → [13, 12, 11]"""
        result = evaluate_expr("range 1 4 |> reverse |> map (lambda x -> x + 10)")
        assert result == [13.0, 12.0, 11.0]


# ── Pipe with user-defined functions ─────────────────────────────


class TestPipeUserFunctions:
    def test_pipe_single_user_fn(self):
        """5 |> add_ten → 15"""
        code = """function add_ten x
    return x + 10

result = 5 |> add_ten
"""
        interp = run(code)
        assert interp.variables["result"] == 15

    def test_pipe_chain_user_fns(self):
        """3 |> increment |> double → 8"""
        code = """function increment x
    return x + 1

function double x
    return x * 2

result = 3 |> increment |> double
"""
        interp = run(code)
        assert interp.variables["result"] == 8

    def test_pipe_user_fn_string(self):
        """User function on string via pipe"""
        code = """function exclaim s
    return s + "!"

result = "hello" |> exclaim
"""
        interp = run(code)
        assert interp.variables["result"] == "hello!"

    def test_pipe_user_fn_list(self):
        """User function on list via pipe"""
        code = """function first lst
    return lst[0]

result = [10, 20, 30] |> first
"""
        interp = run(code)
        assert interp.variables["result"] == 10

    def test_pipe_user_fn_with_extra_args(self):
        """User fn with extra args: piped value as last arg."""
        code = """function add_to_each n lst
    return map (lambda x -> x + n) lst

result = [1, 2, 3] |> add_to_each 10
"""
        interp = run(code)
        assert interp.variables["result"] == [11.0, 12.0, 13.0]


# ── Pipe with variables ──────────────────────────────────────────


class TestPipeVariables:
    def test_pipe_result_in_variable(self):
        """Pipe result stored in variable."""
        result = evaluate_expr('"hello" |> upper')
        assert result == "HELLO"

    def test_variable_piped(self):
        """Variable piped to function."""
        code = """x = "hello"
result = x |> upper
"""
        interp = run(code)
        assert interp.variables["result"] == "HELLO"

    def test_chained_pipe_in_variable(self):
        """Chained pipe result in variable."""
        code = """x = [3, 1, 2]
result = x |> sort |> reverse
"""
        interp = run(code)
        assert interp.variables["result"] == [3.0, 2.0, 1.0]

    def test_pipe_to_lambda_variable(self):
        """Pipe to a lambda stored in a variable."""
        code = """doubler = lambda x -> x * 2
result = 5 |> doubler
"""
        interp = run(code)
        assert interp.variables["result"] == 10


# ── Pipe in control flow ─────────────────────────────────────────


class TestPipeControlFlow:
    def test_pipe_in_if_condition(self):
        """Pipe expression in if condition (via lambda returning bool)."""
        code = """x = 5 |> lambda v -> v > 3
if x
    result = "yes"
else
    result = "no"
"""
        interp = run(code)
        assert interp.variables["result"] == "yes"

    def test_pipe_in_assignment_in_loop(self):
        """Pipe used inside a for loop."""
        code = """results = []
for i in [1, 2, 3]
    v = i |> lambda x -> x * 10
    append results v
result = results
"""
        interp = run(code)
        assert interp.variables["result"] == [10.0, 20.0, 30.0]

    def test_pipe_with_print(self):
        """Pipe result printed correctly."""
        code = """print "hello" |> upper
"""
        interp, stdout = run_program(code)
        assert stdout.strip() == "HELLO"

    def test_pipe_chain_with_print(self):
        """Chained pipe result printed correctly."""
        code = """print "  Hello  " |> trim |> lower
"""
        interp, stdout = run_program(code)
        assert stdout.strip() == "hello"


# ── Pipe edge cases ──────────────────────────────────────────────


class TestPipeEdgeCases:
    def test_pipe_number_literal(self):
        """Pipe with number literal."""
        result = evaluate_expr("42 |> lambda x -> x + 8")
        assert result == 50

    def test_pipe_string_literal(self):
        """Pipe with string literal."""
        result = evaluate_expr('"abc" |> upper')
        assert result == "ABC"

    def test_pipe_empty_list(self):
        """Pipe with empty list."""
        result = evaluate_expr("[] |> reverse")
        assert result == []

    def test_pipe_single_element_list(self):
        """Pipe with single-element list."""
        result = evaluate_expr("[42] |> reverse")
        assert result == [42.0]

    def test_pipe_to_non_function_error(self):
        """Pipe to a non-function raises error."""
        with pytest.raises(RuntimeError):
            code = """x = 5
result = 10 |> x
"""
            run(code)

    def test_pipe_preserves_types(self):
        """Pipe preserves value types correctly."""
        result = evaluate_expr('[1, 2, 3] |> sort')
        assert isinstance(result, list)

    def test_pipe_variable_list_chain(self):
        """Chain pipe on a variable list."""
        code = """x = [5, 3, 1, 4, 2]
result = x |> sort |> reverse
"""
        interp = run(code)
        assert interp.variables["result"] == [5.0, 4.0, 3.0, 2.0, 1.0]


# ── Pipe with builtins ───────────────────────────────────────────


class TestPipeBuiltins:
    def test_pipe_to_abs(self):
        """(0 - 5) |> abs → 5"""
        result = evaluate_expr("(0 - 5) |> abs")
        assert result == 5

    def test_pipe_to_join(self):
        """Pipe list to join (piped value is LAST = list)."""
        result = evaluate_expr('["a", "b", "c"] |> join ","')
        assert result == "a,b,c"

    def test_pipe_to_reduce_via_lambda(self):
        """Pipe list to reduce via wrapping lambda (reduce arg order doesn't match pipe)."""
        result = evaluate_expr("[1, 2, 3, 4] |> lambda lst -> reduce (lambda a b -> a + b) (lst) 0")
        assert result == 10

    def test_pipe_to_sort_builtin(self):
        """Pipe list to sort builtin."""
        result = evaluate_expr("[5, 2, 8, 1] |> sort")
        assert result == [1.0, 2.0, 5.0, 8.0]

    def test_pipe_to_keys(self):
        """Pipe map to keys."""
        code = """m = {"a": 1, "b": 2}
result = m |> keys
"""
        interp = run(code)
        assert set(interp.variables["result"]) == {"a", "b"}


# ── AST tests ────────────────────────────────────────────────────


class TestPipeAST:
    def test_pipe_creates_pipe_node(self):
        """Parser creates PipeNode for pipe expressions."""
        tokens = list(tokenize("x = a |> b\n"))
        parser = Parser(tokens)
        ast_nodes = parser.parse()
        assign_node = ast_nodes[0]
        assert isinstance(assign_node, AssignmentNode)
        assert isinstance(assign_node.expression, PipeNode)

    def test_chained_pipe_left_associative(self):
        """a |> b |> c creates nested PipeNode (left-associative)."""
        tokens = list(tokenize("x = a |> b |> c\n"))
        parser = Parser(tokens)
        ast_nodes = parser.parse()
        assign_node = ast_nodes[0]
        pipe_outer = assign_node.expression
        assert isinstance(pipe_outer, PipeNode)
        # outer.value should be PipeNode(a, b) (inner pipe)
        pipe_inner = pipe_outer.value
        assert isinstance(pipe_inner, PipeNode)

    def test_pipe_repr(self):
        """PipeNode has meaningful repr."""
        tokens = list(tokenize("x = a |> b\n"))
        parser = Parser(tokens)
        ast_nodes = parser.parse()
        pipe = ast_nodes[0].expression
        s = repr(pipe)
        assert "PipeNode" in s


# ── Complex integration tests ────────────────────────────────────


class TestPipeIntegration:
    def test_full_pipeline(self):
        """Full functional pipeline: range → map → filter."""
        result = evaluate_expr("range 1 6 |> map (lambda x -> x * x) |> filter (lambda x -> x > 10)")
        assert result == [16.0, 25.0]

    def test_pipeline_with_user_fn_and_builtins(self):
        """Mix user functions and builtins in a pipeline."""
        code = """function double x
    return x * 2

result = "  hello  " |> trim |> upper |> lambda s -> s + "!"
"""
        interp = run(code)
        assert interp.variables["result"] == "HELLO!"

    def test_pipe_in_list_literal(self):
        """Pipe used in variable then used in list."""
        code = """x = 5 |> lambda v -> v * 2
result = [x, 20, 30]
"""
        interp = run(code)
        assert interp.variables["result"] == [10.0, 20.0, 30.0]

    def test_pipe_with_map_and_sort(self):
        """Pipe chain with map and sort."""
        result = evaluate_expr("[3, 1, 2] |> map (lambda x -> x * 10) |> sort")
        assert result == [10.0, 20.0, 30.0]

    def test_pipe_reverse_chain(self):
        """Pipe chain with map and reverse."""
        code = """result = [10, 20, 30] |> map (lambda x -> x + 1) |> reverse
"""
        interp = run(code)
        assert interp.variables["result"] == [31.0, 21.0, 11.0]

    def test_pipe_string_pipeline(self):
        """String processing pipeline."""
        result = evaluate_expr('"  Hello World  " |> trim |> lower')
        assert result == "hello world"

    def test_pipe_string_pipeline_upper(self):
        """String processing pipeline with upper."""
        result = evaluate_expr('"  Hello World  " |> trim |> upper')
        assert result == "HELLO WORLD"
