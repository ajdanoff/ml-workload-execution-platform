# =====================================================================
# Stage 1: Build & Dependency Collection
# =====================================================================
FROM python:3.10-slim AS builder

WORKDIR /build

# Install any basic OS compilation dependencies if required by extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies directly into a localized folder path
RUN pip install --no-cache-dir --target=/build/deps \
    fastapi[standard] \
    pytest \
    pytest-asyncio \
    httpx \
    pytest-mock

# =====================================================================
# Stage 2: Minimal Runtime Environment
# =====================================================================
FROM python:3.10-slim AS runner

WORKDIR /workspace

# Copy installed site-packages directly from the builder stage
COPY --from=builder /build/deps /usr/local/lib/python3.10/site-packages
# FastAPI standard installs the 'fastapi' command line tool to /usr/local/bin
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy app code and test configuration into working directory
COPY src/ ./src/
COPY pytest.ini ./

# Set environment paths to optimize Python runtime behaviors
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace/src

# Create a non-privileged system user for runtime application execution
RUN useradd -u 8888 appuser && chown -R appuser:appuser /workspace
USER appuser

# Expose standard production communication port
EXPOSE 8000

# Run production-ready Uvicorn server matching your application router mapping
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
