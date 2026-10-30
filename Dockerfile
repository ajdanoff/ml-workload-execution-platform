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

COPY --from=builder /build/deps /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ ./src/
COPY pytest.ini ./
# Copy our wrapper script path over
COPY entrypoint.sh ./ 

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace/src

RUN useradd -u 8888 appuser && chown -R appuser:appuser /workspace
USER appuser

EXPOSE 8000

# Tell Docker to always process scripts through the entrypoint script
ENTRYPOINT ["./entrypoint.sh"]

CMD ["uvicorn", "src.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
