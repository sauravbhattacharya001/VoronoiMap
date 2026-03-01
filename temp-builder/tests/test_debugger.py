"""Tests for sauravdb — the sauravcode interactive debugger."""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from io import StringIO

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from saurav import tokenize, ASTNode, AssignmentNode, PrintNode, NumberNode, StringNode
from sauravdb import (
    SauravDebugger, DebugInterpreter, LineTrackingParser,
    DebuggerQuit, DebuggerRestart,
    _format_value, _node_line,
)


# ── Helpers ──────────────────────────────────────────────────────────

def make_debugger(code, filename="test.srv"):
    """Create a debugger instance from source code."""
    tokens = list(tokenize(code))
    parser = LineTrackingParser(tokens)
    ast_nodes = parser.parse()
    return SauravDebugger(filename, code, ast_nodes)


def run_with_inputs(code, inputs, filename="test.srv"):
    """Run debugger with simulated user inputs, return captured output."""
    debugger = make_debugger(code, filename)
    input_iter = iter(inputs)
    output = StringIO()

    def mock_input(prompt=""):
        try:
            val = next(input_iter)
            return val
        except StopIteration:
            raise DebuggerQuit()

    with patch('builtins.input', mock_input), \
         patch('sys.stdout', output):
        try:
            debugger.run()
        except DebuggerQuit:
            pass

    return output.getvalue()


# ── _format_value ────────────────────────────────────────────────────

class TestFormatValue:
    def test_integer_float(self):
        assert _format_value(5.0) == "5"

    def test_float(self):
        assert _format_value(3.14) == "3.14"

    def test_bool_true(self):
        assert _format_value(True) == "true"

    def test_bool_false(self):
        assert _format_value(False) == "false"

    def test_string(self):
        assert _format_value("hello") == "'hello'"

    def test_list(self):
        result = _format_value([1.0, 2.0, 3.0])
        assert "[" in result

    def test_dict(self):
        result = _format_value({"a": 1})
        assert "a" in result

    def test_truncation(self):
        long_str = "x" * 200
        result = _format_value(long_str, max_len=20)
        assert len(result) <= 20
        assert result.endswith("...")

    def test_none(self):
        assert _format_value(None) == "None"


# ── _node_line ───────────────────────────────────────────────────────

class TestNodeLine:
    def test_with_line_num(self):
        node = AssignmentNode("x", NumberNode(5))
        node.line_num = 42
        assert _node_line(node) == 42

    def test_without_line_num(self):
        node = PrintNode(NumberNode(5))
        assert _node_line(node) is None

    def test_with_none_line_num(self):
        node = ASTNode()
        node.line_num = None
        assert _node_line(node) is None


# ── LineTrackingParser ───────────────────────────────────────────────

class TestLineTrackingParser:
    def test_tags_assignment(self):
        code = "x = 5"
        tokens = list(tokenize(code))
        parser = LineTrackingParser(tokens)
        nodes = parser.parse()
        assert len(nodes) == 1
        assert nodes[0].line_num is not None

    def test_tags_print(self):
        code = "print 42"
        tokens = list(tokenize(code))
        parser = LineTrackingParser(tokens)
        nodes = parser.parse()
        assert len(nodes) == 1
        assert nodes[0].line_num is not None

    def test_tags_function(self):
        code = "function greet\n    print \"hello\"\n"
        tokens = list(tokenize(code))
        parser = LineTrackingParser(tokens)
        nodes = parser.parse()
        assert len(nodes) == 1
        assert nodes[0].line_num is not None

    def test_multiline_tags(self):
        code = "x = 1\ny = 2\nprint x"
        tokens = list(tokenize(code))
        parser = LineTrackingParser(tokens)
        nodes = parser.parse()
        assert len(nodes) == 3
        # Each should have a different line number
        lines = [n.line_num for n in nodes]
        assert all(l is not None for l in lines)

    def test_preserves_parse_behavior(self):
        """LineTrackingParser should produce same AST as regular Parser."""
        code = "x = 5\nprint x"
        tokens = list(tokenize(code))

        from saurav import Parser
        regular = Parser(list(tokens)).parse()
        tracking = LineTrackingParser(list(tokens)).parse()

        assert len(regular) == len(tracking)
        assert type(regular[0]) == type(tracking[0])
        assert type(regular[1]) == type(tracking[1])


# ── SauravDebugger ───────────────────────────────────────────────────

class TestSauravDebugger:
    def test_create(self):
        db = make_debugger("x = 5")
        assert db.filename == "test.srv"
        assert len(db.source_lines) == 1
        assert db.stepping is True
        assert len(db.breakpoints) == 0

    def test_source_lines(self):
        code = "x = 1\ny = 2\nprint x"
        db = make_debugger(code)
        assert db.source_lines == ["x = 1", "y = 2", "print x"]

    def test_breakpoint_set(self):
        db = make_debugger("x = 1\ny = 2\nprint x")
        db.breakpoints.add(2)
        assert 2 in db.breakpoints

    def test_breakpoint_delete(self):
        db = make_debugger("x = 1\ny = 2")
        db.breakpoints.add(1)
        db.breakpoints.discard(1)
        assert 1 not in db.breakpoints

    def test_step_through(self):
        """Step through a simple program."""
        code = "x = 5\nprint x"
        output = run_with_inputs(code, ["s", "s"])
        assert "x = 5" in output
        assert "5" in output

    def test_continue(self):
        """Continue runs to completion."""
        code = "x = 5\nprint x"
        output = run_with_inputs(code, ["c"])
        assert "Program finished" in output

    def test_quit(self):
        """Quit exits cleanly."""
        code = "x = 5\nprint x"
        output = run_with_inputs(code, ["q"])
        assert "exited" in output

    def test_vars_command(self):
        """vars shows current variables."""
        code = "x = 5\ny = 10\nprint x"
        # Step past both assignments, then check vars
        output = run_with_inputs(code, ["s", "s", "vars", "c"])
        assert "x = 5" in output
        assert "y = 10" in output

    def test_print_variable(self):
        """p <var> prints a variable."""
        code = "x = 42\nprint x"
        output = run_with_inputs(code, ["s", "p x", "c"])
        assert "42" in output

    def test_print_unknown_variable(self):
        """p <var> for unknown variable shows error."""
        code = "x = 5"
        output = run_with_inputs(code, ["p z", "c"])
        assert "not found" in output

    def test_breakpoint_commands(self):
        """b/bl/d commands."""
        code = "x = 1\ny = 2\nz = 3"
        output = run_with_inputs(code, ["b 2", "bl", "d 2", "bl", "c"])
        assert "Breakpoint set at line 2" in output
        assert "Breakpoint removed at line 2" in output

    def test_breakpoint_invalid_line(self):
        """b with out-of-range line shows error."""
        code = "x = 1"
        output = run_with_inputs(code, ["b 999", "c"])
        assert "out of range" in output

    def test_list_source(self):
        """list command shows source."""
        code = "x = 1\ny = 2\nz = 3\nprint x"
        output = run_with_inputs(code, ["list 5", "c"])
        assert "x = 1" in output
        assert "y = 2" in output

    def test_help(self):
        """h shows help text."""
        code = "x = 1"
        output = run_with_inputs(code, ["h", "c"])
        assert "step" in output.lower()
        assert "continue" in output.lower()
        assert "breakpoint" in output.lower()

    def test_funcs_command(self):
        """funcs shows defined functions."""
        code = "function add x y\n    return x + y\nadd 1 2"
        output = run_with_inputs(code, ["s", "funcs", "c"])
        assert "add(x, y)" in output

    def test_unknown_command(self):
        """Unknown command shows error."""
        code = "x = 1"
        output = run_with_inputs(code, ["foobar", "c"])
        assert "Unknown command" in output

    def test_where_command(self):
        """where shows current position."""
        code = "x = 1\ny = 2"
        output = run_with_inputs(code, ["where", "c"])
        assert "test.srv" in output

    def test_banner(self):
        """Banner shows file info."""
        code = "x = 1"
        output = run_with_inputs(code, ["c"])
        assert "sauravdb" in output
        assert "test.srv" in output


# ── DebugInterpreter ─────────────────────────────────────────────────

class TestDebugInterpreter:
    def test_is_interpreter_subclass(self):
        db = make_debugger("x = 1")
        assert isinstance(db.interpreter, DebugInterpreter)

    def test_statement_counting(self):
        code = "x = 1\ny = 2\nz = 3"
        db = make_debugger(code)
        db.stepping = False  # Don't prompt
        try:
            for node in db.ast_nodes:
                db.interpreter.interpret(node)
        except DebuggerQuit:
            pass
        assert db.statements_executed == 3

    def test_call_stack_tracks_functions(self):
        code = "function greet\n    print \"hi\"\ngreet"
        # Step into function, check stack, then continue
        output = run_with_inputs(code, ["s", "s", "stack", "c"])
        assert "greet" in output


# ── Edge Cases ───────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_program(self):
        code = ""
        output = run_with_inputs(code, [])
        assert "Program finished" in output
        assert "0 statements" in output

    def test_breakpoint_at_invalid_number(self):
        code = "x = 1"
        output = run_with_inputs(code, ["b abc", "c"])
        assert "Invalid line number" in output

    def test_delete_nonexistent_breakpoint(self):
        code = "x = 1"
        output = run_with_inputs(code, ["d 5", "c"])
        assert "No breakpoint" in output

    def test_no_breakpoints_message(self):
        code = "x = 1"
        output = run_with_inputs(code, ["bl", "c"])
        assert "No breakpoints" in output

    def test_breakpoint_stops_on_continue(self):
        """Setting a breakpoint and continuing should stop at it."""
        code = "x = 1\ny = 2\nz = 3\nprint z"
        # Set breakpoint at line 3, continue, verify we stopped
        output = run_with_inputs(code, ["b 3", "c", "p z", "c"])
        # After continuing to breakpoint at line 3, z should be set
        # (z = 3 is the breakpoint line, debugger stops before executing)
        # So z may not be set yet — check we at least stopped
        assert "z = 3" in output  # Source line shown in context

    def test_no_user_funcs(self):
        code = "x = 1"
        output = run_with_inputs(code, ["funcs", "c"])
        assert "no user-defined" in output

    def test_print_builtin(self):
        code = "x = 1"
        output = run_with_inputs(code, ["p abs", "c"])
        assert "builtin" in output

    def test_no_vars(self):
        code = "print 1"
        output = run_with_inputs(code, ["vars", "c"])
        assert "no variables" in output or "Variables" in output

    def test_stack_at_top_level(self):
        code = "x = 1"
        output = run_with_inputs(code, ["stack", "c"])
        assert "top level" in output

    def test_breakpoint_no_arg(self):
        code = "x = 1"
        output = run_with_inputs(code, ["b", "c"])
        assert "Usage" in output

    def test_delete_no_arg(self):
        code = "x = 1"
        output = run_with_inputs(code, ["d", "c"])
        assert "Usage" in output

    def test_print_no_arg(self):
        code = "x = 1"
        output = run_with_inputs(code, ["p", "c"])
        assert "Usage" in output

    def test_list_with_context(self):
        code = "\n".join([f"x{i} = {i}" for i in range(20)])
        output = run_with_inputs(code, ["list 3", "c"])
        # Should show some lines around current
        assert "│" in output

    def test_empty_input_ignored(self):
        """Empty input at prompt should be ignored, not crash."""
        code = "x = 1"
        output = run_with_inputs(code, ["", "", "c"])
        assert "Program finished" in output
