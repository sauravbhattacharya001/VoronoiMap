"""
CLI entry points for sauravcode.

These thin wrappers allow ``pip install sauravcode`` to create
console scripts (``sauravcode`` and ``sauravcode-compile``) that
delegate to the original interpreter and compiler modules.
"""

import os
import sys


def _project_root():
    """Return the directory containing the top-level saurav.py / sauravcc.py."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main_interpret():
    """Entry point for the ``sauravcode`` console script (interpreter + REPL)."""
    root = _project_root()
    script = os.path.join(root, "saurav.py")

    # If running from an installed package, saurav.py lives alongside the
    # sauravcode/ package directory.  Fall back to importlib for editable
    # installs where the layout is different.
    if os.path.isfile(script):
        # Inject root so that ``import saurav`` inside the script resolves.
        if root not in sys.path:
            sys.path.insert(0, root)

    # Import the interpreter's main() and call it.
    # This avoids exec/subprocess overhead.
    import importlib.util

    spec = importlib.util.spec_from_file_location("saurav", script)
    if spec is None or spec.loader is None:
        print("Error: Cannot locate saurav.py interpreter", file=sys.stderr)
        sys.exit(1)

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main()


def main_compile():
    """Entry point for the ``sauravcode-compile`` console script (compiler)."""
    root = _project_root()
    script = os.path.join(root, "sauravcc.py")

    if os.path.isfile(script):
        if root not in sys.path:
            sys.path.insert(0, root)

    import importlib.util

    spec = importlib.util.spec_from_file_location("sauravcc", script)
    if spec is None or spec.loader is None:
        print("Error: Cannot locate sauravcc.py compiler", file=sys.stderr)
        sys.exit(1)

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main()
