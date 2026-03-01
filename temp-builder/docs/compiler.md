# Compiler Guide

The sauravcode compiler (`sauravcc.py`) translates `.srv` source files into native executables by generating C code and invoking `gcc`.

## Compilation Pipeline

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌────────────┐
│ .srv     │───▶│ Tokenize │───▶│  Parse   │───▶│ Generate │───▶│   gcc      │
│ source   │    │ (lexer)  │    │  (AST)   │    │  (C code)│    │ (native)   │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └────────────┘
```

## Usage

```bash
# Compile and run immediately
python sauravcc.py program.srv

# Just output the generated C code
python sauravcc.py program.srv --emit-c

# Compile with a custom output name
python sauravcc.py program.srv -o myprogram

# Keep the intermediate .c file for inspection
python sauravcc.py program.srv --keep-c

# Verbose mode — shows gcc invocation
python sauravcc.py program.srv -v
```

## How Features Map to C

The compiler translates sauravcode constructs to idiomatic C:

| Sauravcode | C Implementation |
|------------|-----------------|
| Functions | C functions returning `double` |
| Variables | Local `double` or `const char*` variables |
| Strings | `const char*` / string literals |
| Booleans | `double` (1.0 = true, 0.0 = false) |
| Lists | `SrvList` struct (dynamic array with bounds checking) |
| Classes | C `struct` + associated functions taking struct pointer |
| Try/Catch | `setjmp` / `longjmp` |
| Print | Type-detecting `printf` (formats ints without `.0`) |

## Smart Code Generation

The compiler is smart about what runtime code it emits:

- **Feature scanning:** Pre-scans the AST to detect which features are used (lists, strings, try/catch)
- **Minimal runtime:** Only includes the runtime support code for features actually used
- **Forward declarations:** All functions are forward-declared before definitions
- **Type tracking:** Tracks whether variables hold strings, numbers, or lists to generate correct C
- **Integer detection:** Numbers without fractional parts print as integers (`42` not `42.0`)

## Inspecting Generated Code

Use `--emit-c` to see what the compiler produces:

```bash
python sauravcc.py hello.srv --emit-c
```

For the simple "Hello, World!" program, this generates:

```c
#include <stdio.h>

int main(void) {
    printf("%s\n", "Hello, World!");
    return 0;
}
```

For more complex programs with lists, classes, or error handling, the compiler includes the necessary runtime structs and support functions.

## Feature Support Matrix

| Feature | Interpreter | Compiler |
|---------|:-----------:|:--------:|
| Functions & recursion | ✅ | ✅ |
| Variables & assignment | ✅ | ✅ |
| Arithmetic (+, -, *, /, %) | ✅ | ✅ |
| Comparisons | ✅ | ✅ |
| Booleans & logical ops | ✅ | ✅ |
| If / else if / else | ✅ | ✅ |
| While loops | ✅ | ✅ |
| For loops (range-based) | ✅ | ✅ |
| Strings | ✅ | ✅ |
| Lists (dynamic arrays) | ✅ | ✅ |
| Classes | ✅ | ✅ |
| Try / catch | ✅ | ✅ |

## Known Limitations

!!! warning "Memory Management"
    The compiled C code uses `malloc` for lists but does not call `free`. Short-running programs are fine; long-running programs may leak memory.

!!! info "Single Return Type"
    The compiler uses `double` for all numeric values. Functions that return different types (number in one branch, string in another) may cause issues.

## Tips

- Use `--emit-c` during development to understand what your code compiles to
- The interpreter and compiler should produce identical output — if they differ, it's a bug
- Parentheses in sauravcode (`f (n - 1)`) are crucial for correct compilation of recursive calls
