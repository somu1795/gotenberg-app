# ─────────────────────────────────────────────────────────────────
# Dockerfile — Gateway container image
#
# This is ONLY used when deploying via docker-compose.yml
# (./start.sh with no flags). It builds the gateway as a container.
#
# If running the gateway natively on the host (./start.sh --dev),
# this file is NOT used — Python runs directly from your venv.
# ─────────────────────────────────────────────────────────────────

FROM python:3.12-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config.py config.yaml main.py proxy.py ./
COPY middleware/ middleware/

# Run as non-root
RUN useradd -r -s /bin/false gateway
USER gateway

EXPOSE 9225

CMD ["python", "main.py"]
