# ---- Build stage ----
FROM python:3.14-slim AS builder

WORKDIR /app

# Install build deps
COPY pyproject.toml MANIFEST.in requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Copy source
COPY vormap.py README.md LICENSE ./
COPY tests/ tests/

# Build wheel
RUN pip install --no-cache-dir build \
    && python -m build --wheel --outdir /dist

# ---- Runtime stage ----
FROM python:3.14-slim AS runtime

LABEL maintainer="Saurav Bhattacharya <online.saurav@gmail.com>"
LABEL description="VoronoiMap – estimate aggregate statistics via Voronoi partitioning"

WORKDIR /app

# Install the built wheel + fast dependencies
COPY --from=builder /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl \
    && pip install --no-cache-dir numpy scipy \
    && rm -f /tmp/*.whl

# Non-root user for security
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

ENTRYPOINT ["voronoimap"]
