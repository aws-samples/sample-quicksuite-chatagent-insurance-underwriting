#!/usr/bin/env python3
"""
Generate bedrock_agentcore_nova.yaml with configurable values from enterprise_config.yaml
"""

import yaml
import os
import sys
from config_manager import config

def generate_agentcore_config():
    """Generate AgentCore configuration with enterprise config values"""
    
    # AgentCore configuration template
    agentcore_config = {
        'name': config.mcp_server_name,
        'entrypoint': 'mcpserver.py',
        'platform': 'linux/arm64',
        'container_runtime': 'none',
        'aws': {
            'execution_role_auto_create': True,
            'region': config.region,
            'network_configuration': {
                'network_mode': 'PUBLIC'
            },
            'protocol_configuration': {
                'server_protocol': 'MCP'
            },
            'observability': {
                'enabled': True
            }
        },
        'memory': {
            'mode': 'NO_MEMORY'
        },
        'authorizer_configuration': {
            'customJWTAuthorizer': {
                'discoveryUrl': '',
                'allowedClients': []
            }
        }
    }
    
    # Write to config directory
    config_path = '../config/bedrock_agentcore_nova.yaml'
    with open(config_path, 'w') as f:
        yaml.dump(agentcore_config, f, default_flow_style=False, sort_keys=False)
    
    print(f"✅ Generated AgentCore config: {config_path}")
    print(f"   MCP Server Name: {config.mcp_server_name}")
    print(f"   Region: {config.region}")

if __name__ == "__main__":
    try:
        generate_agentcore_config()
    except FileNotFoundError:
        print("❌ Config directory not found")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"❌ YAML generation error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error generating AgentCore config: {type(e).__name__}")
        sys.exit(1)