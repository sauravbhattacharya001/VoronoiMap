# Your First Program

Let's build a real program step by step. We'll create a grade calculator that takes a list of scores, computes the average, and assigns letter grades.

## Step 1: Start Simple

Create a file called `grades.srv`:

```
print "Grade Calculator"
```

Run it:

```bash
python saurav.py grades.srv
```

## Step 2: Add a Function

Functions use the `function` keyword. No parentheses needed for calls:

```
function greet name
    print "Welcome to Grade Calculator,"
    print name

greet "Student"
```

## Step 3: Work with Lists

Create a list of scores and compute the total:

```
scores = [92, 85, 78, 96, 88]

total = 0
for i 0 (len scores)
    total = total + scores[i]

average = total / (len scores)
print "Average:"
print average
```

## Step 4: Add Grade Logic

Use if/else if/else to assign letter grades:

```
function letter_grade score
    if score >= 90
        return "A"
    else if score >= 80
        return "B"
    else if score >= 70
        return "C"
    else if score >= 60
        return "D"
    else
        return "F"
```

## Step 5: Put It All Together

Here's the complete program:

```
# grades.srv — A sauravcode grade calculator

function letter_grade score
    if score >= 90
        return "A"
    else if score >= 80
        return "B"
    else if score >= 70
        return "C"
    else if score >= 60
        return "D"
    else
        return "F"

function compute_average scores_list size
    total = 0
    for i 0 size
        total = total + scores_list[i]
    return total / size

# Student scores
scores = [92, 85, 78, 96, 88]
n = len scores

# Calculate average
avg = compute_average scores n
print "Class average:"
print avg

# Print each grade
print "Individual grades:"
for i 0 n
    print scores[i]
    print letter_grade scores[i]
```

Run with interpreter or compiler:

```bash
python saurav.py grades.srv
python sauravcc.py grades.srv
```

## Key Takeaways

| Concept | Sauravcode | Traditional |
|---------|-----------|-------------|
| Function call | `add 3 5` | `add(3, 5)` |
| Blocks | Indentation | `{ }` |
| For loop | `for i 0 10` | `for (i=0; i<10; i++)` |
| Nested call | `f (n - 1)` | `f(n - 1)` |

!!! warning "The Disambiguation Rule"
    `f n - 1` parses as `f(n) - 1`, **not** `f(n-1)`. Use parentheses when passing expressions: `f (n - 1)`.

## Next Steps

- [Language Reference](language.md) — the complete specification with EBNF grammar
- [Examples](examples.md) — more annotated programs
- [Compiler Guide](compiler.md) — understand the generated C code
