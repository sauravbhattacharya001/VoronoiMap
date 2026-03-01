# Contributing to sauravcode

Thanks for your interest in contributing to sauravcode! Whether you're fixing a bug, adding a language feature, improving docs, or writing tests, every contribution helps.

## Getting Started

### Prerequisites

- **Python 3.9+** (for the interpreter and compiler)
- **gcc** (for the compiler to produce native executables)
- **pytest** and **pytest-cov** (for running tests)

### Setup

```bash
# Clone the repo
git clone https://github.com/sauravbhattacharya001/sauravcode.git
cd sauravcode

# Install test dependencies
pip install pytest pytest-cov

# Verify everything works
python -m pytest tests/ -v
```

### Project Structure

```
sauravcode/
├── saurav.py          # Tree-walk interpreter
├── sauravcc.py        # Compiler (.srv → C → native executable)
├── tests/
│   ├── test_interpreter.py   # Interpreter tests
│   └── test_compiler.py      # Compiler/codegen tests
├── docs/                     # MkDocs documentation source
├── *.srv                     # Example sauravcode programs
├── pyproject.toml            # pytest + coverage config
├── .codecov.yml              # Codecov settings
└── .github/workflows/        # CI/CD pipelines
```

## How to Contribute

### 1. Find Something to Work On

- **Issues** — Check [open issues](https://github.com/sauravbhattacharya001/sauravcode/issues) for bugs or feature requests
- **Tests** — Increase coverage (currently ~85%). Look at `Missing` lines in coverage reports
- **Language features** — Propose new syntax via an issue first
- **Docs** — Improve examples, fix typos, or add tutorials

### 2. Create a Branch

```bash
git checkout -b feature/your-feature-name
```

Use descriptive branch names:
- `fix/division-by-zero-message`
- `feature/string-interpolation`
- `docs/add-list-examples`
- `test/compiler-edge-cases`

### 3. Make Your Changes

#### Working on the Interpreter (`saurav.py`)

The interpreter has three stages:
1. **Tokenizer** (`tokenize()`) — converts source code to tokens
2. **Parser** (`Parser` class) — builds an AST from tokens
3. **Interpreter** (`Interpreter` class) — walks the AST and executes

#### Working on the Compiler (`sauravcc.py`)

The compiler shares tokenizer and parser logic but adds:
4. **C Code Generator** (`CCodeGenerator`) — converts AST to C source
5. **Build step** — invokes gcc to produce a native binary

#### Key Design Principles

- **No semicolons, no parentheses, no commas** in sauravcode syntax
- Indentation-based blocks (like Python)
- Function arguments are space-separated: `add 3 5` not `add(3, 5)`
- Keep the language minimal and readable

### 4. Write Tests

Every change should include tests. We use **pytest** with organized test classes:

```python
# In tests/test_interpreter.py or tests/test_compiler.py

class TestYourFeature:
    def test_basic_case(self):
        output = run_code("your sauravcode here\n")
        assert output.strip() == "expected output"

    def test_error_case(self):
        with pytest.raises(RuntimeError, match="expected error"):
            run_code("bad code\n")
```

Run tests with coverage:

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage report
python -m pytest tests/ --cov=. --cov-report=term-missing

# Run a specific test file
python -m pytest tests/test_interpreter.py -v

# Run a specific test
python -m pytest tests/test_interpreter.py::TestArithmetic::test_addition -v
```

**Coverage requirement:** Don't decrease the overall coverage percentage. New features should have corresponding tests.

### 5. Test with .srv Files

Create or update `.srv` example files to exercise your changes:

```
# my_feature.srv
function example x
    return x + 1

print example 5
```

Test with both interpreter and compiler:

```bash
# Interpreter
python saurav.py my_feature.srv

# Compiler
python sauravcc.py my_feature.srv
```

### 6. Submit a Pull Request

1. Push your branch: `git push origin feature/your-feature-name`
2. Open a PR against `main`
3. Describe what you changed and why
4. Reference any related issues (`Fixes #123`)
5. CI will run tests automatically — make sure they pass

## Code Style

- **Python**: Follow PEP 8 generally, but match existing patterns in the codebase
- **Naming**: `snake_case` for functions/variables, `PascalCase` for AST node classes
- **Comments**: Add them for non-obvious logic, especially in the parser and code generator
- **Keep it simple**: sauravcode values clarity — the implementation should too

## Reporting Bugs

Open an issue with:
1. **Sauravcode program** that triggers the bug (minimal reproducible example)
2. **Expected behavior** vs **actual behavior**
3. Whether it affects the interpreter, compiler, or both
4. Python version and OS

## Proposing Language Features

sauravcode is intentionally minimal. Before implementing a new language feature:

1. **Open an issue** describing the proposed syntax and semantics
2. Explain the **motivation** — what problem does it solve?
3. Show **example code** using the new feature
4. Wait for discussion before starting implementation

## License

By contributing, you agree that your contributions will be licensed under the same [MIT License](LICENSE) that covers the project.
