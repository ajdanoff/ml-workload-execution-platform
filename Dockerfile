# =====================================================================
# Stage 1: Build & Dependency Collection
# =====================================================================
FROM python:3.10-slim AS builder

WORKDIR /build

# Install any basic OS compilation dependencies if required by extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 1. Copy only the requirements manifest to optimize Docker layer build cache boundaries
COPY requirements.txt .

# 2. Install python dependencies cleanly out of requirements.txt into a target folder path
RUN pip install --no-cache-dir --target=/build/deps -r requirements.txt

# =====================================================================
# Stage 2: Minimal Runtime Environment
# =====================================================================
FROM python:3.10-slim AS runner

WORKDIR /workspace

# Copy installed site-packages and binary executable tools cleanly from the builder layer
COPY --from=builder /build/deps /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 3. Copy source code and config blueprints into working directory paths
COPY src/ ./src/
COPY pytest.ini ./

# Set environment paths to optimize Python runtime runtime behaviors
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace/src

# Create a non-privileged system user for runtime application execution security
RUN useradd -u 8888 appuser && chown -R appuser:appuser /workspace
USER appuser

# Expose standard production communication port
EXPOSE 8000

# Run production-ready Uvicorn server matching your application package layouts
CMD ["uvicorn", "src.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
