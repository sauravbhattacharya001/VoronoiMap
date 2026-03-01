# Copilot Instructions for sauravcode

## Project Overview

sauravcode is a custom programming language with minimal syntax — no parentheses for function calls, no commas, no semicolons, no braces. The project has two main components:

- **`saurav.py`** — Tree-walk interpreter that directly evaluates `.srv` source files
- **`sauravcc.py`** — Compiler that translates `.srv` → C → native executable via gcc

Both share a common language but have independent tokenizer/parser implementations.

## Architecture

### Interpreter (`saurav.py`, ~870 lines)
1. **Tokenizer** — regex-based, emits INDENT/DEDENT tokens for block structure
2. **Parser** — recursive descent, produces AST nodes (AssignmentNode, FunctionDef, IfNode, etc.)
3. **Evaluator** — walks the AST, maintains variable scopes and function definitions

### Compiler (`sauravcc.py`, ~1300 lines)
1. **Tokenizer** — same regex approach as interpreter
2. **Parser** — recursive descent with operator precedence for expressions
3. **C Code Generator** — emits C source with:
   - `SrvList` struct for dynamic arrays with bounds checking
   - `setjmp`/`longjmp` for try/catch
   - C structs for classes
   - Type-detecting `print` via tagged unions or type inference
4. **gcc invocation** — compiles generated C to native binary

### Key Design Decisions
- Indentation-based blocks (Python-style) via INDENT/DEDENT tokens
- Expression-as-argument ambiguity: `f n - 1` parses as `f(n) - 1`, use `f (n - 1)` for nested
- Dynamic typing in interpreter; compiler infers or defaults to `double` for numbers
- Lists are implemented as dynamic arrays (realloc-based) in compiled output

## Language Syntax

```
function add x y
    return x + y

x = add 3 5          # No parens, no commas
print x               # Prints 8

nums = [10, 20, 30]   # Lists use commas + brackets
append nums 40
print len nums         # 4

for i 1 6              # Range-based for
    print i

if x > 5
    print "big"
else
    print "small"
```

## How to Test

```bash
# Run interpreter tests
python saurav.py test_all.srv

# Run compiler tests
python sauravcc.py test_all.srv

# Test individual examples
python saurav.py hello.srv
python sauravcc.py hello.srv
python saurav.py a.srv
python sauravcc.py a.srv
```

Both interpreter and compiler should produce identical output for all `.srv` files.

## Conventions

- Source files use `.srv` extension
- Keywords: `function`, `return`, `class`, `if`, `else if`, `else`, `for`, `while`, `try`, `catch`, `print`, `true`, `false`, `and`, `or`, `not`, `append`, `len`, `self`
- Indentation is 4 spaces (tabs normalized to 4 spaces)
- No formal test framework — `test_all.srv` is the comprehensive test suite
- Changes should be tested against both interpreter AND compiler

## Important Files

| File | Purpose |
|------|---------|
| `saurav.py` | Interpreter |
| `sauravcc.py` | Compiler |
| `test_all.srv` | Full feature test suite |
| `hello.srv` | Hello world example |
| `a.srv` | Function composition example |
| `docs/LANGUAGE.md` | EBNF grammar and language spec |
| `docs/ARCHITECTURE.md` | Internal architecture docs |

## Common Pitfalls

1. **Tokenizer ordering matters** — `==` must be matched before `=`, `<=` before `<`
2. **INDENT/DEDENT generation** — must track indent level stack correctly
3. **Function call ambiguity** — `f a + b` means `f(a) + b`, not `f(a + b)`
4. **Compiler C output** — generated C must compile with standard gcc (C99+)
5. **Both paths must agree** — any language change needs updates to BOTH `saurav.py` and `sauravcc.py`
