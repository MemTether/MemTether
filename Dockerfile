FROM python:3.10-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy project files (README.md is required by pyproject readme=)
COPY pyproject.toml README.md ./
COPY *.py .
COPY clients/ clients/
COPY scripts/ scripts/
COPY integrations/ integrations/

# Install the package
RUN pip install --no-cache-dir .
RUN pip install --no-cache-dir fastapi uvicorn

# Create non-root user
RUN useradd -m -s /bin/bash memtether
USER memtether

# Set data dir
ENV MEM_DB=/home/memtether/.memtether/memory.db

# Default command
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8080"]
