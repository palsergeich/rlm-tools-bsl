# Docker Guide for rlm-tools-bsl

This guide explains how to run rlm-tools-bsl in Docker for both server and CLI modes.

## Quick Start

### 1. Build the image

```bash
docker build -t rlm-tools-bsl .
```

### 2. Run as HTTP Server (MCP)

```bash
docker run -d \
  --name rlm-server \
  -p 9000:9000 \
  -v /path/to/1c-sources:/data \
  -e ANTHROPIC_API_KEY=your_key_here \
  rlm-tools-bsl \
  --transport streamable-http --host 0.0.0.0 --port 9000
```

Server will be available at `http://127.0.0.1:9000/mcp`

### 3. Run with docker-compose (Recommended)

Create `data/` directory and place your 1C source code there:

```bash
mkdir -p data
cp -r /path/to/your/1c-sources/* data/
```

Then start the server:

```bash
docker-compose up -d rlm-server
```

Check logs:
```bash
docker-compose logs -f rlm-server
```

## Configuration

### Environment Variables

Pass them via `-e` flag or in `.env` file:

```bash
-e RLM_MAX_SESSIONS=5
-e RLM_SESSION_TIMEOUT=10
-e ANTHROPIC_API_KEY=sk-...
-e OPENAI_API_KEY=sk-...
```

See **[docs/ENV_REFERENCE.md](ENV_REFERENCE.md)** for full list.

### Volume Mounts

| Mount Point | Purpose |
|---|---|
| `/data` | 1C source code directory |
| `/app/.env` | Environment configuration (optional, read-only) |

Example:
```bash
docker run -d \
  -v /mnt/my-projects/project1:/data \
  -v /home/user/.env:/app/.env:ro \
  -p 9000:9000 \
  rlm-tools-bsl \
  --transport streamable-http --host 0.0.0.0 --port 9000
```

## CLI Mode (Index Building)

### Using docker-compose

```bash
# Build index
docker-compose run --rm rlm-cli index build /data/my-project

# Check index info
docker-compose run --rm rlm-cli index info /data/my-project

# Update index
docker-compose run --rm rlm-cli index update /data/my-project

# Drop index
docker-compose run --rm rlm-cli index drop /data/my-project
```

### Using docker run

```bash
docker run --rm \
  --entrypoint rlm-bsl-index \
  -v /path/to/1c-sources:/data \
  rlm-tools-bsl \
  index build /data
```

## Client Configuration

### Claude Code / Cursor / Other AI IDEs

Add to your MCP config:

```json
{
  "mcpServers": {
    "rlm-tools-bsl": {
      "type": "http",
      "url": "http://127.0.0.1:9000/mcp"
    }
  }
}
```

### Using stdio transport (stdio mode)

If you prefer stdio (local Docker container):

```bash
docker run --rm \
  -v /path/to/1c-sources:/data \
  rlm-tools-bsl \
  rlm-tools-bsl --transport stdio
```

## Health Check

Server includes automatic health check via HTTP socket. Monitor with:

```bash
docker inspect --format='{{.State.Health.Status}}' rlm-server
```

## Performance Tuning

### For Large Projects (20K+ files)

Pre-build the index:
```bash
docker-compose run --rm rlm-cli index build /data/my-project
```

Then start server with indexed project:
```bash
docker-compose up -d rlm-server
```

### Increase Session Limits

```bash
docker run -d \
  -e RLM_MAX_SESSIONS=10 \
  -e RLM_SESSION_TIMEOUT=20 \
  -p 9000:9000 \
  -v /data:/data \
  rlm-tools-bsl \
  --transport streamable-http --host 0.0.0.0 --port 9000
```

### Memory/CPU Limits

```bash
docker run -d \
  --memory=4g \
  --cpus=2 \
  -p 9000:9000 \
  -v /data:/data \
  rlm-tools-bsl \
  --transport streamable-http --host 0.0.0.0 --port 9000
```

## Troubleshooting

### Port 9000 already in use

Use a different port:
```bash
docker run -d -p 8000:9000 rlm-tools-bsl
```

Then update MCP config to `http://127.0.0.1:8000/mcp`

### Container keeps restarting

Check logs:
```bash
docker logs rlm-server
```

Common issues:
- Missing `/data` volume or empty directory
- Invalid environment variables
- Insufficient memory for large projects

### Slow file operations on large projects

Build index first:
```bash
docker-compose run --rm rlm-cli index build /data/my-project
```

This creates SQLite index for instant lookups.

### LLM Query not working

Ensure API keys are set:
```bash
docker run -d \
  -e ANTHROPIC_API_KEY=sk-... \
  -e OPENAI_API_KEY=sk-... \
  -p 9000:9000 \
  -v /data:/data \
  rlm-tools-bsl \
  --transport streamable-http --host 0.0.0.0 --port 9000
```

## Development

### Build for development

```bash
docker build -t rlm-tools-bsl:dev \
  --target development \
  -f Dockerfile.dev .
```

(Optional: create Dockerfile.dev for development with test dependencies)

### Run tests in container

```bash
docker run --rm \
  -v "$(pwd)":/app \
  rlm-tools-bsl \
  pytest tests/
```

## Production Deployment

### Using Kubernetes

See example Kubernetes manifests in `k8s/` directory (if available).

### Using systemd (bare metal)

For native systemd service, see **[docs/INSTALL.md](INSTALL.md)** instead of Docker.

### Health check endpoint

Implement custom health check:

```python
curl -X POST http://127.0.0.1:9000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}'
```

## Multiple Projects

To run multiple instances:

```yaml
# docker-compose.yml
services:
  project1:
    image: rlm-tools-bsl
    ports:
      - "9001:9000"
    volumes:
      - ./data/project1:/data

  project2:
    image: rlm-tools-bsl
    ports:
      - "9002:9000"
    volumes:
      - ./data/project2:/data
```

Then in MCP config:

```json
{
  "mcpServers": {
    "project1": { "url": "http://127.0.0.1:9001/mcp" },
    "project2": { "url": "http://127.0.0.1:9002/mcp" }
  }
}
```
