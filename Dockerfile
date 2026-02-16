# ---- Base Stage ----
FROM python:3.13-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ---- Dependencies Stage ----
FROM base AS dependencies

# Install system-level build deps needed to compile Python packages:
#   - gcc, g++           : C/C++ compiler (cryptography, bcrypt, etc.)
#   - libffi-dev          : Foreign Function Interface (cryptography)
#   - libssl-dev          : OpenSSL headers (cryptography)
#   - libmagic1           : Runtime library for python-magic
#   - libmagic-dev        : Dev headers for python-magic
#   - cargo, rustc        : Rust compiler (cryptography >= 40 build requirement)
#   - pkg-config          : Needed by many C-extension build systems
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    libmagic1 \
    libmagic-dev \
    cargo \
    rustc \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---- Production Stage ----
FROM base AS production

# Install only the runtime system libraries (no compilers/headers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    libssl3 \
    libffi8 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from the dependencies stage
COPY --from=dependencies /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=dependencies /usr/local/bin /usr/local/bin

# Copy application source code
COPY src/ ./src/
COPY main.py .

# Expose the default FastAPI/Uvicorn port
EXPOSE 8000

# Health check against the /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application with Uvicorn
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
