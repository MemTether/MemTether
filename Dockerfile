FROM python:3.10-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY *.py .
COPY clients/ clients/
COPY scripts/ scripts/
COPY integrations/ integrations/
COPY tests/ tests/

# Install the package
RUN pip install --no-cache-dir .

# Create non-root user
RUN useradd -m -s /bin/bash memtether
USER memtether

# Set data dir
ENV MEM_DB=/home/memtether/.memtether/memory.db

# Default command
CMD ["python", "-m", "memsearch", "--help"]
