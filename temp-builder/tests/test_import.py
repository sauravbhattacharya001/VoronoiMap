"""
Tests for the sauravcode import system.

Tests module loading, circular import prevention, error handling,
variable/function sharing, nested imports, and path resolution.
"""

import pytest
import sys
import os
import io
import tempfile
import shutil
from contextlib import redirect_stdout

# Add repo root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from saurav import (
    tokenize,
    Parser,
    Interpreter,
    ImportNode,
    FunctionCallNode,
)


# ============================================================
# Helpers
# ============================================================

def run_code(code: str) -> str:
    """Parse and execute sauravcode, returning captured stdout."""
    tokens = list(tokenize(code))
    parser = Parser(tokens)
    ast_nodes = parser.parse()
    
    interpreter = Interpreter()
    buf = io.StringIO()
    with redirect_stdout(buf):
        for node in ast_nodes:
            if isinstance(node, FunctionCallNode):
                interpreter.execute_function(node)
            else:
                interpreter.interpret(node)
    return buf.getvalue().strip()


def run_code_with_dir(code: str, source_dir: str) -> str:
    """Parse and execute sauravcode with a specific source directory."""
    tokens = list(tokenize(code))
    parser = Parser(tokens)
    ast_nodes = parser.parse()
    
    interpreter = Interpreter()
    interpreter._source_dir = source_dir
    buf = io.StringIO()
    with redirect_stdout(buf):
        for node in ast_nodes:
            if isinstance(node, FunctionCallNode):
                interpreter.execute_function(node)
            else:
                interpreter.interpret(node)
    return buf.getvalue().strip()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test modules."""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def write_module(directory: str, name: str, code: str):
    """Write a .srv module file to a directory."""
    path = os.path.join(directory, name if name.endswith('.srv') else name + '.srv')
    with open(path, 'w') as f:
        f.write(code)
    return path


# ============================================================
# Parser Tests
# ============================================================

class TestImportParser:
    """Tests for import statement parsing."""

    def test_parse_string_import(self):
        tokens = list(tokenize('import "math_utils"\n'))
        parser = Parser(tokens)
        stmts = parser.parse()
        assert len(stmts) == 1
        assert isinstance(stmts[0], ImportNode)
        assert stmts[0].module_path == "math_utils"

    def test_parse_bare_identifier_import(self):
        tokens = list(tokenize('import helpers\n'))
        parser = Parser(tokens)
        stmts = parser.parse()
        assert len(stmts) == 1
        assert isinstance(stmts[0], ImportNode)
        assert stmts[0].module_path == "helpers"

    def test_parse_import_with_extension(self):
        tokens = list(tokenize('import "utils.srv"\n'))
        parser = Parser(tokens)
        stmts = parser.parse()
        assert stmts[0].module_path == "utils.srv"

    def test_parse_import_with_path(self):
        tokens = list(tokenize('import "lib/utils"\n'))
        parser = Parser(tokens)
        stmts = parser.parse()
        assert stmts[0].module_path == "lib/utils"

    def test_import_node_repr(self):
        node = ImportNode("my_module")
        assert "my_module" in repr(node)
        assert "ImportNode" in repr(node)

    def test_parse_multiple_imports(self):
        code = 'import "a"\nimport "b"\nimport "c"\n'
        tokens = list(tokenize(code))
        parser = Parser(tokens)
        stmts = parser.parse()
        assert len(stmts) == 3
        assert all(isinstance(s, ImportNode) for s in stmts)
        assert stmts[0].module_path == "a"
        assert stmts[1].module_path == "b"
        assert stmts[2].module_path == "c"

    def test_import_is_keyword(self):
        """'import' should tokenize as a keyword, not an identifier."""
        tokens = list(tokenize('import "foo"\n'))
        kw_tokens = [t for t in tokens if t[0] == 'KEYWORD' and t[1] == 'import']
        assert len(kw_tokens) == 1


# ============================================================
# Import Execution Tests
# ============================================================

class TestImportExecution:
    """Tests for import statement execution."""

    def test_import_variables(self, temp_dir):
        write_module(temp_dir, "vars", 'X = 42\nY = "hello"\n')
        output = run_code_with_dir(
            'import "vars"\nprint X\nprint Y\n',
            temp_dir
        )
        assert output == "42\nhello"

    def test_import_does_not_shadow_existing_variables(self, temp_dir):
        """Issue #13: imported variables must not overwrite caller's."""
        write_module(temp_dir, "vars", 'X = 42\nY = "imported"\n')
        output = run_code_with_dir(
            'X = 100\nimport "vars"\nprint X\nprint Y\n',
            temp_dir
        )
        # X should still be 100 (caller's value preserved), Y is new
        assert output == "100\nimported"

    def test_import_multiple_modules_no_cross_shadow(self, temp_dir):
        """Second import should not overwrite variables from first import."""
        write_module(temp_dir, "mod_a", 'SHARED = "from_a"\nA_ONLY = 1\n')
        write_module(temp_dir, "mod_b", 'SHARED = "from_b"\nB_ONLY = 2\n')
        output = run_code_with_dir(
            'import "mod_a"\nimport "mod_b"\nprint SHARED\nprint A_ONLY\nprint B_ONLY\n',
            temp_dir
        )
        # SHARED should keep mod_a's value (first import wins)
        assert output == "from_a\n1\n2"

    def test_import_functions(self, temp_dir):
        write_module(temp_dir, "math_lib",
            'function square n\n    return n * n\n\nfunction add a b\n    return a + b\n')
        output = run_code_with_dir(
            'import "math_lib"\nprint square 5\nprint add 3 4\n',
            temp_dir
        )
        assert output == "25\n7"

    def test_import_auto_adds_extension(self, temp_dir):
        write_module(temp_dir, "helper.srv", 'LOADED = true\n')
        output = run_code_with_dir(
            'import "helper"\nprint LOADED\n',
            temp_dir
        )
        assert output == "true"

    def test_import_with_explicit_extension(self, temp_dir):
        write_module(temp_dir, "utils.srv", 'VAL = 99\n')
        output = run_code_with_dir(
            'import "utils.srv"\nprint VAL\n',
            temp_dir
        )
        assert output == "99"

    def test_import_bare_identifier(self, temp_dir):
        write_module(temp_dir, "mylib.srv", 'LIB_VAR = 123\n')
        output = run_code_with_dir(
            'import mylib\nprint LIB_VAR\n',
            temp_dir
        )
        assert output == "123"

    def test_import_multiple_modules(self, temp_dir):
        write_module(temp_dir, "mod_a", 'A = 1\n')
        write_module(temp_dir, "mod_b", 'B = 2\n')
        write_module(temp_dir, "mod_c", 'C = 3\n')
        output = run_code_with_dir(
            'import "mod_a"\nimport "mod_b"\nimport "mod_c"\nprint A + B + C\n',
            temp_dir
        )
        assert output == "6"

    def test_import_module_with_print(self, temp_dir):
        """Import a module that has print statements — they should execute."""
        write_module(temp_dir, "loud", 'print "loading"\nX = 10\n')
        output = run_code_with_dir(
            'import "loud"\nprint X\n',
            temp_dir
        )
        assert output == "loading\n10"

    def test_import_function_using_module_var(self, temp_dir):
        """Imported functions should be able to use module-level variables."""
        write_module(temp_dir, "config",
            'PREFIX = "Hello"\nfunction greet name\n    return f"{PREFIX} {name}"\n')
        # Variables set before function definition are available via closure
        output = run_code_with_dir(
            'import "config"\nresult = greet "World"\nprint result\n',
            temp_dir
        )
        assert "World" in output


# ============================================================
# Circular Import Tests
# ============================================================

class TestCircularImports:
    """Tests for circular import detection and prevention."""

    def test_circular_import_no_infinite_loop(self, temp_dir):
        """Circular imports should not cause infinite recursion."""
        write_module(temp_dir, "circ_a", 'import "circ_b"\nA = "from_a"\nprint "a loaded"\n')
        write_module(temp_dir, "circ_b", 'import "circ_a"\nB = "from_b"\nprint "b loaded"\n')
        output = run_code_with_dir('import "circ_a"\n', temp_dir)
        # Should complete without hanging
        assert "loaded" in output

    def test_self_import_skipped(self, temp_dir):
        """Importing self should be silently skipped."""
        write_module(temp_dir, "self_ref", 'import "self_ref"\nprint "ok"\n')
        output = run_code_with_dir('import "self_ref"\n', temp_dir)
        # "ok" should print exactly once
        assert output.count("ok") == 1

    def test_diamond_import(self, temp_dir):
        """Diamond dependency: A->B, A->C, B->D, C->D. D loads once."""
        write_module(temp_dir, "diamond_d", 'print "d"\nD = 4\n')
        write_module(temp_dir, "diamond_b", 'import "diamond_d"\nB = 2\n')
        write_module(temp_dir, "diamond_c", 'import "diamond_d"\nC = 3\n')
        write_module(temp_dir, "diamond_a",
            'import "diamond_b"\nimport "diamond_c"\nprint D\n')
        output = run_code_with_dir('import "diamond_a"\n', temp_dir)
        # "d" should print exactly once
        assert output.count("d") == 1

    def test_duplicate_import_skipped(self, temp_dir):
        """Importing the same module twice should only execute it once."""
        write_module(temp_dir, "once", 'print "loaded"\nVAL = 7\n')
        output = run_code_with_dir(
            'import "once"\nimport "once"\nprint VAL\n',
            temp_dir
        )
        assert output.count("loaded") == 1
        assert "7" in output


# ============================================================
# Error Handling Tests
# ============================================================

class TestImportErrors:
    """Tests for import error handling."""

    def test_import_nonexistent_file(self, temp_dir):
        with pytest.raises(RuntimeError, match="not found"):
            run_code_with_dir('import "does_not_exist"\n', temp_dir)

    def test_import_syntax_error_in_module(self, temp_dir):
        write_module(temp_dir, "bad_syntax", 'function\n')
        with pytest.raises(SyntaxError):
            run_code_with_dir('import "bad_syntax"\n', temp_dir)

    def test_import_runtime_error_in_module(self, temp_dir):
        write_module(temp_dir, "bad_runtime", 'x = 1 / 0\n')
        with pytest.raises((ZeroDivisionError, RuntimeError)):
            run_code_with_dir('import "bad_runtime"\n', temp_dir)


# ============================================================
# Nested Import Tests
# ============================================================

class TestNestedImports:
    """Tests for multi-level import chains."""

    def test_two_level_import(self, temp_dir):
        """A imports B, B imports C — A can use C's definitions."""
        write_module(temp_dir, "level_c", 'DEEP = 42\n')
        write_module(temp_dir, "level_b", 'import "level_c"\nMID = DEEP + 1\n')
        output = run_code_with_dir(
            'import "level_b"\nprint MID\n',
            temp_dir
        )
        assert output == "43"

    def test_three_level_chain(self, temp_dir):
        write_module(temp_dir, "chain_1", 'V1 = 1\n')
        write_module(temp_dir, "chain_2", 'import "chain_1"\nV2 = V1 + 1\n')
        write_module(temp_dir, "chain_3", 'import "chain_2"\nV3 = V2 + 1\n')
        output = run_code_with_dir(
            'import "chain_3"\nprint V3\n',
            temp_dir
        )
        assert output == "3"

    def test_imported_function_calls_other_import(self, temp_dir):
        write_module(temp_dir, "base", 'function double n\n    return n * 2\n')
        write_module(temp_dir, "derived",
            'import "base"\nfunction quadruple n\n    return double (double n)\n')
        output = run_code_with_dir(
            'import "derived"\nprint quadruple 5\n',
            temp_dir
        )
        assert output == "20"


# ============================================================
# Subdirectory Import Tests
# ============================================================

class TestSubdirectoryImports:
    """Tests for importing from subdirectories."""

    def test_import_from_subdirectory(self, temp_dir):
        lib_dir = os.path.join(temp_dir, "lib")
        os.makedirs(lib_dir)
        write_module(lib_dir, "helper.srv", 'HELPER = true\n')
        # Note: path uses forward slashes
        output = run_code_with_dir(
            'import "lib/helper"\nprint HELPER\n',
            temp_dir
        )
        assert output == "true"


# ============================================================
# Integration Tests
# ============================================================

class TestImportIntegration:
    """Integration tests combining imports with other language features."""

    def test_import_with_lists(self, temp_dir):
        write_module(temp_dir, "list_utils",
            'function first items\n    return items[0]\n')
        output = run_code_with_dir(
            'import "list_utils"\ndata = [10, 20, 30]\nprint first data\n',
            temp_dir
        )
        assert output == "10"

    def test_import_with_maps(self, temp_dir):
        write_module(temp_dir, "defaults",
            'DEFAULTS = {"color": "blue", "size": 12}\n')
        output = run_code_with_dir(
            'import "defaults"\nprint DEFAULTS["color"]\n',
            temp_dir
        )
        assert output == "blue"

    def test_import_with_try_catch(self, temp_dir):
        write_module(temp_dir, "safe", 'function safe_div a b\n    if b == 0\n        throw "division by zero"\n    return a / b\n')
        output = run_code_with_dir(
            'import "safe"\ntry\n    print safe_div 10 0\ncatch e\n    print f"Error: {e}"\n',
            temp_dir
        )
        assert "Error: division by zero" in output

    def test_import_with_lambda(self, temp_dir):
        """Test import with lambda — uses map builtin which handles lambdas."""
        write_module(temp_dir, "data_mod",
            'NUMS = [1, 2, 3]\n')
        output = run_code_with_dir(
            'import "data_mod"\nresult = map (lambda x -> x * 3) NUMS\nprint result\n',
            temp_dir
        )
        assert "3" in output
        assert "9" in output

    def test_import_preserves_local_scope(self, temp_dir):
        """Variables set before import should still be available after."""
        write_module(temp_dir, "addon", 'IMPORTED = true\n')
        output = run_code_with_dir(
            'LOCAL = 42\nimport "addon"\nprint LOCAL\nprint IMPORTED\n',
            temp_dir
        )
        assert output == "42\ntrue"
