# Sauravcode Language Reference

> The complete specification of the sauravcode programming language.

## Table of Contents

- [Design Philosophy](#design-philosophy)
- [Source Files](#source-files)
- [Lexical Structure](#lexical-structure)
- [Types](#types)
- [Variables](#variables)
- [Operators](#operators)
- [Control Flow](#control-flow)
- [Functions](#functions)
- [Imports](#imports)
- [Classes](#classes)
- [Lists](#lists)
- [Maps (Dictionaries)](#maps-dictionaries)
- [Error Handling](#error-handling)
- [Built-in Functions](#built-in-functions)
- [Grammar (EBNF)](#grammar-ebnf)

---

## Design Philosophy

Sauravcode strips away syntactic noise to let you focus on logic:

- **No parentheses** for function calls — `add 3 5` instead of `add(3, 5)`
- **No commas** between arguments
- **No semicolons** to end statements
- **No braces** — indentation defines blocks (like Python)
- **No `def`/`func`/`fn`** — just `function`

The result is code that reads almost like pseudocode.

## Source Files

- Extension: `.srv`
- Encoding: UTF-8
- Line endings: LF or CRLF
- Indentation: spaces or tabs (tabs are normalized to 4 spaces)

## Lexical Structure

### Comments

Line comments start with `#`:

```
# This is a comment
x = 10  # inline comment
```

### Identifiers

Identifiers start with a letter or underscore, followed by letters, digits, or underscores:

```
my_var
_private
counter2
```

### Keywords

```
function  return  class  new  self
if  else if  else
for  while  in
try  catch  throw
import
print  true  false
and  or  not
int  float  bool  string
list  set  map  stack  queue
append  len  pop  get
```

### Literals

| Type    | Examples                          |
|---------|-----------------------------------|
| Integer | `42`, `0`, `100`                  |
| Float   | `3.14`, `0.5`, `2.`              |
| String  | `"hello"`, `"it's a \"quote\""` |
| Boolean | `true`, `false`                   |
| List    | `[1, 2, 3]`, `["a", "b"]`       |
| Map     | `{"key": "value", "count": 42}` |

### Indentation

Blocks are defined by indentation level. An increase in indentation starts a new block; a decrease ends it:

```
function greet name
    print "Hello"      # indented = part of function body
    print name

print "outside"        # back to top level
```

### F-Strings (String Interpolation)

Prefix a string with `f` to embed expressions inside `{}`:

```
name = "Alice"
age = 30
print f"Hello, {name}!"           # Hello, Alice!
print f"{name} is {age} years old" # Alice is 30 years old
print f"2 + 3 = {2 + 3}"          # 2 + 3 = 5
```

Expressions inside `{}` are evaluated at runtime. Any valid expression is allowed, including function calls and arithmetic.

## Types

Sauravcode is dynamically typed. Values carry their type at runtime.

| Type    | Description            | Example Values          |
|---------|------------------------|-------------------------|
| Number  | 64-bit floating point  | `42`, `3.14`, `-7`     |
| String  | Character sequence     | `"hello"`, `""`        |
| Boolean | Logical value          | `true`, `false`         |
| List    | Dynamic array          | `[1, 2, 3]`            |
| Object  | Class instance         | `new Point`             |

Optional type annotations (`int`, `float`, `bool`, `string`) are recognized by the parser but not enforced at runtime.

## Variables

### Declaration & Assignment

Variables are declared on first assignment. No `let`, `var`, or `const` needed:

```
x = 10
name = "sauravcode"
flag = true
pi = 3.14159
```

### Scope

- Variables inside a function are **local** to that call
- Top-level variables are **global**
- Function parameters shadow outer variables

## Operators

### Arithmetic

| Operator | Description    | Example       | Result |
|----------|---------------|---------------|--------|
| `+`      | Addition       | `10 + 3`      | `13`   |
| `-`      | Subtraction    | `10 - 3`      | `7`    |
| `*`      | Multiplication | `10 * 3`      | `30`   |
| `/`      | Division       | `10 / 3`      | `3.33` |
| `%`      | Modulo         | `10 % 3`      | `1`    |

### Comparison

| Operator | Description          |
|----------|----------------------|
| `==`     | Equal                |
| `!=`     | Not equal            |
| `<`      | Less than            |
| `>`      | Greater than         |
| `<=`     | Less than or equal   |
| `>=`     | Greater than or equal|

### Logical

| Operator | Description     | Example            |
|----------|----------------|--------------------|
| `and`    | Logical AND     | `x and y`          |
| `or`     | Logical OR      | `x or y`           |
| `not`    | Logical NOT     | `not x`            |

### Operator Precedence (highest to lowest)

1. `not` (unary)
2. `*`, `/`, `%`
3. `+`, `-`
4. `==`, `!=`, `<`, `>`, `<=`, `>=`
5. `and`
6. `or`

### Grouping

Use parentheses to override precedence:

```
result = (2 + 3) * (4 - 1)    # 15
```

### Pipe Operator

The pipe operator `|>` passes the result of the left expression as the last argument to the function on the right:

| Expression | Equivalent To |
|-----------|---------------|
| `x \|> f` | `f x` |
| `x \|> f a` | `f a x` |
| `x \|> f a b` | `f a b x` |

Pipes can be chained:

```
function double x
    return x * 2

function add x y
    return x + y

result = 5 |> double |> add 10
print result    # 20
```

The pipe operator has the lowest precedence, binding after all other operators.

## Control Flow

### If / Else If / Else

```
score = 85
if score >= 90
    print "A"
else if score >= 80
    print "B"
else if score >= 70
    print "C"
else
    print "F"
```

### While Loop

```
i = 0
while i < 10
    print i
    i = i + 1
```

### For Loop (Range-Based)

Iterates from `start` to `end - 1`:

```
for i 1 6
    print i    # prints 1, 2, 3, 4, 5
```

Syntax: `for <variable> <start> <end>`

### For-Each Loop (For-In)

Iterate over elements of a list, string, or map:

```
# List iteration
fruits = ["apple", "banana", "cherry"]
for fruit in fruits
    print fruit

# String iteration
for ch in "hello"
    print ch

# Map iteration (iterates over keys)
config = {"host": "localhost", "port": 8080}
for key in config
    print f"{key}: {config[key]}"
```

Syntax: `for <variable> in <iterable>`

## Functions

### Definition

```
function name param1 param2
    # body (indented)
    return expression
```

### Calling

No parentheses, no commas — just space-separated arguments:

```
function add x y
    return x + y

add 3 5          # returns 8
print add 3 5    # prints 8
```

### Recursion

Use parentheses to disambiguate nested calls in arguments:

```
function factorial n
    if n <= 1
        return 1
    return n * factorial (n - 1)

print factorial 10    # 3628800
```

Without parentheses, `factorial n - 1` would parse as `factorial(n) - 1` rather than `factorial(n - 1)`.

### Nested Calls

```
function square x
    return x * x

function sum_of_squares a b
    return square a + square b

print sum_of_squares 3 4    # 25
```

## Imports

Import functions and variables from other `.srv` files:

```
import "utils"         # imports utils.srv from the same directory
import utils           # bare identifier syntax (same effect)
```

All top-level variables and functions from the imported module become available in the current scope.

### Module Search

The interpreter looks for the module file relative to the importing file's directory:
- `import "utils"` → looks for `utils.srv`
- `import "lib/helpers"` → looks for `lib/helpers.srv`

## Classes

### Definition

```
class Point
    function init x y
        self.x = x
        self.y = y

    function display
        print self.x
        print self.y
```

### Instantiation

```
p = new Point
p.init 10 20
p.display
```

### Field Access

```
print p.x    # dot notation
p.x = 42    # field assignment
```

## Lists

### Creation

```
nums = [10, 20, 30]
empty = []
mixed = [1, "two", true]
```

### Indexing

Zero-based indexing:

```
print nums[0]     # 10
print nums[2]     # 30
nums[1] = 99      # assignment
```

### Operations

| Operation        | Syntax              | Description                |
|-----------------|---------------------|----------------------------|
| Length           | `len list`          | Number of elements         |
| Append           | `append list value` | Add element to end         |
| Index access     | `list[i]`           | Get element at index       |
| Index assignment | `list[i] = val`     | Set element at index       |

### Example

```
nums = [10, 20, 30]
print len nums        # 3
append nums 40
print nums[3]         # 40
print len nums        # 4
```

### List Comprehensions

Create lists with a concise expression syntax:

```
# Basic comprehension
squares = [x * x for x in range 1 6]
print squares    # [1, 4, 9, 16, 25]

# With condition (filter)
evens = [x for x in range 1 11 if x % 2 == 0]
print evens    # [2, 4, 6, 8, 10]
```

Syntax: `[expression for variable in iterable]` or `[expression for variable in iterable if condition]`

## Maps (Dictionaries)

Key-value data structures for associative storage.

### Creation

```
empty = {}
person = {"name": "Alice", "age": 30, "city": "Seattle"}
```

### Access and Modification

```
print person["name"]     # Alice
person["age"] = 31       # update
person["email"] = "a@b"  # add new key
```

### Operations

| Operation      | Syntax              | Description              |
|---------------|---------------------|--------------------------|
| Access        | `map[key]`          | Get value by key         |
| Assign        | `map[key] = val`    | Set or update value      |
| Contains      | `contains map key`  | Check if key exists      |
| Length        | `len map`           | Number of key-value pairs|
| Iteration     | `for key in map`    | Iterate over keys        |

## Error Handling

### Try / Catch

```
try
    x = risky_operation
catch err
    print "Error occurred"
    print err
```

The catch variable (`err`) receives the error message as a string.

### Throw

Raise an error explicitly with `throw`:

```
function validate_age age
    if age < 0
        throw "Age cannot be negative"
    if age > 150
        throw f"Unrealistic age: {age}"
    return age

try
    validate_age -5
catch err
    print err    # Age cannot be negative
```

**Compiler note:** Try/catch compiles to `setjmp`/`longjmp` in the generated C code.

## Built-in Functions

### Core

| Function          | Syntax                | Description                    |
|------------------|-----------------------|--------------------------------|
| `print`          | `print expr`          | Print value to stdout          |
| `len`            | `len list`            | Return list/string length      |
| `append`         | `append list value`   | Append value to list           |
| `pop`            | `pop list`            | Remove and return last element |
| `get`            | `get list index`      | Get element at index           |

### String Functions

| Function          | Syntax                          | Description                            |
|------------------|---------------------------------|----------------------------------------|
| `upper`          | `upper str`                     | Convert string to uppercase            |
| `lower`          | `lower str`                     | Convert string to lowercase            |
| `trim`           | `trim str`                      | Remove leading/trailing whitespace     |
| `replace`        | `replace str old new`           | Replace occurrences in string          |
| `split`          | `split str delim`               | Split string into list                 |
| `join`           | `join delim list`               | Join list elements into string         |
| `contains`       | `contains str sub`              | Check if string/list contains value    |
| `starts_with`    | `starts_with str prefix`        | Check if string starts with prefix     |
| `ends_with`      | `ends_with str suffix`          | Check if string ends with suffix       |
| `substring`      | `substring str start end`       | Extract substring [start:end]          |
| `index_of`       | `index_of str sub`              | Find index of substring (-1 if none)   |
| `char_at`        | `char_at str index`             | Get character at index                 |

### Math Functions

| Function          | Syntax                | Description                            |
|------------------|-----------------------|----------------------------------------|
| `abs`            | `abs n`               | Absolute value                         |
| `round`          | `round n [places]`    | Round number (optional decimal places) |
| `floor`          | `floor n`             | Round down to integer                  |
| `ceil`           | `ceil n`              | Round up to integer                    |
| `sqrt`           | `sqrt n`              | Square root                            |
| `power`          | `power base exp`      | Exponentiation                         |

### Utility Functions

| Function          | Syntax                          | Description                            |
|------------------|---------------------------------|----------------------------------------|
| `type_of`        | `type_of val`                   | Get type name (number/string/bool/list)|
| `to_string`      | `to_string val`                 | Convert value to string                |
| `to_number`      | `to_number val`                 | Convert value to number                |
| `input`          | `input [prompt]`                | Read line from stdin                   |
| `range`          | `range [start] end [step]`      | Generate list of numbers               |
| `reverse`        | `reverse val`                   | Reverse a list or string               |
| `sort`           | `sort list`                     | Sort a list                            |

User-defined functions override builtins, so you can customize any built-in function.

## Grammar (EBNF)

```ebnf
program        = { statement } ;
statement      = function_def | class_def | if_stmt | while_stmt
               | for_stmt | foreach_stmt | try_stmt | return_stmt
               | print_stmt | import_stmt | throw_stmt
               | assignment | append_stmt | expression_stmt ;

function_def   = "function" IDENT { IDENT } NEWLINE INDENT block DEDENT ;
class_def      = "class" IDENT NEWLINE INDENT { function_def } DEDENT ;

if_stmt        = "if" expression NEWLINE INDENT block DEDENT
                 { "else if" expression NEWLINE INDENT block DEDENT }
                 [ "else" NEWLINE INDENT block DEDENT ] ;
while_stmt     = "while" expression NEWLINE INDENT block DEDENT ;
for_stmt       = "for" IDENT term term NEWLINE INDENT block DEDENT ;
foreach_stmt   = "for" IDENT "in" expression NEWLINE INDENT block DEDENT ;
try_stmt       = "try" NEWLINE INDENT block DEDENT
                 "catch" [ IDENT ] NEWLINE INDENT block DEDENT ;
import_stmt    = "import" ( STRING | IDENT ) ;
throw_stmt     = "throw" expression ;

return_stmt    = "return" expression ;
print_stmt     = "print" expression ;
assignment     = IDENT "=" expression
               | IDENT "[" expression "]" "=" expression
               | IDENT "." IDENT "=" expression ;
append_stmt    = "append" IDENT expression ;

expression     = pipe_expr ;
pipe_expr      = logical_or { "|>" logical_or } ;
logical_or     = logical_and { "or" logical_and } ;
logical_and    = comparison { "and" comparison } ;
comparison     = arith_expr [ comp_op arith_expr ] ;
arith_expr     = mul_expr { ("+" | "-") mul_expr } ;
mul_expr       = unary { ("*" | "/" | "%") unary } ;
unary          = "not" unary | "-" atom | atom ;
atom           = NUMBER | STRING | f_string | "true" | "false"
               | IDENT [ "[" expression "]" ]
               | IDENT { arg }
               | "len" atom
               | "new" IDENT
               | "(" expression ")"
               | list_literal
               | list_comprehension
               | map_literal ;

f_string       = "f" '"' { char | "{" expression "}" } '"' ;
list_literal   = "[" [ expression { "," expression } ] "]" ;
list_comprehension = "[" expression "for" IDENT "in" expression
                     [ "if" expression ] "]" ;
map_literal    = "{" [ STRING ":" expression
                     { "," STRING ":" expression } ] "}" ;

arg            = NUMBER | STRING | "true" | "false" | IDENT
               | "(" expression ")" | "[" ... "]" ;

comp_op        = "==" | "!=" | "<" | ">" | "<=" | ">=" ;
block          = { statement } ;
```

---

*This document describes sauravcode as implemented in the interpreter (`saurav.py`) and compiler (`sauravcc.py`). For examples, see the `.srv` files in the repository.*
