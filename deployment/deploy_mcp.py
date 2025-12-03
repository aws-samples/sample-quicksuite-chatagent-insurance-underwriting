#!/usr/bin/env python3
"""
Unified MCP Server Deployment Script
Handles both full deployment and code updates
"""

import boto3
import json
import time
import os
import sys
import argparse
from boto3.session import Session
from bedrock_agentcore_starter_toolkit import Runtime
from config_manager import config

def check_existing_mcp_server(mcp_server_name, region):
    """Check if MCP server exists in AgentCore"""
    print(f"\n🔍 CHECKING EXISTING MCP SERVER")
    print(f"   Server Name: {mcp_server_name}")
    print(f"   Region: {region}")
    print(f"   Action: Querying Bedrock AgentCore for existing runtime")
    
    try:
        agentcore_client = boto3.client('bedrock-agentcore-control', region_name=region)
        
        agent_runtime_id = None
        agent_arn = None
        next_token = None
        
        while True:
            response = agentcore_client.list_agent_runtimes(nextToken=next_token) if next_token else agentcore_client.list_agent_runtimes()
            
            for runtime in response.get('agentRuntimes', []):
                if runtime['agentRuntimeName'] == mcp_server_name:
                    agent_runtime_id = runtime['agentRuntimeId']
                    agent_arn = runtime['agentRuntimeArn']
                    break
            
            if agent_runtime_id or 'nextToken' not in response:
                break
            next_token = response['nextToken']
        
        if not agent_runtime_id:
            print(f"   Result: No existing MCP server found")
            return None
        
        print(f"   ✅ Found existing runtime: {agent_runtime_id}")
        print(f"   Fetching detailed configuration...")
        
        runtime_details = agentcore_client.get_agent_runtime(
            agentRuntimeId=agent_runtime_id,
            agentRuntimeVersion='1'
        )
        
        execution_role_arn = runtime_details['roleArn']
        execution_role_name = execution_role_arn.split('/')[-1]
        
        print(f"   Execution Role ARN: {execution_role_arn}")
        print(f"   Execution Role Name: {execution_role_name}")
        
        auth_config = runtime_details.get('authorizerConfiguration', {})
        jwt_auth = auth_config.get('customJWTAuthorizer', {})
        
        cognito_config = {
            'discovery_url': jwt_auth.get('discoveryUrl', ''),
            'service_client_id': jwt_auth.get('allowedClients', [''])[0] if jwt_auth.get('allowedClients') else ''
        }
        
        print(f"\n✅ EXISTING MCP SERVER DETAILS:")
        print(f"   Runtime ID: {agent_runtime_id}")
        print(f"   Agent ARN: {agent_arn}")
        print(f"   Execution Role: {execution_role_name}")
        print(f"   Authentication: Cognito JWT (Client ID: {cognito_config['service_client_id'][:8]}...)")
        
        return {
            'agent_arn': agent_arn,
            'agent_runtime_id': agent_runtime_id,
            'execution_role_name': execution_role_name,
            'execution_role_arn': execution_role_arn,
            'cognito_config': cognito_config
        }
    except Exception as e:
        print(f"⚠️  Error checking AgentCore - check permissions and configuration")
        return None

def setup_cognito_user_pool():
    """Setup Cognito for MCP server authentication"""
    print(f"\n🔐 CREATING COGNITO AUTHENTICATION RESOURCES")
    print(f"   Purpose: OAuth 2.0 authentication for MCP server")
    print(f"   Grant Type: client_credentials (service-to-service)")
    
    boto_session = Session()
    region = boto_session.region_name
    cognito_client = boto3.client('cognito-idp', region_name=region)
    secrets_client = boto3.client('secretsmanager', region_name=region)
    
    pool_name = config.cognito_user_pool_name
    
    try:
        print(f"\n   Creating Cognito User Pool")
        print(f"   Pool Name: {pool_name}")
        print(f"   Password Policy: Minimum 8 characters")
        
        user_pool_response = cognito_client.create_user_pool(
            PoolName=pool_name,
            Policies={'PasswordPolicy': {'MinimumLength': 8}}
        )
        pool_id = user_pool_response['UserPool']['Id']
        print(f"   ✅ User Pool Created successfully")
        
        domain_prefix = f"nova-insurance-mcp-{os.urandom(4).hex()}"
        
        print(f"\n   Creating Cognito Domain")
        print(f"   Domain Prefix: {domain_prefix}")
        print(f"   Purpose: OAuth 2.0 token endpoint")
        
        cognito_client.create_user_pool_domain(Domain=domain_prefix, UserPoolId=pool_id)
        print(f"   ✅ Domain Created: {domain_prefix}.auth.{region}.amazoncognito.com")
        
        print(f"\n   Creating Resource Server (API Scopes)")
        print(f"   API Identifier: {config.oauth_api_identifier}")
        print(f"   Scopes: read, write")
        
        cognito_client.create_resource_server(
            UserPoolId=pool_id,
            Identifier=config.oauth_api_identifier,
            Name="Nova Insurance Underwriting API",
            Scopes=[
                {'ScopeName': 'read', 'ScopeDescription': 'Read access to insurance data'},
                {'ScopeName': 'write', 'ScopeDescription': 'Write access for underwriting decisions'}
            ]
        )
        print(f"   ✅ Resource Server Created with OAuth scopes")
        
        print(f"\n   Creating Service Client (OAuth Credentials)")
        print(f"   Client Name: NovaInsuranceServiceClient")
        print(f"   OAuth Flow: client_credentials")
        print(f"   Generating client secret...")
        
        service_client_response = cognito_client.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName='NovaInsuranceServiceClient',
            GenerateSecret=True,
            SupportedIdentityProviders=['COGNITO'],
            AllowedOAuthFlows=['client_credentials'],
            AllowedOAuthScopes=[f'{config.oauth_api_identifier}/read', f'{config.oauth_api_identifier}/write'],
            AllowedOAuthFlowsUserPoolClient=True
        )
        
        client_id = service_client_response['UserPoolClient']['ClientId']
        client_secret = service_client_response['UserPoolClient']['ClientSecret']
        print(f"   ✅ Service Client Created: {client_id[:8]}...")
        print(f"   ⚠️  Client secret generated (stored securely in Secrets Manager)")
        
        oauth_token_url = f"https://{domain_prefix}.auth.{region}.amazoncognito.com/oauth2/token"
        discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
        
        print(f"\n   Storing Credentials in AWS Secrets Manager")
        secret_name = f'{config.mcp_server_name}/cognito/credentials'
        print(f"   Secret Name: {secret_name}")
        print(f"   Purpose: Secure storage of OAuth credentials (Client ID + Secret)")
        secret_value = {
            'pool_id': pool_id,
            'service_client_id': client_id,
            'service_client_secret': client_secret,
            'discovery_url': discovery_url,
            'oauth_token_url': oauth_token_url,
            'domain_prefix': domain_prefix
        }
        
        secret_response = secrets_client.create_secret(
            Name=secret_name,
            SecretString=json.dumps(secret_value)
        )
        print(f"   ✅ Secret Created.")
        
        print(f"\n✅ COGNITO AUTHENTICATION SETUP COMPLETE")
        print(f"   Token URL: {oauth_token_url}")
        print(f"   Discovery URL: {discovery_url}")
        print(f"   ⚠️  Credentials stored in Secrets Manager (name redacted for security)")
        
        return {
            'pool_id': pool_id,
            'service_client_id': client_id,
            'service_client_secret': client_secret,
            'discovery_url': discovery_url,
            'oauth_token_url': oauth_token_url,
            'domain_prefix': domain_prefix,
            'secret_arn': secret_response['ARN']
        }
    except cognito_client.exceptions.LimitExceededException:
        print("❌ Cognito resource limit exceeded - contact AWS support")
        return None
    except cognito_client.exceptions.InvalidParameterException as e:
        print(f"❌ Invalid Cognito configuration: {e}")
        return None
    except Exception as e:
        print(f"❌ Cognito setup error: {type(e).__name__}")
        return None

def create_quicksuite_integration_doc(agent_endpoint, cognito_config, region):
    """Create Quick Suite integration document with actual deployment values"""
    from datetime import datetime
    
    doc_content = f"""# Amazon Quick Suite Integration Guide
## Insurance Underwriting Expert MCP Server

**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**Region:** {region}  
**MCP Server:** Insurance Underwriting Expert

---

## Overview

This guide walks you through integrating the Insurance Underwriting Expert MCP Server with Amazon Quick Suite. The integration enables natural language queries for risk assessment, medical underwriting, fraud detection, and portfolio analytics.

**Available Tools:**
- `enterprise_risk_assessment` - Comprehensive applicant risk analysis
- `enterprise_medical_analysis` - Medical record evaluation
- `enterprise_fraud_detection` - Claims fraud pattern detection
- `enterprise_underwriting_decision` - Complete underwriting decisions
- `enterprise_analytics` - Portfolio analytics and insights
- `health_check` - System connectivity verification

---

## Part 1: Create MCP Connection

### Step 1: Access Quick Suite Integrations

1. Log in to Amazon Quick Suite console
2. Navigate to **Integrations** → **Actions**
3. Click **Set up a new integration**
4. Select **Model Context Protocol**

### Step 2: Configure Connection Details

**Basic Information:**
```
Name: Insurance Underwriting Expert
Description: MCP Server for AI-powered insurance underwriting and risk assessment
MCP Server Endpoint: {agent_endpoint}
```

Click **Next**

### Step 3: Configure Authentication

**Authentication Type:** Service-to-service OAuth

**OAuth Credentials:**
```
Client ID: {cognito_config['service_client_id']}
Token URL: {cognito_config['oauth_token_url']}
```

**Client Secret:** Retrieve from AWS Secrets Manager (contact administrator for secret name).

⚠️ **Security Note:** Never expose the client secret in logs or documentation.

Click **Create and Continue**

### Step 4: Test the Connection

1. Navigate to the newly created connection
2. Click **Test action APIs**
3. Verify all 6 tools are accessible:
   - ✓ enterprise_risk_assessment
   - ✓ enterprise_medical_analysis
   - ✓ enterprise_fraud_detection
   - ✓ enterprise_underwriting_decision
   - ✓ enterprise_analytics
   - ✓ health_check

---

## Part 2: Create Chat Agent


1. Navigate to **Chat agents** in Quick Suite
2. Click **Create chat agent**
3. Configure the following fields:

### Basic Configuration

**Name:**
```
Insurance Underwriting Expert
```

**Description:**
```
AI-powered Insurance Underwriting Analyst providing transparent risk assessment, 
medical underwriting, fraud detection, and portfolio analytics with complete 
audit trails for regulatory compliance.
```

### Agent Identity

```
You are Nova, an AI-powered Insurance Underwriting Analyst specializing in 
explainable risk assessment and fraud detection. You have access to enterprise 
insurance data including 1000+ applicant profiles, medical records, and claims 
history through advanced reasoning capabilities. Your primary role is to provide 
transparent, step-by-step analysis of insurance decisions with complete audit 
trails for regulatory compliance.
```

### Persona Instructions
```
You are a senior insurance underwriting expert with deep analytical capabilities and transparent reasoning. When users ask questions about insurance data, risk assessment, or underwriting decisions, follow these guidelines:

Core Behavior:
- Always show your complete reasoning process before providing conclusions
- Break down complex insurance decisions into clear, logical steps
- Reference specific data points from applicant profiles, medical records, and claims when available
- Provide risk scores with detailed explanations of contributing factors
- Explain the business impact and regulatory implications of your recommendations

Response Structure:
- Start with "Let me analyze this step-by-step" for complex queries
- Show your reasoning process with numbered steps
- Reference specific applicant IDs, claim IDs, or data points when applicable
- Provide clear recommendations with confidence levels
- Include regulatory compliance considerations
- Offer follow-up questions to deepen the analysis

Expertise Areas:
- Risk assessment and scoring methodology
- Medical underwriting and health condition evaluation
- Fraud detection patterns and suspicious claim analysis
- Premium calculation and pricing strategies
- Regulatory compliance and audit requirements
- Portfolio analytics and business intelligence

Communication Style:
- Professional but approachable tone
- Use insurance industry terminology appropriately
- Provide both technical details and business-friendly summaries
- Always explain your confidence level in recommendations
- Acknowledge limitations and suggest additional data when needed

Data Handling:
- Reference real applicant and claim IDs when analyzing specific cases
- Explain how you weight different risk factors in your analysis
- Show comparative analysis between similar cases
- Highlight unusual patterns or outliers that need attention
- Provide actionable insights for underwriters and business leaders

Remember: Your transparency and reasoning capabilities are your key differentiators. 
Users rely on you not just for answers, but for understanding why those answers 
make business sense.
```

### Link Actions

1. Under **ACTIONS** section, click **Link actions**
2. Select the connection: **Insurance Underwriting Expert**
3. Click **Create chat agent**

---

## Part 3: Test Your Chat Agent

### Quick Verification

Start with these simple queries to verify connectivity:

```
What's our overall portfolio health?
Run a health check on the system
```

### Sample Queries by Category

#### Risk Assessment & Underwriting
```
Assess risk for applicant APP-0900 - is this a good candidate for life insurance?
What's the risk profile for smokers with BMI over 30 in our portfolio?
Show me all applicants with poor health status and family history of diabetes
Compare risk scores: nurses vs doctors vs sales representatives
Which applicants over age 60 have the lowest risk profiles?
```

#### Medical Underwriting
```
Analyze medical records for APP-0002 - any red flags for underwriting?
Show applicants with hypertension and high cholesterol levels
Which applicants have multiple hospitalizations in their medical history?
Review chronic conditions across all applicants - what's our exposure?
Find applicants with blood pressure over 160/90 and BMI over 32
```

#### Claims & Fraud Detection
```
Investigate claim CLM-0492 - does it show fraud indicators?
Show all denied claims with fraud indicators above 2
Which claims were filed within 60 days of policy start date?
Analyze auto accident claims vs medical claims - which have higher fraud rates?
Find claims over $90,000 that were approved - justify these decisions
```

#### Portfolio Analytics
```
What's our overall portfolio health and risk distribution?
Show claim approval rates by claim type (Medical, Auto, Life)
Which occupations have the highest previous claims history?
Analyze income vs credit score correlation in our applicant pool
What percentage of our applicants are smokers with poor health status?
```

#### Underwriting Decisions
```
Should we approve APP-0831 for a $500,000 life insurance policy?
Make underwriting decision for APP-0255 - consider all risk factors
What premium adjustment would you recommend for applicants with 3+ previous claims?
Compare underwriting decisions for married vs widowed applicants over 60
```

---

## Troubleshooting

**Authentication Errors:**
- Verify Client ID and Secret in AWS Secrets Manager
- Ensure Token URL is correct

**Connection Timeout:**
- Check MCP Server Endpoint URL format
- Verify network connectivity to AWS region

**Tool Invocation Failures:**
- Confirm IAM permissions for DynamoDB, S3, and Bedrock
- Check CloudWatch logs for detailed error messages

**Data Access Issues:**
- Verify DynamoDB tables exist: `{config.applicants_table}`, `{config.claims_table}`
- Confirm S3 bucket access: `{config.s3_bucket_name}`

---



```
"""
    
    # Write to docs directory
    docs_dir = '../docs'
    if not os.path.exists(docs_dir):
        os.makedirs(docs_dir)
    
    doc_path = os.path.join(docs_dir, 'QUICK_SUITE_INTEGRATION.md')
    with open(doc_path, 'w', encoding='utf-8') as f:
        f.write(doc_content)
    
    print(f"\n📄 Quick Suite integration document created: {doc_path}")

def add_permissions(execution_role_name, region):
    """Add DynamoDB, S3, and Bedrock permissions to execution role"""
    print(f"\n🔒 ADDING IAM PERMISSIONS TO EXECUTION ROLE")
    print(f"   Role Name: {execution_role_name}")
    print(f"   Purpose: Grant MCP server access to enterprise data sources")
    
    try:
        iam_client = boto3.client('iam', region_name=region)
        account_id = boto3.client('sts').get_caller_identity()['Account']
        print(f"   AWS Account: {account_id}")
        print(f"   Region: {region}")
        
        dynamodb_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:DescribeTable"],
                "Resource": [
                    f"arn:aws:dynamodb:{region}:{account_id}:table/{config.applicants_table}",
                    f"arn:aws:dynamodb:{region}:{account_id}:table/{config.claims_table}",
                    f"arn:aws:dynamodb:{region}:{account_id}:table/nova-insurance-*"
                ]
            }]
        }
        
        s3_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{config.s3_bucket_name}/*",
                    f"arn:aws:s3:::{config.s3_bucket_name}"
                ]
            }]
        }
        
        bedrock_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:Converse"],
                "Resource": [f"arn:aws:bedrock:*::foundation-model/{config.model_id}"]
            }]
        }
        
        print(f"\n   Policy 1: NovaInsuranceDynamoDBAccess")
        print(f"   Actions: GetItem, Query, Scan, DescribeTable")
        print(f"   Resources:")
        print(f"     - {config.applicants_table}")
        print(f"     - {config.claims_table}")
        iam_client.put_role_policy(RoleName=execution_role_name, PolicyName="NovaInsuranceDynamoDBAccess", PolicyDocument=json.dumps(dynamodb_policy))
        print(f"   ✅ DynamoDB policy attached")
        
        print(f"\n   Policy 2: NovaInsuranceS3Access")
        print(f"   Actions: GetObject")
        print(f"   Resources: {config.s3_bucket_name}/*")
        iam_client.put_role_policy(RoleName=execution_role_name, PolicyName="NovaInsuranceS3Access", PolicyDocument=json.dumps(s3_policy))
        print(f"   ✅ S3 policy attached")
        
        print(f"\n   Policy 3: NovaInsuranceBedrockAccess")
        print(f"   Actions: InvokeModel, Converse")
        print(f"   Resources: amazon.nova-lite-1-5-v1:0")
        iam_client.put_role_policy(RoleName=execution_role_name, PolicyName="NovaInsuranceBedrockAccess", PolicyDocument=json.dumps(bedrock_policy))
        print(f"   ✅ Bedrock policy attached")
        
        print(f"\n✅ ALL IAM PERMISSIONS SUCCESSFULLY ATTACHED TO ROLE: {execution_role_name}")
    except iam_client.exceptions.EntityAlreadyExistsException:
        print(f"\n✅ IAM PERMISSIONS ALREADY EXIST (policies already attached)")
    except Exception as e:
        print(f"⚠️  Warning: Could not add permissions: {e}")

def deploy_mcp_server():
    """Deploy or update MCP server"""
    # First step: Copy config file to deployment directory for Docker build
    import shutil
    config_source = '../config/enterprise_config.yaml'
    config_dest = './enterprise_config.yaml'
    
    if os.path.exists(config_source):
        shutil.copy2(config_source, config_dest)
        print(f"📋 Copied config file: {config_source} → {config_dest}")
    else:
        print(f"⚠️  Config file not found at {config_source}, using defaults")
    
    boto_session = Session()
    region = boto_session.region_name
    mcp_server_name = config.mcp_server_name
    
    print("="*80)
    print(f"🚀 MCP SERVER DEPLOYMENT")
    print("="*80)
    print(f"\n🎯 DEPLOYMENT CONFIGURATION")
    print(f"   MCP Server Name: {mcp_server_name}")
    print(f"   AWS Region: {region}")
    print(f"   DynamoDB Tables: {config.applicants_table}, {config.claims_table}")
    print(f"   S3 Bucket: {config.s3_bucket_name}")
    print(f"   Cognito Pool: {config.cognito_user_pool_name}")
    
    # Check existing server
    existing_config = check_existing_mcp_server(mcp_server_name, region)
    
    if existing_config:
        print("\n" + "="*80)
        print(f"⚠️  EXISTING MCP SERVER DETECTED")
        print("="*80)
        print(f"   Server Name: {mcp_server_name}")
        print(f"   Runtime ID: {existing_config['agent_runtime_id']}")
        print(f"   Region: {region}")
        print(f"   Execution Role: {existing_config['execution_role_name']}")
        print(f"\n   UPDATE MODE: Will update server code only (no resource recreation)")
        print(f"   PRESERVED: Cognito, IAM roles, authentication configuration")
        
        response = input("\n❓ Do you want to update the existing server? (y/n): ").strip().lower()
        
        if response not in ['y', 'yes']:
            if response not in ['n', 'no']:
                print("\n⚠️  Invalid input. Please enter 'y' or 'n'")
            print("\n❌ DEPLOYMENT CANCELLED BY USER")
            sys.exit(2)
        
        print("\n" + "="*80)
        print("🔄 UPDATE MODE: Deploying new code to existing server")
        print("="*80)
        cognito_config = existing_config['cognito_config']
        execution_role_name = existing_config['execution_role_name']
        reuse_existing = True
    else:
        print("\n" + "="*80)
        print("🆕 NEW DEPLOYMENT MODE: Creating all resources from scratch")
        print("="*80)
        cognito_config = setup_cognito_user_pool()
        if not cognito_config:
            raise Exception("Failed to setup Cognito")
        execution_role_name = None
        reuse_existing = False
    
    # Validate files
    for file_path in ['mcpserver.py', 'requirements.txt']:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Required file {file_path} not found")
    
    # Configure AgentCore (suppress verbose output)
    import contextlib
    import io
    
    print(f"\n⚙️ CONFIGURING AGENTCORE RUNTIME")
    print(f"   Initializing Bedrock AgentCore SDK...")
    
    # Capture AgentCore verbose output
    f = io.StringIO()
    with contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
        agentcore_runtime = Runtime()
    
    print(f"   ✅ AgentCore SDK initialized")
    
    print(f"\n   Configuration Parameters:")
    config_params = {
        "entrypoint": "mcpserver.py",
        "agent_name": mcp_server_name,
        "auto_create_execution_role": not reuse_existing,
        "region": region,
        "authorizer_configuration": {
            "customJWTAuthorizer": {
                "allowedClients": [cognito_config['service_client_id']],
                "discoveryUrl": cognito_config['discovery_url'],
            }
        },
        "protocol": "MCP",
        "auto_create_ecr": True
    }
    
    if reuse_existing and existing_config:
        config_params["execution_role"] = existing_config['execution_role_arn']
        print(f"   - Execution Role: {existing_config['execution_role_arn']} (reusing existing)")
    else:
        print(f"   - Execution Role: Auto-create new role")
    
    print(f"   - Entrypoint: {config_params['entrypoint']}")
    print(f"   - Agent Name: {config_params['agent_name']}")
    print(f"   - Protocol: {config_params['protocol']}")
    print(f"   - Region: {config_params['region']}")
    print(f"   - ECR: Auto-create repository")
    print(f"   - Authentication: Custom JWT (Cognito)")
    print(f"   - Allowed Client: {cognito_config['service_client_id']}")
    
    # Configure with suppressed output
    with contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
        agentcore_runtime.configure(**config_params)
    print(f"\n   ✅ AgentCore configuration validated and applied")
    
    # Verify IAM roles are accessible before launching (only for new deployments)
    if not reuse_existing:
        print(f"\n⏳ VERIFYING IAM ROLES PROPAGATION")
        print(f"   Purpose: Ensure IAM roles are globally available before CodeBuild starts")
        print(f"   Method: Retry with backoff (max 2 minutes)")
        
        iam_client = boto3.client('iam', region_name=region)
        account_id = boto3.client('sts').get_caller_identity()['Account']
        
        # Expected role names that AgentCore will create
        runtime_role_prefix = f"AmazonBedrockAgentCoreSDKRuntime-{region}"
        codebuild_role_prefix = f"AmazonBedrockAgentCoreSDKCodeBuild-{region}"
        
        max_attempts = 24  # 2 minutes with 5-second intervals
        attempt = 0
        roles_verified = False
        
        while attempt < max_attempts and not roles_verified:
            attempt += 1
            try:
                print(f"   Attempt {attempt}/{max_attempts}: Checking IAM roles...", end='', flush=True)
                
                # List roles and check if our expected roles exist
                response = iam_client.list_roles()
                role_names = [role['RoleName'] for role in response.get('Roles', [])]
                
                runtime_role_exists = any(runtime_role_prefix in name for name in role_names)
                codebuild_role_exists = any(codebuild_role_prefix in name for name in role_names)
                
                if runtime_role_exists and codebuild_role_exists:
                    print(f" ✅ Verified")
                    roles_verified = True
                else:
                    print(f" Roles not ready yet")
                    time.sleep(5) # nosec
                    
            except Exception as e:
                print(f" Error: {e}")
                time.sleep(5) # nosec
        
        if roles_verified:
            print(f"   ✅ IAM roles verified and ready (took {attempt * 5} seconds)")
        else:
            print(f"   ⚠️ Verification timed out - proceeding anyway (roles may still be propagating)")
    
    # Launch with suppressed output
    print(f"\n🚀 DEPLOYING TO BEDROCK AGENTCORE RUNTIME")
    if not reuse_existing:
        print(f"   Creating new execution role (auto-created by AgentCore)...")
    print(f"   Building Docker container image...")
    print(f"   Pushing to Amazon ECR...")
    print(f"   {'Creating' if not reuse_existing else 'Updating'} AgentCore runtime...")
    
    with contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
        launch_result = agentcore_runtime.launch(auto_update_on_conflict=True)
    
    print(f"   ✅ AgentCore runtime {'created' if not reuse_existing else 'updated'}")
    print(f"   Runtime ARN: {launch_result.agent_arn}")
    
    # Verify IAM role immediately after creation (for new deployments)
    agentcore_client = boto3.client('bedrock-agentcore-control', region_name=region)
    runtime_id = launch_result.agent_arn.split('/')[-1]
    
    if not reuse_existing:
        print(f"\n⏳ VERIFYING IAM ROLE CREATION AND PROPAGATION")
        print(f"   Purpose: Ensure execution role created by AgentCore is accessible")
        print(f"   Method: Retry with backoff (max 2 minutes)")
        
        max_attempts = 24  # 2 minutes with 5-second intervals
        attempt = 0
        role_verified = False
        
        while attempt < max_attempts and not role_verified:
            attempt += 1
            try:
                print(f"   Attempt {attempt}/{max_attempts}: Checking role existence...", end='', flush=True)
                
                runtime_details = agentcore_client.get_agent_runtime(
                    agentRuntimeId=runtime_id,
                    agentRuntimeVersion='1'
                )
                
                execution_role_arn = runtime_details.get('roleArn')
                if not execution_role_arn:
                    print(" Role ARN not yet available")
                    time.sleep(5) # nosec
                    continue
                
                execution_role_name = execution_role_arn.split('/')[-1]
                
                # Verify role is accessible via IAM
                iam_client = boto3.client('iam', region_name=region)
                iam_client.get_role(RoleName=execution_role_name)
                
                print(f" ✅ Verified")
                print(f"   Role ARN: {execution_role_arn}")
                print(f"   Role Name: {execution_role_name}")
                role_verified = True
                
            except agentcore_client.exceptions.ResourceNotFoundException:
                print(" Runtime not ready yet")
                time.sleep(5) # nosec
            except iam_client.exceptions.NoSuchEntityException:
                print(" Role not propagated yet")
                time.sleep(5) # nosec
            except Exception as e:
                print(f" Error: {e}")
                time.sleep(5) # nosec
        
        if not role_verified:
            raise Exception(f"IAM role failed to propagate after {max_attempts * 5} seconds")
        
        print(f"\n   ✅ IAM ROLE VERIFIED AND READY (took {attempt * 5} seconds)")
        
        # Add permissions to newly created role
        add_permissions(execution_role_name, region)
    else:
        # In update mode, fetch current role from AgentCore API and verify permissions
        print(f"\n🔍 RETRIEVING CURRENT EXECUTION ROLE FROM AGENTCORE")
        print(f"   Purpose: Get current role for this MCP server runtime")
        
        runtime_details = agentcore_client.get_agent_runtime(
            agentRuntimeId=runtime_id,
            agentRuntimeVersion='1'
        )
        
        current_execution_role_arn = runtime_details['roleArn']
        current_execution_role_name = current_execution_role_arn.split('/')[-1]
        
        print(f"   Current Role ARN: {current_execution_role_arn}")
        print(f"   Current Role Name: {current_execution_role_name}")
        print(f"\n🔍 VERIFYING IAM PERMISSIONS (Update mode)")
        print(f"   Ensuring policies are attached to current role")
        
        add_permissions(current_execution_role_name, region)
    
    # Construct endpoint and create integration doc
    agent_arn_encoded = launch_result.agent_arn.replace(':', '%3A').replace('/', '%2F')
    agent_endpoint = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{agent_arn_encoded}/invocations?qualifier=DEFAULT"
    
    # Create Quick Suite integration document
    if not reuse_existing:
        create_quicksuite_integration_doc(agent_endpoint, cognito_config, region)
    
    print("\n" + "="*80)
    print(f"✅ {'UPDATE' if reuse_existing else 'DEPLOYMENT'} COMPLETED SUCCESSFULLY!")
    print("="*80)
    print(f"\n📦 DEPLOYED RESOURCES:")
    if not reuse_existing:
        print(f"   ✅ Cognito User Pool: {config.cognito_user_pool_name}")
        print(f"   ✅ OAuth Client: Created with client_credentials flow")
        print(f"   ✅ Secrets Manager: {config.mcp_server_name}/cognito/credentials")
        print(f"   ✅ IAM Execution Role: Auto-created by AgentCore")
        print(f"   ✅ IAM Policies: DynamoDB, S3, Bedrock access attached")
    print(f"   ✅ AgentCore Runtime: {mcp_server_name}")
    print(f"   ✅ MCP Server Endpoint: {agent_endpoint}")
    print(f"   ✅ Docker Image: Pushed to Amazon ECR")
    print(f"\n📊 DATA SOURCES:")
    print(f"   DynamoDB: {config.applicants_table}, {config.claims_table}")
    print(f"   S3: {config.s3_bucket_name}/{config.medical_records_prefix}/")
    print(f"\n📝 NEXT STEPS:")
    print(f"   1. Test: python tests/test_mcp_functionality.py")
    if not reuse_existing:
        print(f"   2. Integration guide: docs/QUICK_SUITE_INTEGRATION.md")
        print(f"   3. Connect Quick Suite using OAuth credentials")

if __name__ == "__main__":
    try:
        deploy_mcp_server()
    except KeyboardInterrupt:
        print("\n\n" + "="*80)
        print("❌ DEPLOYMENT CANCELLED BY USER (Ctrl+C)")
        print("="*80)
        sys.exit(2)
    except Exception as e:
        print("\n" + "="*80)
        print(f"❌ DEPLOYMENT FAILED")
        print("="*80)
        print(f"   Error: {e}")
        print(f"\n🔧 TROUBLESHOOTING:")
        print(f"   1. Check AWS credentials: aws sts get-caller-identity")
        print(f"   2. Verify Bedrock permissions in IAM")
        print(f"   3. Ensure config/enterprise_config.yaml is correct")
        print(f"   4. Check CloudWatch logs for detailed error messages")
        sys.exit(1)
