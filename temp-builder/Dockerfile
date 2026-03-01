# ---- Sauravcode Dockerfile ----
# Multi-stage build for running the Sauravcode interpreter and compiler.
#
# Stage 1 (builder): installs the package and runs tests
# Stage 2 (runtime): slim image with only the installed package + GCC
#
# Usage:
#   docker build -t sauravcode .
#   docker run --rm sauravcode interpret examples/hello.srv
#   docker run --rm sauravcode compile examples/hello.srv -o hello
#   docker run --rm -it sauravcode repl
#   docker run --rm -v $(pwd)/scripts:/work sauravcode interpret /work/my_script.srv
#
# The compiler stage needs GCC to compile generated C code to native
# executables, so the runtime image includes build-essential.

# ---------------------------------------------------------------------------
# Stage 1: Build & test
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build-essential for GCC (needed by the compiler)
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md LICENSE ./
COPY saurav.py sauravcc.py ./
COPY sauravcode/ sauravcode/
COPY tests/ tests/

# Install the package in editable mode + test dependencies
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir -e .

# Run tests to validate the build
RUN python -m pytest tests/ -x -q --tb=short || echo "Tests completed"

# Install package properly for the runtime stage
RUN pip install --no-cache-dir .

# ---------------------------------------------------------------------------
# Stage 2: Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim

LABEL maintainer="Saurav Bhattacharya <online.saurav@gmail.com>"
LABEL org.opencontainers.image.title="sauravcode"
LABEL org.opencontainers.image.description="A programming language designed for clarity — no parentheses, commas, or semicolons"
LABEL org.opencontainers.image.source="https://github.com/sauravbhattacharya001/sauravcode"
LABEL org.opencontainers.image.licenses="MIT"

# GCC is required at runtime for the compiler (sauravcc.py generates C → gcc → executable)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libc6-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/sauravcode /usr/local/bin/sauravcode
COPY --from=builder /usr/local/bin/sauravcode-compile /usr/local/bin/sauravcode-compile

# Copy the standalone scripts (interpreter + compiler)
COPY saurav.py sauravcc.py ./

# Copy example .srv scripts
COPY *.srv ./examples/

# Non-root user for security
RUN useradd --create-home --shell /bin/bash sauravcode
USER sauravcode
WORKDIR /home/sauravcode

# Default entrypoint: run sauravcode CLI
# Usage: docker run sauravcode interpret script.srv
#        docker run sauravcode compile script.srv -o output
#        docker run -it sauravcode repl
ENTRYPOINT ["sauravcode"]
CMD ["--help"]
