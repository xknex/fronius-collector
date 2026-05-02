# Use a small official Python image
FROM python:3.13-alpine

# Avoid buffering (useful for logs)
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies for running both collector and dashboard
RUN apk add --no-cache tini

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