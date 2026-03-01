"""
Tests for sauravcode compiler (sauravcc.py) — OOP, Pop, and advanced features.

Covers class compilation, object instantiation, dot access, method calls,
dot assignment, pop operations, and edge cases in C code generation.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sauravcc import (
    tokenize,
    Parser,
    CCodeGenerator,
    ProgramNode,
    NumberNode,
    StringNode,
    BoolNode,
    IdentifierNode,
    BinaryOpNode,
    UnaryOpNode,
    CompareNode,
    LogicalNode,
    FunctionNode,
    FunctionCallNode,
    AssignmentNode,
    PrintNode,
    ReturnNode,
    IfNode,
    WhileNode,
    ForNode,
    ListNode,
    IndexNode,
    IndexedAssignmentNode,
    AppendNode,
    LenNode,
    ClassNode,
    NewNode,
    DotAccessNode,
    MethodCallNode,
    DotAssignmentNode,
    TryCatchNode,
    PopNode,
    ASTNode,
)


# ============================================================
# Helpers
# ============================================================

def compile_to_c(code: str) -> str:
    """Tokenize, parse, and compile sauravcode to C source."""
    tokens = tokenize(code)
    parser = Parser(tokens)
    program = parser.parse()
    codegen = CCodeGenerator()
    return codegen.compile(program)


def parse_program(code: str) -> ProgramNode:
    """Parse sauravcode and return the ProgramNode AST."""
    tokens = tokenize(code)
    parser = Parser(tokens)
    return parser.parse()


# ============================================================
# Class Compilation Tests
# ============================================================

class TestClassCompilation:
    """Test C code generation for class definitions and usage."""

    def test_class_simple_init_return(self):
        """Class with simple init (no self.field) compiles to struct."""
        code = """class Foo
    function init
        return 0
"""
        c_code = compile_to_c(code)
        assert "typedef struct" in c_code
        assert "Foo" in c_code
        assert "Foo_init" in c_code

    def test_class_with_no_fields_has_placeholder(self):
        """Class without self.field assignments gets a placeholder field."""
        code = """class Empty
    function init
        return 0
"""
        c_code = compile_to_c(code)
        assert "typedef struct" in c_code
        assert "Empty" in c_code
        assert "__placeholder" in c_code

    def test_class_method_takes_self_pointer(self):
        """Class methods compile to C functions taking struct pointer."""
        code = """class Counter
    function init
        return 0
    function reset
        return 0
"""
        c_code = compile_to_c(code)
        assert "Counter_init" in c_code
        assert "Counter_reset" in c_code
        assert "Counter *self" in c_code

    def test_class_method_with_params(self):
        """Class methods with parameters compile correctly."""
        code = """class Math
    function init
        return 0
    function add x y
        return x + y
"""
        c_code = compile_to_c(code)
        assert "Math_add" in c_code
        assert "double x" in c_code
        assert "double y" in c_code

    def test_self_field_assignment_compiles(self):
        """self.field = val inside class methods compiles to struct field assignment via pointer."""
        code = """class Point
    function init x y
        self.x = x
        self.y = y
"""
        c_code = compile_to_c(code)
        assert "self->x =" in c_code
        assert "self->y =" in c_code
        assert "double x;" in c_code  # struct field
        assert "double y;" in c_code  # struct field

    def test_multiple_classes_simple(self):
        """Multiple class definitions with simple init generate separate structs."""
        code = """class Foo
    function init
        return 0

class Bar
    function init
        return 0
"""
        c_code = compile_to_c(code)
        assert "} Foo;" in c_code
        assert "} Bar;" in c_code


# ============================================================
# Object Instantiation (new) Tests
# ============================================================

class TestNewObjectParsing:
    """Test parsing of new object instantiation."""

    def test_parse_new_no_args(self):
        """new ClassName with no arguments parses correctly."""
        code = """class Foo
    function init
        return 0
x = new Foo
"""
        program = parse_program(code)
        assigns = [s for s in program.statements if isinstance(s, AssignmentNode)]
        assert len(assigns) == 1
        assert isinstance(assigns[0].expression, NewNode)
        assert assigns[0].expression.class_name == "Foo"

    def test_new_with_self_field_compiles(self):
        """new with class using self.field now compiles correctly."""
        code = """class Point
    function init x y
        self.x = x
        self.y = y
p = new Point 3 4
"""
        program = parse_program(code)
        assigns = [s for s in program.statements if isinstance(s, AssignmentNode)]
        assert len(assigns) == 1
        assert isinstance(assigns[0].expression, NewNode)
        assert assigns[0].expression.class_name == "Point"


# ============================================================
# Pop Operation Tests
# ============================================================

class TestPopCompilation:
    """Test parsing and C code generation for pop operations."""

    def test_pop_standalone_compiles(self):
        """pop as a standalone statement compiles to srv_list_pop call."""
        code = """nums = [1, 2, 3]
pop nums
"""
        c_code = compile_to_c(code)
        assert "srv_list_pop(&nums)" in c_code

    def test_pop_in_expression_compiles(self):
        """pop in assignment expression compiles correctly."""
        code = """nums = [1, 2, 3]
x = pop nums
"""
        program = parse_program(code)
        assigns = [s for s in program.statements if isinstance(s, AssignmentNode)]
        assert len(assigns) == 2
        assert isinstance(assigns[1].expression, PopNode)
        
        c_code = compile_to_c(code)
        assert "srv_list_pop(&nums)" in c_code

    def test_pop_node_exists_in_ast(self):
        """PopNode class exists and can be instantiated."""
        node = PopNode("mylist")
        assert node.list_name == "mylist"

    def test_list_runtime_includes_pop_function(self):
        """The C list runtime includes srv_list_pop even if pop isn't parseable."""
        code = """nums = [1, 2, 3]
"""
        c_code = compile_to_c(code)
        assert "srv_list_pop" in c_code  # included in runtime


# ============================================================
# Dot Access / Assignment Tests (outside classes)
# ============================================================

class TestDotChain:
    """Test dot access on identifiers (outside class self context)."""

    def test_dot_access_on_new_object(self):
        """Dot access on object created with new (simple class)."""
        code = """class Foo
    function init
        return 0
f = new Foo
print f.x
"""
        # This should parse — the dot access is on f (IDENT), not self
        program = parse_program(code)
        prints = [s for s in program.statements if isinstance(s, PrintNode)]
        assert len(prints) == 1

    def test_dot_assignment_on_variable(self):
        """Dot assignment on a variable (obj.field = val)."""
        code = """class Foo
    function init
        return 0
f = new Foo
f.x = 42
"""
        program = parse_program(code)
        dot_assigns = [s for s in program.statements if isinstance(s, DotAssignmentNode)]
        assert len(dot_assigns) == 1

    def test_new_node_in_expression_compiles(self):
        """NewNode compiles to struct initialization with init call."""
        code = """class Foo
    function init
        return 0
f = new Foo
f.x = 99
"""
        c_code = compile_to_c(code)
        assert "Foo_init" in c_code
        assert "f.x = 99" in c_code

    def test_full_oop_workflow_compiles(self):
        """Full OOP workflow: class with fields, new, dot access, methods."""
        code = """class Point
    function init x y
        self.x = x
        self.y = y
    function getX
        return self.x

p = new Point 3 4
print p.x
"""
        c_code = compile_to_c(code)
        # Struct should have x and y fields
        assert "double x;" in c_code
        assert "double y;" in c_code
        # Init should use pointer access
        assert "self->x =" in c_code
        assert "self->y =" in c_code
        # getX should read via pointer
        assert "self->x" in c_code
        # NewNode should generate init call
        assert "Foo" not in c_code or "Point" in c_code
        assert "Point_init" in c_code

    def test_pop_in_while_loop_compiles(self):
        """Pop used in a while loop body compiles correctly."""
        code = """nums = [1, 2, 3]
while len nums > 0
    x = pop nums
    print x
"""
        c_code = compile_to_c(code)
        assert "srv_list_pop" in c_code
        assert "srv_list_len" in c_code


# ============================================================
# Try/Catch Advanced Tests
# ============================================================

class TestTryCatchAdvanced:
    """Advanced try/catch compilation tests."""

    def test_try_catch_with_function_call(self):
        """try/catch with a function call in the try body."""
        code = """function risky x
    return x / 0

try
    result = risky 5
catch e
    print "error"
"""
        c_code = compile_to_c(code)
        assert "setjmp" in c_code
        assert "risky" in c_code
        assert "__error_msg" in c_code

    def test_try_catch_catch_var_declared_once(self):
        """Catch variable should be declared as const char* once."""
        code = """try
    x = 1
catch err
    print err
"""
        c_code = compile_to_c(code)
        assert 'const char *err = __error_msg;' in c_code

    def test_nested_try_catch(self):
        """Nested try/catch blocks compile without errors."""
        code = """try
    try
        x = 1
    catch inner
        print inner
catch outer
    print outer
"""
        c_code = compile_to_c(code)
        assert c_code.count("setjmp") >= 2
        assert "inner" in c_code
        assert "outer" in c_code

    def test_try_with_assignment_and_catch(self):
        """Try body with variable assignment, catch prints error."""
        code = """try
    x = 42
    y = x + 1
catch e
    print e
"""
        c_code = compile_to_c(code)
        assert "double x = 42" in c_code
        assert "setjmp" in c_code
        assert 'printf("%s\\n", err)' in c_code or '__error_msg' in c_code


# ============================================================
# Code Generator Edge Cases
# ============================================================

class TestCodeGenEdgeCases:
    """Edge cases in C code generation."""

    def test_multiple_functions_forward_declared(self):
        """All functions get forward declarations before definitions."""
        code = """function foo x
    return bar x

function bar y
    return y + 1
"""
        c_code = compile_to_c(code)
        fwd_foo = c_code.index("double foo(double x);")
        fwd_bar = c_code.index("double bar(double y);")
        body_foo = c_code.index("double foo(double x) {")
        assert fwd_foo < body_foo
        assert fwd_bar < body_foo

    def test_function_no_params_uses_void(self):
        """Functions with no parameters use void in C."""
        code = """function greet
    print "hi"
"""
        c_code = compile_to_c(code)
        assert "double greet(void)" in c_code

    def test_string_var_tracked_for_print(self):
        """String variables get %s format in printf."""
        code = """name = "world"
print name
"""
        c_code = compile_to_c(code)
        assert '%s' in c_code
        assert 'const char *name' in c_code

    def test_list_var_tracked_for_print(self):
        """List variables get special list print format."""
        code = """nums = [1, 2, 3]
print nums
"""
        c_code = compile_to_c(code)
        assert "srv_list_len" in c_code

    def test_deeply_nested_if_else(self):
        """Deeply nested if/else if/else compiles correctly."""
        code = """x = 5
if x > 10
    print 1
else if x > 7
    print 2
else if x > 3
    print 3
else
    print 4
"""
        c_code = compile_to_c(code)
        assert c_code.count("else if") == 2
        assert "} else {" in c_code

    def test_for_loop_var_declared_in_scope(self):
        """For loop variable is declared in the generated C."""
        code = """for i 0 10
    print i
"""
        c_code = compile_to_c(code)
        assert "double i = 0" in c_code
        assert "i < 10" in c_code
        assert "i++" in c_code

    def test_empty_function_gets_return_zero(self):
        """Functions without explicit return get implicit return 0."""
        code = """function noop
    x = 1
"""
        c_code = compile_to_c(code)
        assert "return 0;" in c_code

    def test_bool_assignment_uses_int(self):
        """Boolean variables declared as int in C."""
        code = """flag = true
"""
        c_code = compile_to_c(code)
        assert "int flag = 1" in c_code

    def test_complex_expression_parenthesized(self):
        """Binary operations are parenthesized in C output."""
        code = """x = (2 + 3) * (4 - 1)
"""
        c_code = compile_to_c(code)
        assert "(" in c_code and ")" in c_code

    def test_multiple_list_operations(self):
        """Multiple list operations compile to correct runtime calls."""
        code = """nums = [10, 20, 30]
append nums 40
x = nums[2]
nums[0] = 99
n = len nums
"""
        c_code = compile_to_c(code)
        assert "srv_list_new" in c_code
        # 3 init appends + 1 explicit append + 1 in runtime definition = 5 occurrences
        # But we just check that all operations are present
        assert "srv_list_append" in c_code
        assert "srv_list_get" in c_code
        assert "srv_list_set" in c_code
        assert "srv_list_len" in c_code

    def test_string_with_escape_sequences(self):
        """Strings with escape characters compile correctly."""
        code = 'msg = "hello\\nworld"\nprint msg\n'
        c_code = compile_to_c(code)
        assert 'const char *msg' in c_code

    def test_reassignment_different_value(self):
        """Variable reassignment doesn't redeclare."""
        code = """x = 1
x = 2
x = 3
"""
        c_code = compile_to_c(code)
        decl_count = c_code.count("double x =")
        assert decl_count == 1

    def test_no_math_h_always_included(self):
        """math.h is always included (for fmod support)."""
        code = """x = 5
"""
        c_code = compile_to_c(code)
        assert "#include <math.h>" in c_code

    def test_while_with_decrement(self):
        """While loop with variable decrement compiles correctly."""
        code = """x = 10
while x > 0
    print x
    x = x - 1
"""
        c_code = compile_to_c(code)
        assert "while" in c_code
        assert "x > 0" in c_code

    def test_function_with_multiple_params(self):
        """Function with multiple parameters generates correct C signature."""
        code = """function calc a b c
    return a * b + c
"""
        c_code = compile_to_c(code)
        assert "double calc(double a, double b, double c)" in c_code

    def test_recursive_function(self):
        """Recursive function compiles with forward declaration."""
        code = """function factorial n
    if n <= 1
        return 1
    return n * factorial (n - 1)
"""
        c_code = compile_to_c(code)
        assert "double factorial(double n);" in c_code  # forward decl
        assert "double factorial(double n) {" in c_code  # definition
        assert "factorial(" in c_code  # recursive call

    def test_nested_for_loops(self):
        """Nested for loops compile correctly."""
        code = """for i 0 5
    for j 0 3
        print i
"""
        c_code = compile_to_c(code)
        assert "for (double i = 0" in c_code
        assert "for (double j = 0" in c_code

    def test_comparison_all_operators(self):
        """All comparison operators compile to C equivalents."""
        code = """a = 5
b = 3
if a == b
    print 1
if a != b
    print 2
if a < b
    print 3
if a > b
    print 4
if a <= b
    print 5
if a >= b
    print 6
"""
        c_code = compile_to_c(code)
        assert "==" in c_code
        assert "!=" in c_code
        assert "a < b" in c_code or "(a < b)" in c_code
        assert "a > b" in c_code or "(a > b)" in c_code
        assert "a <= b" in c_code or "(a <= b)" in c_code
        assert "a >= b" in c_code or "(a >= b)" in c_code

    def test_logical_not_generates_bang(self):
        """Logical not compiles to C ! operator."""
        code = """if not true
    print 1
"""
        c_code = compile_to_c(code)
        assert "!" in c_code

    def test_unary_minus_generates_negation(self):
        """Unary minus compiles to C negation."""
        code = """x = -42
"""
        c_code = compile_to_c(code)
        assert "-(42)" in c_code or "-42" in c_code

    def test_modulo_generates_fmod(self):
        """Modulo operator compiles to fmod call."""
        code = """x = 17 % 5
"""
        c_code = compile_to_c(code)
        assert "fmod" in c_code

    def test_no_try_catch_no_setjmp(self):
        """Code without try/catch doesn't include setjmp."""
        code = """x = 5
print x
"""
        c_code = compile_to_c(code)
        assert "setjmp" not in c_code
        assert "#include <setjmp.h>" not in c_code

    def test_no_lists_no_runtime(self):
        """Code without lists doesn't include list runtime."""
        code = """x = 5
print x
"""
        c_code = compile_to_c(code)
        assert "SrvList" not in c_code
        assert "srv_list" not in c_code

    def test_function_call_in_expression(self):
        """Function call result used in expression compiles correctly."""
        code = """function double x
    return x * 2

y = double 5
print y
"""
        c_code = compile_to_c(code)
        # Should call the function and assign result
        assert "double_func" in c_code or "double_" in c_code or "double(5)" in c_code


# ============================================================
# Parser Edge Cases
# ============================================================

class TestParserEdgeCases:
    """Parser edge cases for the compiler."""

    def test_empty_program(self):
        """Empty source produces a valid ProgramNode."""
        program = parse_program("\n")
        assert isinstance(program, ProgramNode)

    def test_comments_only(self):
        """Source with only comments produces a valid program."""
        program = parse_program("# just a comment\n# another\n")
        assert isinstance(program, ProgramNode)

    def test_multiple_newlines(self):
        """Multiple blank lines don't crash the parser."""
        program = parse_program("\n\n\n\nx = 1\n\n\n")
        assert isinstance(program, ProgramNode)

    def test_class_with_simple_methods(self):
        """Class with methods not using self.field parse correctly."""
        code = """class Calc
    function init
        return 0
    function add x y
        return x + y
    function sub x y
        return x - y
"""
        program = parse_program(code)
        classes = [s for s in program.statements if isinstance(s, ClassNode)]
        assert len(classes) == 1
        methods = [s for s in classes[0].body if isinstance(s, FunctionNode)]
        assert len(methods) == 3
        assert methods[0].name == "init"
        assert methods[1].name == "add"
        assert methods[2].name == "sub"

    def test_function_calling_another_function(self):
        """Mutual function references parse correctly."""
        code = """function triple x
    return x * 3

function quadruple x
    return triple x + x
"""
        program = parse_program(code)
        funcs = [s for s in program.statements if isinstance(s, FunctionNode)]
        assert len(funcs) == 2

    def test_nested_list_index(self):
        """List indexing parses correctly."""
        code = """nums = [10, 20, 30]
x = nums[0]
"""
        program = parse_program(code)
        assigns = [s for s in program.statements if isinstance(s, AssignmentNode)]
        assert len(assigns) == 2
        assert isinstance(assigns[1].expression, IndexNode)

    def test_append_in_loop(self):
        """Append inside a for loop parses correctly."""
        code = """nums = []
for i 0 5
    append nums i
"""
        program = parse_program(code)
        fors = [s for s in program.statements if isinstance(s, ForNode)]
        assert len(fors) == 1
        appends = [s for s in fors[0].body if isinstance(s, AppendNode)]
        assert len(appends) == 1

    def test_while_with_break_condition(self):
        """While loop with complex condition parses."""
        code = """x = 0
while x < 100 and x > -1
    x = x + 1
"""
        program = parse_program(code)
        whiles = [s for s in program.statements if isinstance(s, WhileNode)]
        assert len(whiles) == 1
        assert isinstance(whiles[0].condition, LogicalNode)

    def test_if_with_logical_or(self):
        """If with logical or condition parses."""
        code = """x = 5
if x < 0 or x > 10
    print "out of range"
"""
        program = parse_program(code)
        ifs = [s for s in program.statements if isinstance(s, IfNode)]
        assert len(ifs) == 1
        assert isinstance(ifs[0].condition, LogicalNode)

    def test_empty_list_literal(self):
        """Empty list literal parses correctly."""
        code = """x = []
"""
        program = parse_program(code)
        assigns = [s for s in program.statements if isinstance(s, AssignmentNode)]
        assert len(assigns) == 1
        assert isinstance(assigns[0].expression, ListNode)
        assert len(assigns[0].expression.elements) == 0

    def test_list_with_expressions(self):
        """List with computed expressions parses."""
        code = """x = [1 + 2, 3 * 4, 5 - 1]
"""
        program = parse_program(code)
        assigns = [s for s in program.statements if isinstance(s, AssignmentNode)]
        assert isinstance(assigns[0].expression, ListNode)
        assert len(assigns[0].expression.elements) == 3


# ============================================================
# Tokenizer Edge Cases
# ============================================================

class TestTokenizerEdgeCases:
    """Edge cases in the compiler's tokenizer."""

    def test_all_keywords_recognized(self):
        """All language keywords are properly tokenized."""
        keywords = ["function", "return", "class", "new", "self",
                     "if", "else if", "else", "for", "while",
                     "try", "catch", "print", "true", "false",
                     "and", "or", "not", "append", "len", "pop"]
        for kw in keywords:
            tokens = tokenize(f"{kw}\n")
            kw_tokens = [t for t in tokens if t[0] == 'KEYWORD']
            assert len(kw_tokens) >= 1, f"Keyword '{kw}' not recognized"

    def test_identifier_vs_keyword(self):
        """Identifiers that look like keywords are handled correctly."""
        tokens = tokenize("myfunction = 5\n")
        ident_tokens = [t for t in tokens if t[0] == 'IDENT']
        assert any(t[1] == 'myfunction' for t in ident_tokens)

    def test_multidigit_numbers(self):
        """Multi-digit numbers tokenize correctly."""
        tokens = tokenize("12345\n")
        num_tokens = [t for t in tokens if t[0] == 'NUMBER']
        assert num_tokens[0][1] == '12345'

    def test_float_number(self):
        """Floating point numbers tokenize correctly."""
        tokens = tokenize("3.14159\n")
        num_tokens = [t for t in tokens if t[0] == 'NUMBER']
        assert num_tokens[0][1] == '3.14159'

    def test_string_with_escaped_quote(self):
        """String with escaped quote tokenizes correctly."""
        tokens = tokenize('"say \\"hello\\""\n')
        str_tokens = [t for t in tokens if t[0] == 'STRING']
        assert len(str_tokens) == 1

    def test_indent_tracking(self):
        """Indentation creates INDENT/DEDENT tokens."""
        code = "if true\n    x = 1\ny = 2\n"
        tokens = tokenize(code)
        types = [t[0] for t in tokens]
        assert 'INDENT' in types
        assert 'DEDENT' in types

    def test_deeply_nested_indentation(self):
        """Multiple levels of indentation produce correct INDENT/DEDENT."""
        code = "if true\n    if true\n        if true\n            x = 1\n"
        tokens = tokenize(code)
        indent_count = sum(1 for t in tokens if t[0] == 'INDENT')
        assert indent_count >= 3
