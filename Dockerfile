# Stage 1: Build virtualenv with dependencies and application
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

# Create isolated virtualenv and update packaging tooling
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install pinned dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy packaging metadata and source code, then install application package without reinstalling dependencies
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .


# Stage 2: Minimal non-root production runtime
FROM python:3.11-slim-bookworm

# Apply latest OS security updates
RUN apt-get update && \
    apt-get upgrade -y && \
    rm -rf /var/lib/apt/lists/*

# Build arguments for OCI annotations
ARG VERSION="0.1.0"
ARG BUILD_DATE=""
ARG VCS_REF=""

# Standard OCI labels
LABEL org.opencontainers.image.title="server-health-monitor-api" \
      org.opencontainers.image.description="FastAPI service for server log health monitoring" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Create fixed UID/GID 10001 unprivileged service user
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g 10001 -m -s /bin/sh appuser

# Pre-create data directories:
# /data/org   : monitored server logs (mounted read-only at runtime)
# /data/state : checkpoint store directory (mounted writable, owned by appuser)
RUN mkdir -p /data/org /data/state && \
    chown -R 10001:10001 /data/state

# Copy only the compiled virtualenv from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Default environment configuration
ENV HM_ROOT=/data/org \
    HM_STATE_FILE=/data/state/state.json \
    HM_HOST=0.0.0.0 \
    HM_PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Container liveness check using standard library urllib (no curl dependency required)
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

# Run exclusively as non-root user
USER 10001:10001

# Entrypoint runs the FastAPI application via uvicorn.
# Runs with a single worker because the checkpoint state store is designed for a single writer.
ENTRYPOINT ["python", "-m", "health_api"]

