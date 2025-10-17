# Use a small official Python image
FROM python:3.13-slim

# Avoid buffering (useful for logs)
ENV PYTHONUNBUFFERED=1

# Working directory
WORKDIR /app

# Install system deps (if needed) and cleanup
RUN apt-get update && apt-get install -y --no-install-recommends procps\
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the collector script into the image
COPY collector_docker.py .

# Optional: create a log dir or any other needed dirs
VOLUME ["/app/logs"]

# Default command
CMD ["python", "/app/collector_docker.py", "-v"]