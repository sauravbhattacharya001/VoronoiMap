# Changelog

All notable changes to sauravcode will be documented in this file.

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
