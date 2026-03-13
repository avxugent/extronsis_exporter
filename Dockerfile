# syntax=docker/dockerfile:1

# ── Build / dependency stage ──────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install dependencies into a separate prefix so we can copy them cleanly
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL org.opencontainers.image.title="extronsis-exporter" \
      org.opencontainers.image.description="Prometheus exporter for Extron SIS devices" \
      org.opencontainers.image.source="https://github.com/kristofkeppens/extronsis-exporter"

WORKDIR /app

# Copy installed packages from the builder stage
COPY --from=builder /install /usr/local

# Copy the application source
COPY extronsis_exporter/ ./extronsis_exporter/

# The configuration file is expected to be mounted at runtime.
# Default path: /etc/extronsis-exporter/config.yaml
ENV EXTRONSIS_CONFIG=/etc/extronsis-exporter/config.yaml

EXPOSE 9877

# Run as a non-root user for security
RUN useradd --no-create-home --shell /bin/false exporter
USER exporter

ENTRYPOINT ["python", "-m", "extronsis_exporter"]
