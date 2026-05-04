# Use a slim Debian-based Python image (has system libs needed for psutil)
FROM python:3.13-slim

# Avoid buffering (useful for logs)
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies (tini for signal handling, curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Copy collector requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy dashboard requirements and install
COPY dashboard/ dashboard/
RUN pip install --no-cache-dir -r dashboard/requirements.txt

# Copy collector script
COPY collector_docker.py .

# Create log directory
RUN mkdir -p /app/logs

# Create volumes
VOLUME ["/app/logs"]

# Expose dashboard port
EXPOSE 8080

# Use tini to handle signals properly
ENTRYPOINT ["/sbin/tini", "--"]

# Default: run collector in verbose mode
# To run dashboard: docker run -p 8080:8080 fronius-collector python dashboard/app.py
# To run both: see docker-compose.yaml
CMD ["python", "/app/collector_docker.py", "-v"]