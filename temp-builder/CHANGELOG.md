# Changelog

All notable changes to sauravcode will be documented in this file.

## [2.2.0] - 2026-02-15

### Maps (Dictionaries)
- **New data type: maps** — key-value dictionaries with `{ key: value }` literal syntax
- Bracket access for reading: `m["key"]` and writing: `m["key"] = value`
- Supports string, number, and boolean keys; any value type including nested maps and lists
- Empty maps: `{}`
- **3 new built-in functions:** `keys` (get list of keys), `values` (get list of values), `has_key` (check key existence)
- Extended existing builtins: `len` works on maps, `type_of` returns `"map"`, `contains` checks map keys, `to_string` formats maps
- Clean printing: `{"name": "Alice", "age": 30}` (human-readable, consistent formatting)
- **Indexed assignment node** — fixed `list[i] = val` and `map[key] = val` to use proper `IndexedAssignmentNode` (was previously a simplified `AssignmentNode` that lost index info)
- `map_demo.srv` — comprehensive demo showing map creation, access, modification, iteration, word frequency counting, nested maps
- 49 new tests covering map literals, access, assignment, builtins, control flow, formatting, error cases, and indexed assignment for both lists and maps

## [2.1.0] - 2026-02-14

### Standard Library
- **27 built-in functions** added to the interpreter — no imports needed, just use them
- **String functions:** `upper`, `lower`, `trim`, `replace`, `split`, `join`, `contains`, `starts_with`, `ends_with`, `substring`, `index_of`, `char_at`
- **Math functions:** `abs`, `round` (with optional decimal places), `floor`, `ceil`, `sqrt`, `power`
- **Utility functions:** `type_of`, `to_string`, `to_number`, `input` (read from stdin), `range` (1-3 args), `reverse`, `sort`
- User-defined functions override builtins (so you can customize any built-in)
- Full type checking with helpful error messages
- REPL `builtins` command to list all built-in functions with usage
- `stdlib_demo.srv` — comprehensive demo of all standard library functions
- 49 new tests covering all builtins, edge cases, and error conditions

## [2.0.0] - 2026-02-14

### Compiler (sauravcc v2)
- **Full-featured compiler** (`sauravcc.py`) — Compiles `.srv` → C → native executable via gcc
- All language features supported: functions, recursion, lists, booleans, if/else if/else, while, for, classes, try/catch, strings, logical ops, modulo, parenthesized expressions
- Proper operator precedence with recursive descent parser
- Dynamic list runtime (SrvList) with bounds checking
- Try/catch via setjmp/longjmp in generated C code
- Class compilation to C structs with method dispatch
- CLI: `--emit-c`, `-o`, `--keep-c`, `--cc`, `-v` flags

### Documentation
- `docs/LANGUAGE.md` — Complete language reference with EBNF grammar
- `docs/ARCHITECTURE.md` — Compiler and interpreter architecture documentation
- `docs/EXAMPLES.md` — Annotated example programs
- GitHub Pages site with interactive documentation
- Professional README with badges, feature list, and usage examples
- `CHANGELOG.md` — This file

### DevOps
- CodeQL security scanning workflow
- Auto-labeler and stale bot workflows
- `.gitignore` for build artifacts

### Interpreter Improvements
- Reordered tokenizer: `EQ` before `ASSIGN` (fixes `==` parsing)
- Added `print`, string literals, division-by-zero error handling
- Replaced debug `print()` calls with `--debug` flag
- Refactored parser: proper AST nodes instead of codegen hacks

## [1.0.0] - 2025

### Features
- **Interpreter** (`saurav.py`) — Tree-walk interpreter for `.srv` files
- Functions with recursion
- Variables and assignment (dynamic typing)
- Arithmetic: `+`, `-`, `*`, `/`
- Basic control flow
- Nested function calls
