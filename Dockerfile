FROM python:3.12-slim AS base

# Build-time dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    libssl-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifest first for layer caching
COPY pyproject.toml ./
COPY src/ ./src/

# Install project (production deps only)
RUN uv pip install --system --no-cache ".[dev]"

# Runtime stage
FROM base AS runtime

COPY . .

# Non-root user for security
RUN useradd -m -u 1000 trader && chown -R trader:trader /app
USER trader

# Create runtime directories
RUN mkdir -p /app/logs /app/data/parquet /app/catalog

ENTRYPOINT ["python", "-m", "nautilus_predict.main"]
CMD ["--mode", "live"]
