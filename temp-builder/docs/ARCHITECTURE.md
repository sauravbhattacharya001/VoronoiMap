# Sauravcode Architecture

> How the interpreter and compiler work under the hood.

## Overview

Sauravcode has two execution paths:

```
                     ┌─────────────┐
    .srv file ──────►│  Tokenizer  │
                     └──────┬──────┘
                            │ tokens
                     ┌──────▼──────┐
                     │   Parser    │
                     └──────┬──────┘
                            │ AST
                 ┌──────────┴──────────┐
                 │                     │
          ┌──────▼──────┐      ┌──────▼──────┐
          │ Interpreter │      │ C Code Gen  │
          │ (saurav.py) │      │(sauravcc.py)│
          └──────┬──────┘      └──────┬──────┘
                 │                     │ .c file
             output               ┌───▼───┐
                                  │  gcc   │
                                  └───┬───┘
                                      │
                                  executable
```

Both share the same tokenization and parsing stages but diverge at execution:
- **`saurav.py`** — tree-walk interpreter (evaluates the AST directly)
- **`sauravcc.py`** — compiles AST → C source → native binary via gcc

## Pipeline Stages

### 1. Tokenizer

**Input:** Raw source code (string)  
**Output:** List of tokens with type, value, line number, and column

The tokenizer is regex-based using Python's `re.finditer` with named groups. Token types are matched in priority order (longer patterns first — e.g., `==` before `=`).

**Token types:**

| Token       | Pattern                  | Example        |
|------------|--------------------------|----------------|
| `COMMENT`  | `#.*`                    | `# a comment`  |
| `NUMBER`   | `\d+(\.\d*)?`            | `42`, `3.14`   |
| `STRING`   | `"(?:[^"\\]|\\.)*"`      | `"hello"`      |
| `EQ`       | `==`                     | `==`           |
| `NEQ`      | `!=`                     | `!=`           |
| `LTE`      | `<=`                     | `<=`           |
| `GTE`      | `>=`                     | `>=`           |
| `LT`       | `<`                      | `<`            |
| `GT`       | `>`                      | `>`            |
| `ASSIGN`   | `=`                      | `=`            |
| `OP`       | `[+\-*/%]`               | `+`, `-`, `*`  |
| `LPAREN`   | `\(`                     | `(`            |
| `RPAREN`   | `\)`                     | `)`            |
| `LBRACKET` | `\[`                     | `[`            |
| `RBRACKET` | `\]`                     | `]`            |
| `DOT`      | `\.`                     | `.`            |
| `COMMA`    | `,`                      | `,`            |
| `KEYWORD`  | Reserved words           | `function`     |
| `IDENT`    | `[a-zA-Z_]\w*`           | `myVar`        |
| `NEWLINE`  | `\n`                     | newline        |

**Indentation handling:** After each newline, the tokenizer measures the indentation of the next line and emits synthetic `INDENT` / `DEDENT` tokens when the level changes. This converts whitespace-significant blocks into explicit tokens for the parser.

```
function add x y        # KEYWORD IDENT IDENT IDENT NEWLINE
    return x + y        # INDENT KEYWORD IDENT OP IDENT NEWLINE DEDENT
```

### 2. Parser

**Input:** Token list  
**Output:** Abstract Syntax Tree (AST)

The parser is a recursive-descent parser that produces an AST from the token stream. It handles:

- **Statement parsing** — dispatches based on the first token (keyword or identifier)
- **Expression parsing** — uses precedence climbing with these levels:
  1. `or` (lowest)
  2. `and`
  3. Comparison (`==`, `!=`, `<`, `>`, `<=`, `>=`)
  4. Addition/Subtraction (`+`, `-`)
  5. Multiplication/Division/Modulo (`*`, `/`, `%`)
  6. Unary (`not`, `-`)
  7. Postfix (`[]` indexing, `.` access)
  8. Atoms (literals, identifiers, parenthesized expressions)

**AST Node Types:**

| Category    | Node                    | Description                            |
|------------|-------------------------|----------------------------------------|
| Structure  | `ProgramNode`           | Root — list of statements              |
|            | `FunctionNode`          | Function definition                    |
|            | `ClassNode`             | Class definition                       |
| Statements | `AssignmentNode`        | `x = expr`                             |
|            | `IndexedAssignmentNode` | `list[i] = expr`                       |
|            | `DotAssignmentNode`     | `obj.field = expr`                     |
|            | `ReturnNode`            | `return expr`                          |
|            | `PrintNode`             | `print expr`                           |
|            | `AppendNode`            | `append list value`                    |
| Control    | `IfNode`                | If/else-if/else chain                  |
|            | `WhileNode`             | While loop                             |
|            | `ForNode`               | Range-based for loop                   |
|            | `TryCatchNode`          | Try/catch block                        |
| Expressions| `BinaryOpNode`          | `left op right`                        |
|            | `UnaryOpNode`           | `not x`, `-x`                          |
|            | `CompareNode`           | `left cmp right`                       |
|            | `LogicalNode`           | `left and/or right`                    |
|            | `NumberNode`            | Numeric literal                        |
|            | `StringNode`            | String literal                         |
|            | `BoolNode`              | `true` / `false`                       |
|            | `IdentifierNode`        | Variable reference                     |
|            | `FunctionCallNode`      | `func arg1 arg2`                       |
|            | `ListNode`              | `[1, 2, 3]`                            |
|            | `IndexNode`             | `list[i]`                              |
|            | `DotAccessNode`         | `obj.field`                            |
|            | `MethodCallNode`        | `obj.method args`                      |
|            | `NewNode`               | `new ClassName`                        |
|            | `LenNode`               | `len list`                             |
|            | `PopNode`               | `pop list`                             |

### 3a. Interpreter (`saurav.py`)

The interpreter is a tree-walk evaluator that traverses the AST and executes each node directly:

- **Functions** are stored in a dictionary and looked up on call
- **Variables** use a flat dictionary with save/restore on function calls (creating local scope)
- **Output formatting** — integers print without decimal points (`42` not `42.0`)

### 3b. Compiler (`sauravcc.py`)

The compiler translates the AST to C source code, then invokes `gcc` to produce a native executable.

**Compilation strategy:**

| Sauravcode Feature | C Implementation                        |
|-------------------|------------------------------------------|
| Functions          | C functions (`double` return type)       |
| Variables          | `double` local variables                 |
| Strings            | `const char*` / string literals          |
| Booleans           | `double` (1.0 = true, 0.0 = false)      |
| Lists              | Custom `SrvList` struct (dynamic array)  |
| Classes            | C `struct` + functions with struct ptr   |
| Try/Catch          | `setjmp` / `longjmp`                     |
| Print              | Type-detecting printf (int vs float vs string) |

**Code generation features:**

- **Feature scanning:** Pre-scans the AST to detect which runtime features are needed (lists, strings, try/catch) and only emits the relevant runtime code
- **Forward declarations:** All functions get forward-declared before definitions
- **Type tracking:** Tracks which variables hold strings vs numbers vs lists to generate correct C code
- **Integer detection:** Numbers without fractional parts print as integers

**Compiler CLI:**

```bash
python sauravcc.py program.srv              # Compile and run
python sauravcc.py program.srv --emit-c     # Output C code only
python sauravcc.py program.srv -o output    # Custom output name
python sauravcc.py program.srv --keep-c     # Keep .c file
python sauravcc.py program.srv -v           # Verbose mode
```

## Known Limitations

1. **Expression-as-argument ambiguity:** `f n - 1` parses as `f(n) - 1`, not `f(n-1)`. Use parentheses: `f (n - 1)`.

2. **Single return type:** The compiler uses `double` for all numeric values. Mixed type returns (number vs string) in the same function can cause issues.

3. **No garbage collection:** The compiled C code uses `malloc` for lists but never calls `free`. For short-running programs this is fine; long-running programs may leak memory.

4. **No standard library:** Beyond `print`, `len`, `append`, `pop`, and `get`, there are no built-in functions for I/O, math, or string manipulation.

5. **No imports/modules:** All code must be in a single `.srv` file.

## File Structure

```
sauravcode/
├── saurav.py          # Interpreter (tree-walk)
├── sauravcc.py        # Compiler (AST → C → gcc)
├── hello.srv          # Hello World example
├── a.srv              # Basic function test
├── test.srv           # Function/call tests
├── test_all.srv       # Comprehensive feature test
├── docs/
│   ├── LANGUAGE.md    # Language specification
│   └── ARCHITECTURE.md # This file
├── .github/
│   └── workflows/     # CI and automation
└── LICENSE            # MIT License
```

---

*For the language specification, see [LANGUAGE.md](LANGUAGE.md).*
