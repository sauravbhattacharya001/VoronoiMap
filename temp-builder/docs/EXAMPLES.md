# Sauravcode Examples

> Annotated examples demonstrating sauravcode features.

## 1. Hello World

The simplest program:

```sauravcode
print "Hello, World!"
```

Run: `python saurav.py hello.srv`  
Compile: `python sauravcc.py hello.srv`

---

## 2. Functions and Arithmetic

Functions are defined with `function`, called without parentheses:

```sauravcode
function add x y
    return x + y

function multiply x y
    return x * y

# Call functions — no parens, no commas
print add 3 5           # 8
print multiply 4 6      # 24
```

---

## 3. Fibonacci (Recursion)

Parentheses disambiguate nested calls:

```sauravcode
function fib n
    if n <= 1
        return n
    return fib (n - 1) + fib (n - 2)

# Without parens, "fib n - 1" would parse as "fib(n) - 1"
print fib 10    # 55
print fib 20    # 6765
```

---

## 4. Factorial (Recursion + Multiplication)

```sauravcode
function factorial n
    if n <= 1
        return 1
    return n * factorial (n - 1)

print factorial 10    # 3628800
print factorial 5     # 120
```

---

## 5. Control Flow

### If / Else If / Else

```sauravcode
score = 85

if score >= 90
    print "A — excellent"
else if score >= 80
    print "B — good"
else if score >= 70
    print "C — average"
else
    print "Below C"
```

### While Loop

```sauravcode
# Count from 0 to 4
i = 0
while i < 5
    print i
    i = i + 1
```

### For Loop (Range)

```sauravcode
# Prints 1 through 5
for i 1 6
    print i
```

---

## 6. Lists

```sauravcode
# Create a list
nums = [10, 20, 30]

# Access elements (zero-indexed)
print nums[0]         # 10
print nums[2]         # 30

# Length
print len nums        # 3

# Append
append nums 40
print nums[3]         # 40
print len nums        # 4

# Modify
nums[0] = 99
print nums[0]         # 99
```

---

## 7. Boolean Logic

```sauravcode
x = true
y = false

if x and not y
    print "x is true, y is false"

if x or y
    print "at least one is true"

# Comparisons return booleans
if 5 != 3
    print "five is not three"
```

---

## 8. Classes

```sauravcode
class Point
    function init x y
        self.x = x
        self.y = y

    function display
        print self.x
        print self.y

p = new Point
p.init 10 20
p.display         # prints 10, then 20
```

---

## 9. Error Handling

```sauravcode
try
    x = risky_function
catch err
    print "Something went wrong"
    print err
```

---

## 10. Putting It Together: Sum of Squares

A complete program combining functions, loops, and lists:

```sauravcode
# Sum of squares from 1 to n
function sum_of_squares n
    total = 0
    for i 1 (n + 1)
        total = total + i * i
    return total

# Using function composition
function square x
    return x * x

function hypotenuse a b
    return square a + square b

print "Sum of squares 1-10:"
print sum_of_squares 10       # 385

print "Hypotenuse squared of 3,4:"
print hypotenuse 3 4          # 25
```

---

*For the full language specification, see [LANGUAGE.md](LANGUAGE.md).*
