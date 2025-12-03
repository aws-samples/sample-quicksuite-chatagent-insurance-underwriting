#!/bin/bash

# Deploy Nova Lite 2.0 Insurance MCP Server with Virtual Environment
# This script handles everything automatically

set -e

echo "🚀 Nova Lite 2.0 Insurance MCP Server Deployment"
echo ""

# Check prerequisites
if [ ! -f "deployment/mcpserver.py" ]; then
    echo "❌ mcpserver.py not found in deployment directory"
    exit 1
fi

# Use shared virtual environment
source smart_insurance_agent_venv/bin/activate

# Validate AWS credentials
aws sts get-caller-identity > /dev/null || {
    echo "❌ AWS credentials not configured. Run 'aws configure' first."
    exit 1
}

cd deployment

# Clear AgentCore cache to ensure fresh deployment
if [ -f ".bedrock_agentcore.yaml" ]; then
    echo "🧹 Clearing AgentCore cache for fresh deployment..."
    rm -f .bedrock_agentcore.yaml
fi

# Always ensure Dockerfile has the correct dependencies
echo "📦 Preparing deployment package..."
    cat > Dockerfile << 'EOF'
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app

# All environment variables in one layer
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1 \
    AWS_REGION=us-east-1 \
    AWS_DEFAULT_REGION=us-east-1

# Copy requirements first for better caching
COPY requirements_docker.txt .

# Install all required dependencies from requirements file
RUN uv pip install -r requirements_docker.txt

# Copy entire project first
COPY . .

# Create config directory and copy config file
RUN mkdir -p /config
RUN cp ../config/enterprise_config.yaml /config/ 2>/dev/null || echo "Config file not found, using defaults"

# Signal that this is running in Docker for host binding logic
ENV DOCKER_CONTAINER=1

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 9000
EXPOSE 8000
EXPOSE 8080

# Copy entire project (respecting .dockerignore)
COPY . .

# Use the full module path
CMD ["opentelemetry-instrument", "python", "-m", "mcpserver"]
EOF

# Force fresh build by adding timestamp to trigger rebuild
TIMESTAMP=$(date +%s)
echo "# Build timestamp: $TIMESTAMP" >> Dockerfile

# Generate AgentCore config with enterprise values
python generate_agentcore_config.py > /dev/null

# Check if config was generated successfully
if [ ! -f "../config/bedrock_agentcore_nova.yaml" ]; then
    echo "❌ Failed to generate bedrock_agentcore_nova.yaml"
    exit 1
fi
if python deploy_mcp.py; then
    cd ..
    echo ""
    echo "🎉 Nova Lite 2.0 Insurance MCP Server deployed successfully!"
    echo ""
    echo "📋 Next steps:"
    echo "1. Test: python tests/test_mcp_functionality.py"
    echo "2. Integrate with Quick Suite"
    echo "3. Monitor via CloudWatch"
    echo ""
else
    EXIT_CODE=$?
    cd ..
    if [ $EXIT_CODE -eq 2 ]; then
        # Exit code 2 means user cancelled
        exit 0
    else
        echo "❌ Deployment failed"
        exit 1
    fi
fi