---
hide:
  - navigation
---

# sauravcode

<p align="center" style="font-size: 1.3em; color: #8b949e;">
A programming language that reads like thought.<br>
No parentheses. No commas. No semicolons. Just logic.
</p>

<div class="grid cards" markdown>

-   :zap: **Two Execution Modes**

    ---

    Interpret with `saurav.py` for quick runs, or compile to native C with `sauravcc.py` for maximum performance.

-   :broom: **Clean Syntax**

    ---

    No parens for calls, no commas between args, no semicolons. Indentation defines blocks. Code reads like pseudocode.

-   :arrows_counterclockwise: **Full Recursion**

    ---

    Recursive functions with parenthesized disambiguation: `factorial (n - 1)` works as you'd expect.

-   :package: **Dynamic Lists**

    ---

    Arrays with `append`, `len`, indexing, and assignment. Compiles to bounds-checked C arrays.

-   :classical_building: **Classes & Objects**

    ---

    Object-oriented with `class`, `self`, `new`, methods, and dot notation for fields.

-   :shield: **Error Handling**

    ---

    `try`/`catch` blocks. The compiler maps these to `setjmp`/`longjmp` in generated C.

</div>

## See the Difference

=== "sauravcode"

    ```
    function add x y
        return x + y

    print add 3 5
    ```

=== "Python"

    ```python
    def add(x, y):
        return x + y

    print(add(3, 5))
    ```

=== "JavaScript"

    ```javascript
    function add(x, y) {
        return x + y;
    }

    console.log(add(3, 5));
    ```

## Quick Start

**Prerequisites:** Python 3.6+, gcc (for compiler)

```bash
git clone https://github.com/sauravbhattacharya001/sauravcode.git
cd sauravcode
```

Run with the interpreter:

```bash
python saurav.py hello.srv
```

Compile to native executable:

```bash
python sauravcc.py hello.srv
./hello
```

!!! tip "New to sauravcode?"
    Check out the [Getting Started](getting-started.md) guide for a step-by-step introduction,
    or dive into the [Language Reference](language.md) for the complete specification.

## Links

- [:fontawesome-brands-github: GitHub Repository](https://github.com/sauravbhattacharya001/sauravcode)
- [:material-web: Project Home Page](https://sites.google.com/view/sauravcode)
- [:material-book-open-variant: Language Reference](language.md)
- [:material-cog: Architecture Guide](architecture.md)
