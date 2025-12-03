#!/usr/bin/env python3
"""
Test All Nova Lite 2.0 MCP Server Tools
"""

import json
import boto3
import requests
import urllib.parse
import logging
import sys
import os
from datetime import datetime
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'deployment'))
from config_manager import config

# Configure secure logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

def get_oauth_token():
    """Get JWT token from Cognito for MCP server authentication"""
    try:
        region = boto3.Session().region_name
        agentcore_client = boto3.client('bedrock-agentcore-control', region_name=region)
        cognito_client = boto3.client('cognito-idp', region_name=region)
        
        # Get runtime details from AgentCore API with pagination
        agent_runtime_id = None
        next_token = None
        
        while True:
            if next_token:
                response = agentcore_client.list_agent_runtimes(nextToken=next_token)
            else:
                response = agentcore_client.list_agent_runtimes()
            
            for runtime in response.get('agentRuntimes', []):
                if runtime['agentRuntimeName'] == config.mcp_server_name:
                    agent_runtime_id = runtime['agentRuntimeId']
                    break
            
            if agent_runtime_id or 'nextToken' not in response:
                break
            next_token = response['nextToken']
        
        if not agent_runtime_id:
            raise Exception(f"MCP server '{config.mcp_server_name}' not found")
        
        runtime_details = agentcore_client.get_agent_runtime(
            agentRuntimeId=agent_runtime_id,
            agentRuntimeVersion='1'
        )
        
        # Extract Cognito config from AgentCore
        auth_config = runtime_details.get('authorizerConfiguration', {})
        jwt_auth = auth_config.get('customJWTAuthorizer', {})
        discovery_url = jwt_auth.get('discoveryUrl', '')
        client_id = jwt_auth.get('allowedClients', [''])[0]
        
        # Extract pool ID from discovery URL
        if discovery_url and 'amazonaws.com/' in discovery_url:
            pool_id = discovery_url.split('amazonaws.com/')[1].split('/')[0]
        else:
            raise Exception("Could not extract Cognito pool ID from AgentCore")
        
        # Get client secret directly from Cognito
        client_response = cognito_client.describe_user_pool_client(
            UserPoolId=pool_id,
            ClientId=client_id
        )
        client_secret = client_response['UserPoolClient'].get('ClientSecret')
        
        # Get domain for token endpoint from user pool
        pool_response = cognito_client.describe_user_pool(UserPoolId=pool_id)
        domain = pool_response['UserPool'].get('Domain')
        
        if domain:
            token_endpoint = f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token"
        else:
            raise Exception("Cognito user pool has no domain configured")
        
        # Make OAuth token request
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'grant_type': 'client_credentials'
        }
        
        token_response = requests.post(
            token_endpoint, 
            headers=headers, 
            data=data,
            auth=(client_id, client_secret)
        )
        token_response.raise_for_status()
        
        return token_response.json()['access_token']
        
    except requests.exceptions.RequestException as e:
        logger.error(f"OAuth request failed: {type(e).__name__}")
        return None
    except Exception as e:
        logger.error(f"Failed to get JWT token: {type(e).__name__}")
        return None

def get_runtime_details():
    """Get complete runtime details from AgentCore API"""
    try:
        region = boto3.Session().region_name
        agentcore_client = boto3.client('bedrock-agentcore-control', region_name=region)
        
        # Find matching runtime
        agent_runtime_id = None
        agent_arn = None
        next_token = None
        
        while True:
            if next_token:
                response = agentcore_client.list_agent_runtimes(nextToken=next_token)
            else:
                response = agentcore_client.list_agent_runtimes()
            
            for runtime in response.get('agentRuntimes', []):
                if runtime['agentRuntimeName'] == config.mcp_server_name:
                    agent_runtime_id = runtime['agentRuntimeId']
                    agent_arn = runtime['agentRuntimeArn']
                    break
            
            if agent_runtime_id or 'nextToken' not in response:
                break
            next_token = response['nextToken']
        
        if not agent_runtime_id:
            raise Exception(f"MCP server '{config.mcp_server_name}' not found in AgentCore")
        
        # Build endpoint
        encoded_arn = urllib.parse.quote(agent_arn, safe='')
        endpoint = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"
        
        return {
            'server_name': config.mcp_server_name,
            'runtime_id': agent_runtime_id,
            'endpoint': endpoint,
            'region': region
        }
        
    except Exception as e:
        logger.error(f"Failed to get runtime details: {e}")
        return None

def get_sample_data():
    """Get sample applicant and claim IDs"""
    try:
        dynamodb = boto3.resource('dynamodb')
        
        # Get sample applicant
        applicants_table = dynamodb.Table(config.applicants_table)
        applicants_response = applicants_table.scan(Limit=1)
        applicants = applicants_response.get('Items', [])
        
        # Get sample claim
        claims_table = dynamodb.Table(config.claims_table)
        claims_response = claims_table.scan(Limit=1)
        claims = claims_response.get('Items', [])
        
        if not applicants or not claims:
            raise Exception("No sample data found")
            
        return applicants[0]['applicant_id'], claims[0]['claim_id']
        
    except Exception as e:
        raise Exception(f"Failed to get sample data: {type(e).__name__}")

def test_mcp_tool(endpoint, token, tool_name, arguments=None):
    """Test MCP tool"""
    if arguments is None:
        arguments = {}
    
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream'
    }
    
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        },
        "id": 1
    }
    
    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=180)
        response.raise_for_status()
        
        content = response.text
        if content.startswith('data: '):
            data_line = content.split('\n')[0][6:]
            result = json.loads(data_line)
            return result.get('result', {})
        
        result = response.json()
        
        # Handle MCP JSON-RPC response format
        if 'result' in result and 'content' in result['result']:
            content = result['result']['content']
            if content and len(content) > 0 and 'text' in content[0]:
                tool_result = json.loads(content[0]['text'])
                return tool_result
        
        return result
        
    except Exception as e:
        return {"error": str(e), "status": "failed"}

def test_all_tools(endpoint, token, sample_applicant, sample_claim):
    """Test all 6 MCP server tools"""
    tools_to_test = [
        {
            "name": "health_check",
            "description": "Server health status",
            "args": {},
            "icon": "🔍"
        },
        {
            "name": "enterprise_risk_assessment", 
            "description": "Risk assessment with reasoning",
            "args": {"applicant_id": sample_applicant},
            "icon": "📊"
        },
        {
            "name": "enterprise_medical_analysis",
            "description": "Medical records analysis", 
            "args": {"applicant_id": sample_applicant},
            "icon": "🏥"
        },
        {
            "name": "enterprise_fraud_detection",
            "description": "Fraud detection analysis",
            "args": {"claim_id": sample_claim},
            "icon": "🔍"
        },
        {
            "name": "enterprise_underwriting_decision",
            "description": "Complete underwriting decision",
            "args": {"applicant_id": sample_applicant, "policy_type": "life", "coverage_amount": 500000},
            "icon": "⚖️"
        },
        {
            "name": "enterprise_analytics",
            "description": "Portfolio analytics",
            "args": {},
            "icon": "📈"
        }
    ]
    
    print(f"🛠️ Testing {len(tools_to_test)} MCP Tools:")
    print()
    
    results = {}
    for tool in tools_to_test:
        print(f"{tool['icon']} Testing {tool['name']}... ({tool['description']})")
        
        result = test_mcp_tool(endpoint, token, tool['name'], tool['args'])
        
        # Special handling for health_check tool
        if tool['name'] == 'health_check':
            if result and result.get('status') == 'healthy':
                print(f"   ✅ Success")
                results[tool['name']] = 'success'
            else:
                error_msg = result.get('error', 'Unknown error') if result else 'No response'
                print(f"   ❌ Failed: {error_msg}")
                results[tool['name']] = 'failed'
        else:
            # Standard handling for other tools
            if result and result.get('status') == 'success':
                print(f"   ✅ Success")
                results[tool['name']] = 'success'
            else:
                error_msg = result.get('error', 'Unknown error') if result else 'No response'
                print(f"   ❌ Failed: {error_msg}")
                results[tool['name']] = 'failed'
        print()
    
    return results

def get_complete_runtime_details():
    """Get complete runtime details from AgentCore API"""
    try:
        region = boto3.Session().region_name
        agentcore_client = boto3.client('bedrock-agentcore-control', region_name=region)
        
        # Find matching runtime
        agent_runtime_id = None
        agent_arn = None
        next_token = None
        
        while True:
            if next_token:
                response = agentcore_client.list_agent_runtimes(nextToken=next_token)
            else:
                response = agentcore_client.list_agent_runtimes()
            
            for runtime in response.get('agentRuntimes', []):
                if runtime['agentRuntimeName'] == config.mcp_server_name:
                    agent_runtime_id = runtime['agentRuntimeId']
                    agent_arn = runtime['agentRuntimeArn']
                    break
            
            if agent_runtime_id or 'nextToken' not in response:
                break
            next_token = response['nextToken']
        
        if not agent_runtime_id:
            raise Exception(f"MCP server '{config.mcp_server_name}' not found in AgentCore")
        
        # Get detailed runtime info
        runtime_details = agentcore_client.get_agent_runtime(
            agentRuntimeId=agent_runtime_id,
            agentRuntimeVersion='1'
        )
        
        # Extract auth config
        auth_config = runtime_details.get('authorizerConfiguration', {})
        jwt_auth = auth_config.get('customJWTAuthorizer', {})
        
        # Build endpoint
        encoded_arn = urllib.parse.quote(agent_arn, safe='')
        endpoint = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"
        
        return {
            'server_name': config.mcp_server_name,
            'runtime_id': agent_runtime_id,
            'endpoint': endpoint,
            'discovery_url': jwt_auth.get('discoveryUrl', ''),
            'allowed_clients': jwt_auth.get('allowedClients', []),
            'region': region
        }
        
    except Exception as e:
        logger.error("Failed to get runtime details - check AgentCore configuration")
        return None

def main():
    """Test all Nova Lite 2.0 MCP Server tools"""
    print("🧪 Nova Lite 2.0 MCP Server - All Tools Test")
    print()
    
    # Get complete runtime details first
    print("🔍 Connecting to AgentCore runtime...")
    runtime_details = get_complete_runtime_details()
    if not runtime_details:
        print("❌ Failed to connect to AgentCore runtime")
        return
    
    print("✅ Connected to AgentCore runtime")
    print(f"   MCP Server: {runtime_details['server_name']}")
    print(f"   Runtime ID: {runtime_details['runtime_id']}")
    print(f"   Endpoint: {runtime_details['endpoint']}")
    print(f"   Auth Discovery: {runtime_details['discovery_url']}")
    print(f"   Region: {runtime_details['region']}")
    print()
    
    # Get OAuth credentials
    print("🔐 Getting OAuth credentials...")
    token = get_oauth_token()
    if not token:
        print("❌ Failed to get OAuth token")
        return
    print("✅ Got OAuth credentials")
    print()
    
    # Get sample data
    print("📊 Getting sample data from database...")
    try:
        sample_applicant, sample_claim = get_sample_data()
        print(f"✅ Retrieved applicant: {sample_applicant}")
        print(f"✅ Retrieved claim: {sample_claim}")
    except Exception as e:
        print(f"❌ Failed to get sample data: {e}")
        return
    print()
    
    # Test all tools
    print("="*70)
    print("🛠️ MCP TOOLS VALIDATION")
    print("="*70)
    print()
    results = test_all_tools(runtime_details['endpoint'], token, sample_applicant, sample_claim)
    
    # Summary
    print("="*70)
    print("📊 TEST SUMMARY:")
    successful = sum(1 for r in results.values() if r == 'success')
    total = len(results)
    failed = total - successful
    print(f"   ✅ Successful: {successful}/{total} tools")
    if failed > 0:
        print(f"   ❌ Failed: {failed}/{total} tools")
    
    if successful == total:
        print("🎉 All tools working perfectly!")
        print("✅ MCP server is production-ready for Quick Suite integration")
    else:
        print("⚠️ Some tools failed - check individual errors above")
        print("✅ OAuth authentication and connectivity verified")
    print("="*70)
    
    # Functional tests for underwriting use case
    if successful == total:
        print("\n" + "="*70)
        print("🎯 FUNCTIONAL TESTS - UNDERWRITING USE CASE")
        print("="*70)
        
        print("\n🔍 Test 1: Risk Assessment with Reasoning")
        risk_result = test_mcp_tool(runtime_details['endpoint'], token, "enterprise_risk_assessment", {"applicant_id": sample_applicant})
        if risk_result and risk_result.get('status') == 'success':
            print("   ✅ Success")
            data = risk_result.get('data', {})
            reasoning = data.get('reasoning_process', '')
            assessment = data.get('risk_assessment', '')
            if reasoning:
                print(f"   🧠 Reasoning: {reasoning[:150]}...")
            if assessment:
                print(f"   📊 Assessment: {assessment[:150]}...")
        else:
            print("   ❌ Failed")
        
        print("\n🏥 Test 2: Medical Analysis")
        medical_result = test_mcp_tool(runtime_details['endpoint'], token, "enterprise_medical_analysis", {"applicant_id": sample_applicant})
        if medical_result and medical_result.get('status') == 'success':
            print("   ✅ Success")
            data = medical_result.get('data', {})
            analysis = data.get('medical_analysis', '')
            if analysis:
                print(f"   👨‍⚕️ Analysis: {analysis[:150]}...")
        else:
            print("   ❌ Failed")
        
        print("\n⚠️ Test 3: Fraud Detection")
        fraud_result = test_mcp_tool(runtime_details['endpoint'], token, "enterprise_fraud_detection", {"claim_id": sample_claim})
        if fraud_result and fraud_result.get('status') == 'success':
            print("   ✅ Success")
            data = fraud_result.get('data', {})
            fraud_analysis = data.get('fraud_analysis', '')
            if fraud_analysis:
                print(f"   🔎 Analysis: {fraud_analysis[:150]}...")
        else:
            print("   ❌ Failed")
        
        print("\n⚖️ Test 4: Underwriting Decision")
        decision_result = test_mcp_tool(runtime_details['endpoint'], token, "enterprise_underwriting_decision", {
            "applicant_id": sample_applicant,
            "policy_type": "life",
            "coverage_amount": 500000
        })
        if decision_result and decision_result.get('status') == 'success':
            print("   ✅ Success")
            data = decision_result.get('data', {})
            decision = data.get('underwriting_decision', '')
            if decision:
                print(f"   📝 Decision: {decision[:150]}...")
        else:
            print("   ❌ Failed")
        
        print("\n📊 Test 5: Portfolio Analytics")
        analytics_result = test_mcp_tool(runtime_details['endpoint'], token, "enterprise_analytics")
        if analytics_result and analytics_result.get('status') == 'success':
            print("   ✅ Success")
            data = analytics_result.get('data', {})
            report = data.get('analytics_report', '')
            if report:
                print(f"   📈 Report: {report[:150]}...")
        else:
            print("   ❌ Failed")
        
        print("\n" + "="*70)
        print("✅ FUNCTIONAL TESTS COMPLETED")
        print("="*70)

if __name__ == "__main__":
    main()