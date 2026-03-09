ARG NAUTILUSTRADER_IMAGE=ghcr.io/nautechsystems/nautilustrader:latest
FROM ${NAUTILUSTRADER_IMAGE} AS runtime

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python3 -m pip install --upgrade pip setuptools wheel
RUN python3 -m pip install --no-cache-dir -e ".[dev]"

# Copy remaining repo files
COPY . .

# Runtime directories
RUN mkdir -p /app/logs /app/data/parquet /app/catalog

# Drop privileges
RUN useradd -m -u 1000 trader && chown -R trader:trader /app
USER trader

ENTRYPOINT ["python3", "-m", "nautilus_predict.main"]
CMD ["--mode", "live"]
