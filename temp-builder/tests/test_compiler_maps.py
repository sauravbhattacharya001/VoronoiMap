"""Tests for map/dict and for-in compiler support (sauravcode issue #3 Phase 2)."""

import subprocess
import os
import sys
import tempfile
import pytest

# Path to compiler
COMPILER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sauravcc.py')

def compile_and_run(code, expect_error=False):
    """Compile sauravcode to exe, run, return stdout."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.srv', delete=False) as f:
        f.write(code)
        srv_path = f.name
    exe_path = srv_path.replace('.srv', '.exe')
    try:
        # Compile
        result = subprocess.run(
            [sys.executable, COMPILER, srv_path, '-o', exe_path],
            capture_output=True, text=True, timeout=30
        )
        if expect_error:
            return result.stderr
        assert result.returncode == 0, f"Compilation failed: {result.stderr}"
        # Run
        result = subprocess.run(
            [exe_path], capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip()
    finally:
        for p in [srv_path, exe_path, srv_path.replace('.srv', '.c')]:
            if os.path.exists(p):
                os.unlink(p)


def emit_c(code):
    """Get the emitted C code for inspection."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.srv', delete=False) as f:
        f.write(code)
        srv_path = f.name
    try:
        result = subprocess.run(
            [sys.executable, COMPILER, srv_path, '--emit-c'],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout
    finally:
        os.unlink(srv_path)


class TestMapLiteral:
    """Map literal creation and printing."""

    def test_empty_map(self):
        out = compile_and_run('m = {}\nprint len m')
        assert out == '0'

    def test_single_pair(self):
        out = compile_and_run('m = {"x": 42}\nprint m["x"]')
        assert out == '42'

    def test_multiple_pairs(self):
        out = compile_and_run('m = {"a": 1, "b": 2, "c": 3}\nprint len m')
        assert out == '3'

    def test_float_values(self):
        out = compile_and_run('m = {"pi": 3.14}\nprint m["pi"]')
        assert out == '3.14'

    def test_negative_values(self):
        out = compile_and_run('m = {"neg": -5}\nprint m["neg"]')
        assert out == '-5'

    def test_zero_value(self):
        out = compile_and_run('m = {"zero": 0}\nprint m["zero"]')
        assert out == '0'

    def test_print_map(self):
        out = compile_and_run('m = {"x": 1}\nprint m')
        assert '"x": 1' in out
        assert '{' in out and '}' in out


class TestMapAccess:
    """Map key access and mutation."""

    def test_get_existing_key(self):
        out = compile_and_run('m = {"name": 42}\nprint m["name"]')
        assert out == '42'

    def test_set_new_key(self):
        out = compile_and_run('m = {"a": 1}\nm["b"] = 2\nprint m["b"]')
        assert out == '2'

    def test_update_existing_key(self):
        out = compile_and_run('m = {"x": 10}\nm["x"] = 20\nprint m["x"]')
        assert out == '20'

    def test_multiple_updates(self):
        code = 'm = {"a": 0}\nm["a"] = 1\nm["a"] = 2\nm["a"] = 3\nprint m["a"]'
        out = compile_and_run(code)
        assert out == '3'

    def test_add_many_keys(self):
        code = 'm = {}\nm["a"] = 1\nm["b"] = 2\nm["c"] = 3\nm["d"] = 4\nm["e"] = 5\nprint len m'
        out = compile_and_run(code)
        assert out == '5'


class TestMapBuiltins:
    """has_key and len on maps."""

    def test_has_key_true(self):
        out = compile_and_run('m = {"x": 1}\nprint has_key m "x"')
        assert out == '1'

    def test_has_key_false(self):
        out = compile_and_run('m = {"x": 1}\nprint has_key m "y"')
        assert out == '0'

    def test_has_key_after_add(self):
        code = 'm = {}\nprint has_key m "k"\nm["k"] = 99\nprint has_key m "k"'
        out = compile_and_run(code)
        assert out == '0\n1'

    def test_len_empty(self):
        out = compile_and_run('m = {}\nprint len m')
        assert out == '0'

    def test_len_after_inserts(self):
        code = 'm = {"a": 1}\nm["b"] = 2\nm["c"] = 3\nprint len m'
        out = compile_and_run(code)
        assert out == '3'

    def test_len_no_change_on_update(self):
        code = 'm = {"x": 1}\nm["x"] = 2\nprint len m'
        out = compile_and_run(code)
        assert out == '1'


class TestForEachMap:
    """for-in iteration over map keys."""

    def test_iterate_keys(self):
        code = 'm = {"a": 1, "b": 2}\nfor k in m\n    print k'
        out = compile_and_run(code)
        keys = set(out.split('\n'))
        assert keys == {'a', 'b'}

    def test_iterate_empty_map(self):
        code = 'm = {}\nfor k in m\n    print k'
        out = compile_and_run(code)
        assert out == ''

    def test_iterate_access_value(self):
        code = 'm = {"x": 10, "y": 20}\nfor k in m\n    print m[k]'
        out = compile_and_run(code)
        vals = set(out.split('\n'))
        assert vals == {'10', '20'}

    def test_iterate_single_entry(self):
        code = 'm = {"only": 42}\nfor k in m\n    print k'
        out = compile_and_run(code)
        assert out == 'only'


class TestForEachList:
    """for-in iteration over lists."""

    def test_iterate_list(self):
        code = 'nums = [10, 20, 30]\nfor n in nums\n    print n'
        out = compile_and_run(code)
        assert out == '10\n20\n30'

    def test_iterate_empty_list(self):
        code = 'xs = []\nfor x in xs\n    print x'
        out = compile_and_run(code)
        assert out == ''

    def test_iterate_single_element(self):
        code = 'xs = [42]\nfor x in xs\n    print x'
        out = compile_and_run(code)
        assert out == '42'

    def test_iterate_with_body(self):
        code = 'xs = [1, 2, 3]\ntotal = 0\nfor x in xs\n    total = total + x\nprint total'
        out = compile_and_run(code)
        assert out == '6'

    def test_iterate_with_condition(self):
        code = 'xs = [1, 2, 3, 4, 5]\nfor x in xs\n    if x > 3\n        print x'
        out = compile_and_run(code)
        assert out == '4\n5'

    def test_nested_for_each(self):
        code = 'xs = [1, 2]\nys = [10, 20]\nfor x in xs\n    for y in ys\n        print x + y'
        out = compile_and_run(code)
        assert out == '11\n21\n12\n22'


class TestMapWithControl:
    """Maps combined with control flow."""

    def test_map_in_if(self):
        code = 'm = {"flag": 1}\nif m["flag"] == 1\n    print "yes"'
        out = compile_and_run(code)
        assert out == 'yes'

    def test_has_key_in_if(self):
        code = 'm = {"x": 1}\nif has_key m "x"\n    print "found"'
        out = compile_and_run(code)
        assert out == 'found'

    def test_map_in_while(self):
        code = 'm = {"count": 3}\nwhile m["count"] > 0\n    print m["count"]\n    m["count"] = m["count"] - 1'
        out = compile_and_run(code)
        assert out == '3\n2\n1'

    def test_map_in_for_body(self):
        """Map created and used inside a for loop."""
        code = 'for i 0 2\n    m = {"val": i}\n    print m["val"]'
        out = compile_and_run(code)
        assert out == '0\n1'


class TestMapWithExpressions:
    """Maps used in expressions."""

    def test_map_value_in_arithmetic(self):
        code = 'm = {"a": 10, "b": 20}\nprint m["a"] + m["b"]'
        out = compile_and_run(code)
        assert out == '30'

    def test_map_value_in_comparison(self):
        code = 'm = {"x": 5}\nif m["x"] > 3\n    print "big"'
        out = compile_and_run(code)
        assert out == 'big'

    def test_map_value_multiplication(self):
        code = 'm = {"price": 10, "qty": 3}\nprint m["price"] * m["qty"]'
        out = compile_and_run(code)
        assert out == '30'


class TestForEachWithClassicFor:
    """for-in coexists with classic range for loops."""

    def test_classic_for_still_works(self):
        code = 'for i 0 3\n    print i'
        out = compile_and_run(code)
        assert out == '0\n1\n2'

    def test_both_for_styles(self):
        code = 'for i 0 3\n    print i\nxs = [10, 20]\nfor x in xs\n    print x'
        out = compile_and_run(code)
        assert out == '0\n1\n2\n10\n20'


class TestMapHashResizing:
    """Test that the hash map handles many entries (triggers resize)."""

    def test_many_entries(self):
        # Insert more than initial capacity (16) to trigger resize
        lines = ['m = {}']
        for i in range(30):
            lines.append(f'm["k{i}"] = {i}')
        lines.append('print len m')
        code = '\n'.join(lines)
        out = compile_and_run(code)
        assert out == '30'

    def test_many_entries_access(self):
        lines = ['m = {}']
        for i in range(20):
            lines.append(f'm["k{i}"] = {i * 10}')
        lines.append('print m["k15"]')
        code = '\n'.join(lines)
        out = compile_and_run(code)
        assert out == '150'


class TestEmittedC:
    """Verify the emitted C code structure."""

    def test_map_emits_srvmap(self):
        c = emit_c('m = {"x": 1}')
        assert 'SrvMap' in c
        assert 'srv_map_new' in c
        assert 'srv_map_set' in c

    def test_foreach_emits_loop(self):
        c = emit_c('m = {"a": 1}\nfor k in m\n    print k')
        assert 'for (int __i_k' in c
        assert '.occupied' in c

    def test_has_key_emits_call(self):
        c = emit_c('m = {"x": 1}\nprint has_key m "x"')
        assert 'srv_map_has_key' in c

    def test_no_map_runtime_without_maps(self):
        c = emit_c('x = 42\nprint x')
        assert 'SrvMap' not in c
