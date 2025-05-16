FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (for Selenium/Chromedriver)
RUN apt-get update && \
    apt-get install -y --no-install-recommends chromium-driver chromium && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY scripts/peloton-scrape/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code after dependencies for better build caching
COPY scripts/peloton-scrape/ .

# Optional: better Docker logging
ENV PYTHONUNBUFFERED=1

# Entrypoint, pass envs as needed
ENTRYPOINT ["python", "scrape.py"]
