#!/usr/bin/env python3
"""sauravdb — Interactive debugger for sauravcode (.srv files).

Usage:
    python sauravdb.py <filename>.srv [--debug]

Debugger commands:
    s, step       Execute the next statement
    n, next       Execute the next statement (same as step for now)
    c, continue   Continue until next breakpoint or end
    b <line>      Set a breakpoint at line number
    d <line>      Delete breakpoint at line number
    bl            List all breakpoints
    p <expr>      Print a variable's value
    vars          Show all variables in current scope
    funcs         Show all defined functions
    stack         Show call stack
    where         Show current position in source
    list [n]      Show source code around current line (n=context lines)
    restart       Restart from beginning
    q, quit       Exit debugger
    h, help       Show this help
"""

import sys
import os
try:
    import readline  # Enable line editing in input() (Unix/macOS)
except ImportError:
    pass  # readline not available on Windows — input() still works

# Import the sauravcode interpreter
from saurav import (
    tokenize, Parser, Interpreter, ASTNode,
    FunctionNode, ReturnNode, PrintNode, FunctionCallNode,
    AssignmentNode, IndexedAssignmentNode, IfNode, WhileNode,
    ForNode, ForEachNode, TryCatchNode, ThrowNode, AppendNode,
    ImportNode, ThrowSignal, ReturnSignal, _format_list, _format_map,
    NumberNode, StringNode, BoolNode, IdentifierNode, BinaryOpNode,
    CompareNode, LogicalNode, UnaryOpNode, ListNode, IndexNode,
    LenNode, MapNode, FStringNode, LambdaNode
)


class LineTrackingParser(Parser):
    """Parser subclass that tags AST nodes with source line numbers.

    Wraps parse_statement() to capture the token position before parsing,
    then annotates the resulting node with the line number from that token.
    This is non-invasive — doesn't change any parsing logic.
    """

    def parse_statement(self):
        # Remember the current token's line number before parsing
        line = None
        if self.pos < len(self.tokens):
            tok = self.tokens[self.pos]
            if len(tok) > 2:
                line = tok[2]

        node = super().parse_statement()

        if node is not None and node.line_num is None and line is not None:
            node.line_num = line

        return node


class DebuggerQuit(Exception):
    """Raised when the user quits the debugger."""
    pass


class DebuggerRestart(Exception):
    """Raised when the user wants to restart."""
    pass


def _format_value(value, max_len=80):
    """Format a value for display, truncating if too long."""
    if isinstance(value, float) and value == int(value):
        s = str(int(value))
    elif isinstance(value, bool):
        s = "true" if value else "false"
    elif isinstance(value, list):
        s = _format_list(value)
    elif isinstance(value, dict):
        s = _format_map(value)
    elif isinstance(value, str):
        s = repr(value)
    else:
        s = str(value)
    if len(s) > max_len:
        s = s[:max_len - 3] + "..."
    return s


def _node_line(node):
    """Extract the source line number from an AST node, if available."""
    if hasattr(node, 'line_num') and node.line_num is not None:
        return node.line_num
    return None


class SauravDebugger:
    """Interactive step-through debugger for sauravcode."""

    def __init__(self, filename, source_code, ast_nodes):
        self.filename = filename
        self.source_lines = source_code.splitlines()
        self.ast_nodes = ast_nodes
        self.interpreter = None

        # Debugger state
        self.breakpoints = set()       # set of line numbers
        self.stepping = True           # True = stop at every statement
        self.current_line = 1
        self.call_stack = []           # list of (function_name, line)
        self.statements_executed = 0

        self._init_interpreter()

    def _init_interpreter(self):
        """Create a fresh interpreter with debug hooks."""
        self.interpreter = DebugInterpreter(self)
        abs_filename = os.path.abspath(self.filename)
        self.interpreter._source_dir = os.path.dirname(abs_filename)
        self.interpreter._imported_modules.add(abs_filename)

    def run(self):
        """Main debugger loop."""
        self._print_banner()
        self._show_current_context()

        try:
            result = None
            for node in self.ast_nodes:
                if isinstance(node, FunctionCallNode):
                    result = self.interpreter.execute_function(node)
                else:
                    self.interpreter.interpret(node)

            print(f"\n{'='*50}")
            print(f"Program finished. {self.statements_executed} statements executed.")
            if result is not None:
                print(f"Final result: {_format_value(result)}")

        except ThrowSignal as e:
            msg = e.message
            if isinstance(msg, float) and msg == int(msg):
                msg = int(msg)
            print(f"\n⚠️  Uncaught error: {msg}")
        except DebuggerQuit:
            print("\nDebugger exited.")
        except DebuggerRestart:
            print("\n🔄 Restarting...")
            self._init_interpreter()
            self.stepping = True
            self.current_line = 1
            self.call_stack = []
            self.statements_executed = 0
            self.run()

    def _print_banner(self):
        """Print welcome banner."""
        print(f"sauravdb — sauravcode debugger")
        print(f"File: {self.filename} ({len(self.source_lines)} lines)")
        print(f"Type 'h' for help, 's' to step, 'c' to continue, 'q' to quit.")
        print()

    def on_statement(self, node):
        """Called before each statement is executed."""
        self.statements_executed += 1
        line = _node_line(node)
        if line:
            self.current_line = line

        should_break = self.stepping or (line and line in self.breakpoints)

        if should_break:
            self._show_current_context(node)
            self._command_loop(node)

    def _show_current_context(self, node=None):
        """Show the current source line and surrounding context."""
        line = self.current_line
        stmt_type = type(node).__name__ if node else "start"

        # Color the current line indicator
        print(f"→ {self.filename}:{line}  [{stmt_type}]")

        # Show 2 lines before and after
        start = max(0, line - 3)
        end = min(len(self.source_lines), line + 2)
        for i in range(start, end):
            line_num = i + 1
            marker = "→ " if line_num == line else "  "
            bp = "●" if line_num in self.breakpoints else " "
            src = self.source_lines[i] if i < len(self.source_lines) else ""
            print(f"  {bp}{marker}{line_num:4d} │ {src}")
        print()

    def _command_loop(self, node=None):
        """Interactive command loop at a breakpoint."""
        while True:
            try:
                cmd = input("(sauravdb) ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                raise DebuggerQuit()

            if not cmd:
                continue

            parts = cmd.split(None, 1)
            command = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if command in ('s', 'step', 'n', 'next'):
                self.stepping = True
                return

            elif command in ('c', 'continue'):
                self.stepping = False
                return

            elif command in ('b', 'break'):
                self._cmd_breakpoint(arg)

            elif command in ('d', 'delete'):
                self._cmd_delete_breakpoint(arg)

            elif command in ('bl', 'breakpoints'):
                self._cmd_list_breakpoints()

            elif command in ('p', 'print'):
                self._cmd_print(arg)

            elif command == 'vars':
                self._cmd_vars()

            elif command == 'funcs':
                self._cmd_funcs()

            elif command == 'stack':
                self._cmd_stack()

            elif command == 'where':
                self._show_current_context(node)

            elif command in ('l', 'list'):
                self._cmd_list_source(arg)

            elif command == 'restart':
                raise DebuggerRestart()

            elif command in ('q', 'quit', 'exit'):
                raise DebuggerQuit()

            elif command in ('h', 'help', '?'):
                self._cmd_help()

            else:
                print(f"Unknown command: '{command}'. Type 'h' for help.")

    # ── Commands ─────────────────────────────────────────────

    def _cmd_breakpoint(self, arg):
        """Set a breakpoint at a line number."""
        if not arg:
            print("Usage: b <line_number>")
            return
        try:
            line = int(arg)
            if line < 1 or line > len(self.source_lines):
                print(f"Line {line} out of range (1-{len(self.source_lines)})")
                return
            self.breakpoints.add(line)
            print(f"Breakpoint set at line {line}: {self.source_lines[line-1].strip()}")
        except ValueError:
            print(f"Invalid line number: {arg}")

    def _cmd_delete_breakpoint(self, arg):
        """Delete a breakpoint."""
        if not arg:
            print("Usage: d <line_number>")
            return
        try:
            line = int(arg)
            if line in self.breakpoints:
                self.breakpoints.discard(line)
                print(f"Breakpoint removed at line {line}")
            else:
                print(f"No breakpoint at line {line}")
        except ValueError:
            print(f"Invalid line number: {arg}")

    def _cmd_list_breakpoints(self):
        """List all breakpoints."""
        if not self.breakpoints:
            print("No breakpoints set.")
            return
        print("Breakpoints:")
        for line in sorted(self.breakpoints):
            src = self.source_lines[line-1].strip() if line <= len(self.source_lines) else ""
            print(f"  line {line}: {src}")

    def _cmd_print(self, arg):
        """Print a variable value."""
        if not arg:
            print("Usage: p <variable_name>")
            return
        name = arg.strip()
        if name in self.interpreter.variables:
            val = self.interpreter.variables[name]
            print(f"  {name} = {_format_value(val, max_len=200)}")
        elif name in self.interpreter.functions:
            fn = self.interpreter.functions[name]
            print(f"  {name} = function({', '.join(fn.params)})")
        elif name in self.interpreter.builtins:
            print(f"  {name} = <builtin function>")
        else:
            print(f"  '{name}' not found in current scope")

    def _cmd_vars(self):
        """Show all variables."""
        if not self.interpreter.variables:
            print("  (no variables)")
            return
        print("Variables:")
        for name, val in sorted(self.interpreter.variables.items()):
            print(f"  {name} = {_format_value(val)}")

    def _cmd_funcs(self):
        """Show all defined functions."""
        user_funcs = self.interpreter.functions
        if not user_funcs:
            print("  (no user-defined functions)")
            return
        print("Functions:")
        for name, fn in sorted(user_funcs.items()):
            print(f"  {name}({', '.join(fn.params)})")

    def _cmd_stack(self):
        """Show call stack."""
        if not self.call_stack:
            print("  <top level>")
            return
        print("Call stack (most recent first):")
        for i, (fname, line) in enumerate(reversed(self.call_stack)):
            prefix = "→ " if i == 0 else "  "
            print(f"  {prefix}{fname}() at line {line}")
        print(f"    <top level>")

    def _cmd_list_source(self, arg):
        """List source code."""
        try:
            context = int(arg) if arg else 10
        except ValueError:
            context = 10
        start = max(0, self.current_line - context - 1)
        end = min(len(self.source_lines), self.current_line + context)
        for i in range(start, end):
            line_num = i + 1
            marker = "→ " if line_num == self.current_line else "  "
            bp = "●" if line_num in self.breakpoints else " "
            print(f"  {bp}{marker}{line_num:4d} │ {self.source_lines[i]}")

    def _cmd_help(self):
        """Show help."""
        print("""
Debugger Commands:
  s, step       Step to next statement
  n, next       Step to next statement (same as step)
  c, continue   Continue until breakpoint or end
  b <line>      Set breakpoint at line number
  d <line>      Delete breakpoint at line number
  bl            List all breakpoints
  p <var>       Print variable value
  vars          Show all variables
  funcs         Show defined functions
  stack         Show call stack
  where         Show current position
  list [n]      Show source (n = context lines, default 10)
  restart       Restart program
  q, quit       Exit debugger
  h, help       Show this help
""")


class DebugInterpreter(Interpreter):
    """Interpreter subclass with debugging hooks.

    Overrides interpret() and execute_function() to call
    debugger.on_statement() before each statement, enabling
    step-through execution.
    """

    def __init__(self, debugger):
        super().__init__()
        self.debugger = debugger

    def interpret(self, ast):
        """Override to add debug hook before each statement."""
        self.debugger.on_statement(ast)
        return super().interpret(ast)

    def execute_function(self, call_node):
        """Override to track call stack."""
        line = _node_line(call_node)
        fname = call_node.name if isinstance(call_node, FunctionCallNode) else "?"
        self.debugger.call_stack.append((fname, line or 0))
        try:
            result = super().execute_function(call_node)
        finally:
            if self.debugger.call_stack:
                self.debugger.call_stack.pop()
        return result

    def execute_if(self, node):
        """Override to add debug hook."""
        self.debugger.on_statement(node)
        # Execute the if logic without calling super().interpret() again
        return self._execute_if_body(node)

    def _execute_if_body(self, node):
        """Execute if/elif/else without re-triggering debug hook."""
        condition = self.evaluate(node.condition)
        if condition:
            for stmt in node.body:
                if isinstance(stmt, FunctionCallNode):
                    self.execute_function(stmt)
                else:
                    self.interpret(stmt)
            return

        for elif_cond, elif_body in node.elif_chains:
            if self.evaluate(elif_cond):
                for stmt in elif_body:
                    if isinstance(stmt, FunctionCallNode):
                        self.execute_function(stmt)
                    else:
                        self.interpret(stmt)
                return

        if node.else_body:
            for stmt in node.else_body:
                if isinstance(stmt, FunctionCallNode):
                    self.execute_function(stmt)
                else:
                    self.interpret(stmt)

    def execute_while(self, node):
        """Override to add debug hook on each iteration."""
        iteration = 0
        while self.evaluate(node.condition):
            iteration += 1
            if iteration > 10_000_000:
                raise RuntimeError("Maximum loop iterations exceeded")
            self.debugger.on_statement(node)
            for stmt in node.body:
                if isinstance(stmt, FunctionCallNode):
                    self.execute_function(stmt)
                else:
                    self.interpret(stmt)

    def execute_for(self, node):
        """Override to add debug hook on each iteration."""
        start = int(self.evaluate(node.start))
        end = int(self.evaluate(node.end))
        for i in range(start, end):
            self.debugger.on_statement(node)
            self.variables[node.var] = float(i)
            for stmt in node.body:
                if isinstance(stmt, FunctionCallNode):
                    self.execute_function(stmt)
                else:
                    self.interpret(stmt)

    def execute_for_each(self, node):
        """Override to add debug hook on each iteration."""
        iterable = self.evaluate(node.iterable)
        if isinstance(iterable, dict):
            iterable = list(iterable.keys())
        elif isinstance(iterable, str):
            iterable = list(iterable)
        elif not isinstance(iterable, list):
            raise RuntimeError(f"Cannot iterate over {type(iterable).__name__}")

        for item in iterable:
            self.debugger.on_statement(node)
            self.variables[node.var] = item
            for stmt in node.body:
                if isinstance(stmt, FunctionCallNode):
                    self.execute_function(stmt)
                else:
                    self.interpret(stmt)


def main():
    """Entry point for the sauravcode debugger."""
    args = sys.argv[1:]

    # Handle --debug flag
    debug_mode = '--debug' in args
    if debug_mode:
        args.remove('--debug')
        import saurav
        saurav.DEBUG = True

    if not args or args[0] in ('--help', '-h'):
        print(__doc__)
        sys.exit(0)

    filename = args[0]

    if not filename.endswith('.srv'):
        print("Error: File must have a .srv extension.")
        sys.exit(1)

    if not os.path.isfile(filename):
        print(f"Error: File '{filename}' does not exist.")
        sys.exit(1)

    try:
        with open(filename, 'r') as f:
            source = f.read()
    except Exception as e:
        print(f"Error reading file: {e}")
        sys.exit(1)

    tokens = list(tokenize(source))
    parser = LineTrackingParser(tokens)
    ast_nodes = parser.parse()

    debugger = SauravDebugger(filename, source, ast_nodes)
    debugger.run()


if __name__ == '__main__':
    main()
