# ══════════════════════════════════════════════════════════════════
# Taomly — Production Dockerfile
# Multi-stage build: builder → runtime
# Final image: ~180MB (python:3.11-slim)
# ══════════════════════════════════════════════════════════════════

# ── Stage 1: builder ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps for psycopg2 compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into /build/venv
COPY requirements.txt .
RUN python -m venv /build/venv \
    && /build/venv/bin/pip install --upgrade pip \
    && /build/venv/bin/pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime ──────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Security: run as non-root user
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --no-create-home appuser

# Runtime deps only (no gcc)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy venv from builder
COPY --from=builder /build/venv /app/venv

# Copy application code
COPY --chown=appuser:appgroup . .

# Switch to non-root
USER appuser

# PATH: prefer venv binaries
ENV PATH="/app/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

EXPOSE 8000

# Health check (Render uses /health)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Entrypoint: uvicorn with 2 workers
# Render sets PORT env var; default 8000
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --loop uvloop"]
