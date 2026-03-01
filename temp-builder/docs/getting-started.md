# Getting Started

This guide walks you through installing and running sauravcode in under 5 minutes.

## Prerequisites

| Requirement | Purpose | Install |
|------------|---------|---------|
| **Python 3.6+** | Run the interpreter and compiler | [python.org](https://python.org) |
| **gcc** | Compile native executables | See below |

### Installing gcc

=== "macOS"

    ```bash
    xcode-select --install
    ```

=== "Ubuntu/Debian"

    ```bash
    sudo apt install build-essential
    ```

=== "Windows"

    Install [MinGW-w64](https://www.mingw-w64.org/) or use WSL.

## Installation

Clone the repository:

```bash
git clone https://github.com/sauravbhattacharya001/sauravcode.git
cd sauravcode
```

That's it — no build step, no dependencies. The interpreter and compiler are single Python files.

## Running Your First Program

### Using the Interpreter

The interpreter (`saurav.py`) evaluates code directly:

```bash
python saurav.py hello.srv
```

Output:

```
Hello, World!
```

### Using the Compiler

The compiler (`sauravcc.py`) generates C code and compiles it to a native executable:

```bash
python sauravcc.py hello.srv
```

This produces a `hello` executable (or `hello.exe` on Windows) and runs it.

### Compiler Options

| Flag | Description |
|------|-------------|
| `--emit-c` | Print generated C code to stdout (don't compile) |
| `--keep-c` | Keep the intermediate `.c` file |
| `-o NAME` | Set the output executable name |
| `-v` | Verbose — show compilation steps |

```bash
# See what C code the compiler generates
python sauravcc.py hello.srv --emit-c

# Compile with a custom name
python sauravcc.py hello.srv -o myprogram
```

## Verify Installation

Run the comprehensive test suite to verify everything works:

=== "Interpreter"

    ```bash
    python saurav.py test_all.srv
    ```

=== "Compiler"

    ```bash
    python sauravcc.py test_all.srv
    ```

Both should produce identical output covering all language features (functions, loops, classes, lists, error handling, etc.).

## Next Steps

- [Your First Program](first-program.md) — write a real sauravcode program from scratch
- [Language Reference](language.md) — the complete language specification
- [Examples](examples.md) — annotated programs covering all features
