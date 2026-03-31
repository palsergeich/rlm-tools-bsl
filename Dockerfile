FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . .

# Install package
RUN pip install --no-cache-dir .

# Create volume mount point for projects
RUN mkdir -p /data

# Default: run HTTP MCP server on port 9000
# Can be overridden with:
#   docker run --rm rlm-tools-bsl rlm-bsl-index index build /data/my-project
#   docker run --rm rlm-tools-bsl rlm-tools-bsl --help
EXPOSE 9000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 9000), timeout=5)" || exit 1

ENTRYPOINT ["rlm-tools-bsl"]
CMD ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"]
