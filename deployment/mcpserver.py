#!/usr/bin/env python3
"""
Insurance Underwriting MCP Server
"""

import time
import os
import json
import boto3
import re
import logging
from botocore.config import Config
from mcp.server.fastmcp import FastMCP
from decimal import Decimal
# Try to import config, error out if not found
try:
    from config_manager import config
except ImportError:
    import sys
    print("❌ Error: config_manager not found - ensure enterprise_config.yaml is properly configured")
    sys.exit(1)

# Configure secure logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

def validate_applicant_id(applicant_id: str) -> bool:
    """Validate applicant ID format for security (prevents injection attacks)"""
    # Ensure ID follows exact pattern: APP-XXXX where X is a digit
    # This prevents SQL injection and ensures data integrity
    return bool(re.match(r'^APP-\d{4}$', applicant_id))

def validate_claim_id(claim_id: str) -> bool:
    """Validate claim ID format for security (prevents injection attacks)"""
    # Ensure ID follows exact pattern: CLM-XXXX where X is a digit
    # This prevents SQL injection and ensures data integrity
    return bool(re.match(r'^CLM-\d{4}$', claim_id))

# Initialize AWS clients for enterprise data access and AI reasoning
def initialize_aws_clients():
    """Initialize AWS clients with proper error handling and timeouts"""
    try:
        region = config.region
        
        # DynamoDB resource for applicant and claims data access
        dynamodb = boto3.resource('dynamodb', region_name=region)
        
        # S3 client for medical records stored as JSON files
        s3_client = boto3.client('s3', region_name=region)
        
        # Bedrock client for Nova AI reasoning with reasonable timeouts
        bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                connect_timeout=60,    # 1 minute connection timeout
                read_timeout=300,      # 5 minute read timeout for reasoning
                retries={'max_attempts': 2}  # Two retries for reliability
            )
        )
        
        logger.info("AWS clients initialized successfully")
        return dynamodb, s3_client, bedrock_client
        
    except Exception as e:
        logger.error("AWS clients initialization failed - check credentials and permissions")
        return None, None, None

# Initialize clients
dynamodb, s3_client, bedrock_client = initialize_aws_clients()

# Initialize FastMCP server
mcp = FastMCP(host="0.0.0.0", stateless_http=True, json_response=True)

MODEL_ID = config.model_id

async def nova_reasoning_request(prompt: str, system_prompt: str = None) -> dict:
    """Make a reasoning request to Nova with transparent decision process"""
    try:
        if not bedrock_client:
            logger.error("Bedrock client not initialized")
            return {"error": "AI service unavailable"}
            
        # Format user message for Nova conversation API
        messages = [{"role": "user", "content": [{"text": prompt}]}]
        
        # Configure Nova request with reasoning enabled
        request = {
            "modelId": MODEL_ID,  # Amazon Nova model
            "messages": messages,
            "inferenceConfig": {
                "temperature": config.inference_config['temperature'],
                "topP": config.inference_config['topP'],
                "maxTokens": config.inference_config['maxTokens']
            },
            "additionalModelRequestFields": {
                "reasoningConfig": {
                    "type": "enabled",
                    "maxReasoningEffort": config.inference_config['maxReasoningEffort']
                }
            }
        }
        
        # Add system prompt if provided (sets context and role for AI)
        if system_prompt:
            request["system"] = [{"text": system_prompt}]
        
        # Send request to Nova via Bedrock
        response = bedrock_client.converse(**request)
        
        # Extract reasoning process and final response from Nova output
        reasoning_content = ""
        final_response = ""
        
        # Parse response content to separate reasoning from final answer
        for content in response['output']['message']['content']:
            if 'reasoningContent' in content:
                # Step-by-step reasoning process (transparent AI)
                reasoning_content = content['reasoningContent']['reasoningText']['text']
            elif 'text' in content:
                # Final response/recommendation
                final_response = content['text']
        
        logger.info("Nova reasoning request completed successfully")
        
        # Return structured response with reasoning transparency
        return {
            "reasoning": reasoning_content,    # Complete reasoning process
            "response": final_response,       # Final answer/decision
            "usage": response.get('usage', {}) # Token usage for cost tracking
        }
    except bedrock_client.exceptions.ValidationException as e:
        logger.error(f"Invalid request to Bedrock: {type(e).__name__}")
        return {"error": "Invalid AI request"}
    except bedrock_client.exceptions.ThrottlingException as e:
        logger.error(f"Bedrock throttling: {type(e).__name__}")
        return {"error": "AI service temporarily unavailable"}
    except Exception as e:
        logger.error(f"Nova reasoning error: {type(e).__name__}")
        return {"error": "AI processing error"}

def decimal_to_float(obj):
    """Convert Decimal objects to float for JSON serialization"""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: decimal_to_float(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [decimal_to_float(v) for v in obj]
    return obj

def get_applicant_data(applicant_id: str) -> dict:
    """Get applicant data from DynamoDB"""
    try:
        if not dynamodb:
            logger.error("DynamoDB client not initialized")
            return {"error": "Database service unavailable"}
        
        # Input validation for security
        if not validate_applicant_id(applicant_id):
            logger.warning(f"Invalid applicant ID format attempted")
            return {"error": "Invalid applicant ID format"}
        
        table = dynamodb.Table(config.applicants_table)
        response = table.get_item(Key={'applicant_id': applicant_id})
        item = response.get('Item', {})
        
        if not item:
            logger.info(f"Applicant not found: {applicant_id}")
            return {"error": f"Applicant {applicant_id} not found"}
        
        converted_item = decimal_to_float(item)
        logger.info(f"Successfully retrieved applicant data")
        return converted_item
    except dynamodb.meta.client.exceptions.ResourceNotFoundException as e:
        logger.error(f"DynamoDB table not found: {config.applicants_table}")
        return {"error": "Database table not found"}
    except Exception as e:
        logger.error(f"Database access error: {type(e).__name__}")
        return {"error": "Database access error"}

def get_medical_records(applicant_id: str) -> dict:
    """Get medical records from S3"""
    try:
        if not s3_client:
            return {"error": "S3 client not initialized"}
        
        # Input validation for security
        if not validate_applicant_id(applicant_id):
            logger.warning("Invalid applicant ID format attempted")
            return {"error": "Invalid applicant ID format"}
        
        bucket_name = config.s3_bucket_name
        key = f'{config.medical_records_prefix}/{applicant_id}/summary.json'
        
        response = s3_client.get_object(Bucket=bucket_name, Key=key)
        data = json.loads(response['Body'].read())
        converted_data = decimal_to_float(data)
        logger.info("Successfully retrieved medical data")
        return converted_data
    except s3_client.exceptions.NoSuchKey:
        logger.info("Medical records not found for applicant")
        return {"error": "Medical records not found"}
    except s3_client.exceptions.NoSuchBucket:
        logger.error("S3 bucket not found")
        return {"error": "Medical records storage not available"}
    except Exception as e:
        logger.error(f"S3 access error: {type(e).__name__}")
        return {"error": "Medical records access error"}

def get_claim_data(claim_id: str) -> dict:
    """Get claim data from DynamoDB"""
    try:
        if not dynamodb:
            logger.error("DynamoDB client not initialized")
            return {"error": "Database service unavailable"}
            
        # Input validation for security
        if not validate_claim_id(claim_id):
            logger.warning(f"Invalid claim ID format attempted")
            return {"error": "Invalid claim ID format"}
        
        table = dynamodb.Table(config.claims_table)
        response = table.get_item(Key={'claim_id': claim_id})
        item = response.get('Item', {})
        
        if not item:
            logger.info(f"Claim not found: {claim_id}")
            return {"error": f"Claim {claim_id} not found"}
            
        return decimal_to_float(item)
    except dynamodb.meta.client.exceptions.ResourceNotFoundException as e:
        logger.error(f"DynamoDB table not found: {config.claims_table}")
        return {"error": "Database table not found"}
    except Exception as e:
        logger.error(f"Database access error: {type(e).__name__}")
        return {"error": "Database access error"}

@mcp.tool()
async def enterprise_risk_assessment(applicant_id: str) -> dict:
    """Perform enterprise risk assessment using real data and Nova reasoning"""
    try:
        # Get real applicant data
        applicant_data = get_applicant_data(applicant_id)
        if 'error' in applicant_data:
            return {"status": "error", "error": f"Applicant {applicant_id} not found"}
        
        # Get medical records
        medical_data = get_medical_records(applicant_id)
        
        # Prepare comprehensive data for analysis
        comprehensive_data = {
            "applicant": applicant_data,
            "medical": medical_data if 'error' not in medical_data else {}
        }
        
        logger.info(f"Data prepared for risk assessment: {applicant_id}")
        
        system_prompt = """You are an expert insurance underwriter with access to comprehensive applicant data. 
        Analyze all available information to provide a thorough risk assessment."""
        
        prompt = f"""
        Analyze this comprehensive insurance applicant profile:
        
        APPLICANT DATA:
        {json.dumps(comprehensive_data, indent=2)}
        
        This is real data from our enterprise database. Provide detailed analysis.
        
        Provide detailed analysis including:
        1. Overall risk score (0-100)
        2. Key risk factors identified
        3. Risk category (Low/Medium/High)
        4. Premium adjustment recommendation (%)
        5. Specific health concerns
        6. Financial stability assessment
        7. Underwriting recommendation
        """
        
        result = await nova_reasoning_request(prompt, system_prompt)
    
        return {
            "status": "success",
            "data": {
                "applicant_id": applicant_id,
                "reasoning_process": result.get("reasoning", ""),
                "risk_assessment": result.get("response", ""),
                "source_data": comprehensive_data,
                "model_used": MODEL_ID,
                "data_quality": "complete" if applicant_data and 'error' not in applicant_data else "incomplete",
                "timestamp": time.time()
            }
        }
    except Exception as e:
        logger.error(f"Risk assessment error: {type(e).__name__}")
        return {"status": "error", "error": "Risk assessment processing failed"}

@mcp.tool()
async def enterprise_medical_analysis(applicant_id: str) -> dict:
    """Analyze medical records with enterprise data and deep reasoning"""
    
    # Get applicant context first
    applicant_data = get_applicant_data(applicant_id)
    if 'error' in applicant_data:
        return {"status": "error", "error": f"Applicant {applicant_id} not found"}
    
    # Get real medical data
    medical_data = get_medical_records(applicant_id)
    if 'error' in medical_data:
        # If no medical records, analyze based on applicant health data
        medical_data = {
            "source": "applicant_profile",
            "health_conditions": applicant_data.get('health_conditions', []),
            "smoker": applicant_data.get('smoker', False),
            "bmi": applicant_data.get('bmi', 0),
            "family_history": applicant_data.get('family_history', {})
        }
    
    system_prompt = """You are a medical underwriter with expertise in evaluating health risks. 
    Analyze medical records in context of the applicant's profile."""
    
    prompt = f"""
    Analyze these medical records for insurance underwriting:
    
    MEDICAL RECORDS:
    {json.dumps(medical_data, indent=2)}
    
    APPLICANT CONTEXT:
    Age: {applicant_data.get('age', 'Unknown')}
    Gender: {applicant_data.get('gender', 'Unknown')}
    Occupation: {applicant_data.get('occupation', 'Unknown')}
    
    Provide comprehensive analysis:
    1. Medical risk assessment (0-100)
    2. Chronic conditions impact
    3. Treatment compliance evaluation
    4. Family history considerations
    5. Lifestyle factors assessment
    6. Future health risk predictions
    7. Underwriting recommendations
    8. Premium impact assessment
    """
    
    result = await nova_reasoning_request(prompt, system_prompt)
    
    return {
        "status": "success",
        "data": {
            "applicant_id": applicant_id,
            "reasoning_process": result.get("reasoning", ""),
            "medical_analysis": result.get("response", ""),
            "medical_data": medical_data,
            "timestamp": time.time()
        }
    }

@mcp.tool()
async def enterprise_fraud_detection(claim_id: str) -> dict:
    """Detect fraud using real claim data and Nova reasoning"""
    
    # Get real claim data
    claim_data = get_claim_data(claim_id)
    if 'error' in claim_data:
        return {"status": "error", "error": f"Claim {claim_id} not found"}
    
    # Get applicant context
    applicant_data = get_applicant_data(claim_data.get('applicant_id', ''))
    
    system_prompt = """You are a fraud detection specialist with access to comprehensive claim and applicant data. 
    Analyze for potential fraudulent activity using pattern recognition."""
    
    prompt = f"""
    Analyze this insurance claim for potential fraud:
    
    CLAIM DATA:
    {json.dumps(claim_data, indent=2)}
    
    APPLICANT PROFILE:
    {json.dumps(applicant_data, indent=2)}
    
    Evaluate comprehensively:
    1. Claim timing analysis
    2. Amount vs policy limit assessment
    3. Historical pattern analysis
    4. Applicant behavior patterns
    5. Red flags identification
    6. Fraud probability score (0-100)
    7. Investigation priority level
    8. Recommended actions
    """
    
    result = await nova_reasoning_request(prompt, system_prompt)
    
    return {
        "status": "success",
        "data": {
            "claim_id": claim_id,
            "reasoning_process": result.get("reasoning", ""),
            "fraud_analysis": result.get("response", ""),
            "claim_data": claim_data,
            "applicant_data": applicant_data,
            "timestamp": time.time()
        }
    }

@mcp.tool()
async def enterprise_underwriting_decision(applicant_id: str, policy_type: str, coverage_amount: float) -> dict:
    """Make comprehensive underwriting decision using all enterprise data"""
    
    # Get all relevant data
    applicant_data = get_applicant_data(applicant_id)
    if 'error' in applicant_data:
        return {"status": "error", "error": f"Applicant {applicant_id} not found"}
    
    medical_data = get_medical_records(applicant_id)
    
    # Get applicant's claim history with validation
    try:
        if not dynamodb:
            logger.error("DynamoDB client not initialized for claim history")
            claim_history = []
        else:
            claims_table = dynamodb.Table(config.claims_table)
            # Use parameterized query for security - applicant_id already validated
            claims_response = claims_table.scan(
                FilterExpression=boto3.dynamodb.conditions.Attr('applicant_id').eq(applicant_id),
                Limit=100,  # Limit results for performance
                ProjectionExpression='claim_id, claim_amount, claim_type, #status, fraud_indicators',
                ExpressionAttributeNames={'#status': 'status'}  # Handle reserved keywords
            )
            claim_history = [decimal_to_float(item) for item in claims_response.get('Items', [])]
    except Exception as e:
        logger.error("Error getting claim history - database access failed")
        claim_history = []
    
    comprehensive_data = {
        "applicant": applicant_data,
        "medical": medical_data if 'error' not in medical_data else {},
        "claim_history": claim_history,
        "requested_policy": {
            "type": policy_type,
            "coverage_amount": coverage_amount
        }
    }
    
    system_prompt = """You are a senior underwriter making final approval decisions. 
    Consider all available data, risk factors, and company policies to make informed decisions."""
    
    prompt = f"""
    Make a comprehensive underwriting decision for this application:
    
    COMPLETE APPLICATION DATA:
    {json.dumps(comprehensive_data, indent=2)}
    
    Provide detailed decision including:
    1. Final decision (Approve/Decline/Conditional Approval)
    2. Detailed reasoning for the decision
    3. Risk score calculation (0-100)
    4. Premium calculation and adjustments
    5. Conditions or requirements (if applicable)
    6. Coverage modifications (if needed)
    7. Regulatory compliance confirmation
    8. Monitoring recommendations
    """
    
    result = await nova_reasoning_request(prompt, system_prompt)
    
    return {
        "status": "success",
        "data": {
            "applicant_id": applicant_id,
            "policy_type": policy_type,
            "coverage_amount": coverage_amount,
            "reasoning_process": result.get("reasoning", ""),
            "underwriting_decision": result.get("response", ""),
            "source_data": comprehensive_data,
            "decision_date": time.strftime("%Y-%m-%d"),
            "underwriter": "Nova_Enterprise_AI",
            "timestamp": time.time()
        }
    }

@mcp.tool()
async def enterprise_analytics() -> dict:
    """Generate enterprise analytics using real data"""
    
    try:
        # Get applicant statistics with pagination for performance
        applicants_table = dynamodb.Table(config.applicants_table)
        applicants = []
        last_key = None
        
        while True:
            if last_key:
                response = applicants_table.scan(ExclusiveStartKey=last_key, Limit=100)
            else:
                response = applicants_table.scan(Limit=100)
            
            applicants.extend([decimal_to_float(item) for item in response.get('Items', [])])
            
            last_key = response.get('LastEvaluatedKey')
            if not last_key:
                break
        
        # Get claims statistics with pagination for performance
        claims_table = dynamodb.Table(config.claims_table)
        claims = []
        last_key = None
        
        while True:
            if last_key:
                response = claims_table.scan(ExclusiveStartKey=last_key, Limit=100)
            else:
                response = claims_table.scan(Limit=100)
            
            claims.extend([decimal_to_float(item) for item in response.get('Items', [])])
            
            last_key = response.get('LastEvaluatedKey')
            if not last_key:
                break
        
        analytics_data = {
            "total_applicants": len(applicants),
            "total_claims": len(claims),
            "avg_age": sum(int(a.get('age', 0)) for a in applicants) / len(applicants) if applicants else 0,
            "avg_income": sum(int(a.get('income', 0)) for a in applicants) / len(applicants) if applicants else 0,
            "smoker_percentage": len([a for a in applicants if a.get('smoker')]) / len(applicants) * 100 if applicants else 0,
            "avg_claim_amount": sum(int(c.get('claim_amount', 0)) for c in claims) / len(claims) if claims else 0,
            "high_risk_claims": len([c for c in claims if int(c.get('fraud_indicators', 0)) > 3])
        }
        
        system_prompt = """You are a business intelligence analyst. Analyze insurance portfolio data and provide insights."""
        
        prompt = f"""
        Analyze this insurance portfolio data:
        
        PORTFOLIO ANALYTICS:
        {json.dumps(analytics_data, indent=2)}
        
        Provide comprehensive analysis:
        1. Portfolio health assessment
        2. Risk distribution analysis
        3. Claims pattern insights
        4. Fraud risk assessment
        5. Profitability indicators
        6. Market trends identification
        7. Strategic recommendations
        """
        
        result = await nova_reasoning_request(prompt, system_prompt)
        
        return {
            "status": "success",
            "data": {
                "reasoning_process": result.get("reasoning", ""),
                "analytics_report": result.get("response", ""),
                "raw_metrics": analytics_data,
                "timestamp": time.time()
            }
        }
        
    except Exception as e:
        logger.error(f"Analytics error: {type(e).__name__}")
        return {"status": "error", "error": "Analytics processing failed"}

@mcp.tool()
async def health_check() -> dict:
    """Health check for Insurance Underwriting MCP Server"""
    health_status = {
        "status": "healthy",
        "model": MODEL_ID,
        "reasoning_enabled": True,
        "data_sources": {},
        "tools_registered": 6,
        "timestamp": time.time()
    }
    
    errors = []
    
    # Check DynamoDB clients
    if not dynamodb:
        errors.append("DynamoDB client not initialized")
        health_status["data_sources"]["dynamodb"] = "unavailable"
    else:
        # Test applicants table
        try:
            applicants_table = dynamodb.Table(config.applicants_table)
            applicants_desc = applicants_table.meta.client.describe_table(TableName=config.applicants_table)
            health_status["data_sources"]["dynamodb_applicants"] = applicants_desc['Table']['TableStatus']
        except Exception as e:
            errors.append("Applicants table access failed")
            health_status["data_sources"]["dynamodb_applicants"] = "error"
        
        # Test claims table
        try:
            claims_table = dynamodb.Table(config.claims_table)
            claims_desc = claims_table.meta.client.describe_table(TableName=config.claims_table)
            health_status["data_sources"]["dynamodb_claims"] = claims_desc['Table']['TableStatus']
        except Exception as e:
            errors.append("Claims table access failed")
            health_status["data_sources"]["dynamodb_claims"] = "error"
    
    # Check S3 client and bucket
    if not s3_client:
        errors.append("S3 client not initialized")
        health_status["data_sources"]["s3_bucket"] = "unavailable"
    else:
        try:
            s3_client.head_bucket(Bucket=config.s3_bucket_name)
            health_status["data_sources"]["s3_bucket"] = "accessible"
        except Exception as e:
            errors.append("S3 bucket access failed")
            health_status["data_sources"]["s3_bucket"] = "error"
    
    # Check Bedrock client
    if not bedrock_client:
        errors.append("Bedrock client not initialized")
        health_status["bedrock_client"] = "unavailable"
    else:
        health_status["bedrock_client"] = "available"
    
    # Set overall status
    if errors:
        health_status["status"] = "unhealthy"
        health_status["errors"] = errors
    
    return health_status

if __name__ == "__main__":
    print("🚀 Starting Nova Enterprise Insurance MCP Server...")
    mcp.run(transport="streamable-http")