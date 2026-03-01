---
name: Bug Report
about: Report a bug in the interpreter or compiler
title: "[Bug] "
labels: bug
assignees: ''
---

## Description

A clear description of the bug.

## Steps to Reproduce

1. Create a `.srv` file with the following code:

```
# paste your sauravcode here
```

2. Run with:
   - [ ] Interpreter: `python saurav.py file.srv`
   - [ ] Compiler: `python sauravcc.py file.srv`

## Expected Behavior

What should happen?

## Actual Behavior

What actually happens? Include the full error output if any.

## Environment

- **OS:** (e.g., Windows 11, Ubuntu 22.04, macOS 14)
- **Python version:** (run `python --version`)
- **gcc version:** (run `gcc --version`, if using compiler)

## Additional Context

- Does the bug occur in the interpreter, compiler, or both?
- Does using `--emit-c` reveal anything in the generated C code?
