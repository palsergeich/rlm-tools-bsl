#!/bin/bash
set -e

echo "🐳 Docker Build & Test for rlm-tools-bsl"
echo "========================================"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. Check Docker is installed
echo -e "\n${YELLOW}[1/5] Checking Docker installation...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${RED}✗ Docker not found${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker $(docker --version)${NC}"

# 2. Build image
echo -e "\n${YELLOW}[2/5] Building Docker image...${NC}"
if docker build -t rlm-tools-bsl:test . ; then
    echo -e "${GREEN}✓ Image built successfully${NC}"
else
    echo -e "${RED}✗ Build failed${NC}"
    exit 1
fi

# 3. Create test data directory
echo -e "\n${YELLOW}[3/5] Creating test data directory...${NC}"
mkdir -p test-data
touch test-data/test.bsl

# 4. Test server startup (HTTP mode)
echo -e "\n${YELLOW}[4/5] Testing server startup (HTTP mode)...${NC}"
echo "Starting container..."
CONTAINER_ID=$(docker run -d \
    -p 9000:9000 \
    -v "$(pwd)/test-data:/data:ro" \
    --name rlm-test \
    rlm-tools-bsl:test \
    --transport streamable-http \
    --host 0.0.0.0 \
    --port 9000 2>&1)

if [ -z "$CONTAINER_ID" ]; then
    echo -e "${RED}✗ Failed to start container${NC}"
    exit 1
fi

echo "Container ID: $CONTAINER_ID"
echo "Waiting for server to start..."
sleep 3

# Check if container is still running
if ! docker ps -q --filter "id=$CONTAINER_ID" | grep -q .; then
    echo -e "${RED}✗ Container exited prematurely${NC}"
    docker logs rlm-test
    docker rm -f rlm-test 2>/dev/null || true
    exit 1
fi

echo -e "${GREEN}✓ Server started${NC}"

# 5. Test health check
echo -e "\n${YELLOW}[5/5] Testing health check...${NC}"
if timeout 10 bash -c 'while ! curl -s http://localhost:9000/health &>/dev/null; do sleep 1; done'; then
    echo -e "${GREEN}✓ Health check passed${NC}"
else
    echo -e "${RED}✗ Health check failed${NC}"
    docker logs rlm-test
    docker rm -f rlm-test 2>/dev/null || true
    exit 1
fi

# Cleanup
echo -e "\n${YELLOW}Cleaning up test container...${NC}"
docker rm -f rlm-test

echo -e "\n${GREEN}✅ All tests passed!${NC}"
echo ""
echo "Next steps:"
echo "  1. Prepare your 1C sources in ./data/ directory"
echo "  2. Run: docker-compose up -d"
echo "  3. Add to your MCP config:"
echo "     {\"mcpServers\": {\"rlm-tools-bsl\": {\"type\": \"http\", \"url\": \"http://127.0.0.1:9000/mcp\"}}}"
echo ""
echo "For more details, see docs/DOCKER.md"
