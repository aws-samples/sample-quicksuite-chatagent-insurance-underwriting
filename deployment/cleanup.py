#!/usr/bin/env python3
"""
Cleanup all AWS resources created by Nova Lite 2.0 Enterprise Insurance deployment
"""

import boto3
import json
import sys
import os
import time
from bedrock_agentcore_starter_toolkit import Runtime
# config_manager is in the same directory
from config_manager import config

def get_mcp_server_details():
    """Get details of the specific MCP server to cleanup"""
    try:
        agentcore_client = boto3.client('bedrock-agentcore-control')
        
        # Find the specific MCP server
        next_token = None
        while True:
            response = agentcore_client.list_agent_runtimes(
                nextToken=next_token
            ) if next_token else agentcore_client.list_agent_runtimes()
            
            for runtime in response.get('agentRuntimes', []):
                if runtime['agentRuntimeName'] == config.mcp_server_name:
                    # Get detailed runtime info
                    try:
                        runtime_details = agentcore_client.get_agent_runtime(
                            agentRuntimeId=runtime['agentRuntimeId'],
                            agentRuntimeVersion='1'
                        )
                    except agentcore_client.exceptions.ResourceNotFoundException:
                        continue
                    except Exception as e:
                        logger.error("Error getting runtime details - skipping")
                        continue
                    
                    return {
                        'agent_runtime_id': runtime['agentRuntimeId'],
                        'agent_arn': runtime['agentRuntimeArn'],
                        'execution_role_arn': runtime_details['roleArn'],
                        'execution_role_name': runtime_details['roleArn'].split('/')[-1],
                        'auth_config': runtime_details.get('authorizerConfiguration', {})
                    }
            
            if 'nextToken' not in response:
                break
            next_token = response['nextToken']
        
        return None
        
    except Exception as e:
        print(f"⚠️ Error getting MCP server details: {e}")
        return None

def cleanup_agentcore_runtime(server_details):
    """Delete specific AgentCore runtime and wait for deletion to complete"""
    try:
        if not server_details:
            print("ℹ️ No MCP server found to cleanup")
            return False
            
        print(f"🔄 Cleaning up AgentCore runtime: {server_details['agent_runtime_id']}")
        
        agentcore_client = boto3.client('bedrock-agentcore-control')
        
        response = agentcore_client.delete_agent_runtime(
            agentRuntimeId=server_details['agent_runtime_id']
        )
        
        status = response.get('status', 'UNKNOWN')
        print(f"✅ AgentCore runtime deletion initiated - Status: {status}")
        
        # Wait for deletion to complete with exponential backoff
        print(f"⏳ Waiting for AgentCore runtime deletion to complete...")
        print(f"   This typically takes 3-5 minutes. Please wait...")
        max_attempts = 20  # Reduced attempts
        attempt = 0
        
        while attempt < max_attempts:
            attempt += 1
            # Exponential backoff: start with 5s, increase to 15s
            sleep_time = min(5 + (attempt * 2), 15)
            time.sleep(sleep_time)
            
            try:
                # Try to get the runtime - if it exists, deletion is not complete
                agentcore_client.get_agent_runtime(
                    agentRuntimeId=server_details['agent_runtime_id'],
                    agentRuntimeVersion='1'
                )
                print(f"     Still deleting... ({attempt * 10}s elapsed)", end='\r', flush=True)
            except agentcore_client.exceptions.ResourceNotFoundException:
                # Runtime not found - deletion complete
                print(f"\n   ✅ AgentCore runtime deleted successfully (took {attempt * 10} seconds)")
                return True
            except Exception as e:
                # Other errors - continue waiting
                print(f"     Checking... ({attempt * 10}s elapsed)", end='\r', flush=True)
        
        print(f"\n   ⚠️ Deletion verification timed out after {max_attempts * 10} seconds")
        print(f"   The runtime may still be deleting in the background")
        return False
        
    except Exception as e:
        print(f"⚠️ Error cleaning up AgentCore: {e}")
        return False

def cleanup_cognito_resources():
    """Delete Cognito user pool and related resources"""
    try:
        print("🔄 Cleaning up Cognito resources...")
        
        # Get Cognito config from Secrets Manager
        secrets_client = boto3.client('secretsmanager')
        try:
            secret_response = secrets_client.get_secret_value(
                SecretId=f'{config.mcp_server_name}/cognito/credentials'
            )
            cognito_config = json.loads(secret_response['SecretString'])
            
            # Delete Cognito user pool
            cognito_client = boto3.client('cognito-idp')
            pool_id = cognito_config['pool_id']
            
            # Delete domain first
            try:
                cognito_client.delete_user_pool_domain(
                    Domain=cognito_config['domain_prefix'],
                    UserPoolId=pool_id
                )
                print(f"✅ Cognito domain deleted: {cognito_config['domain_prefix']}")
            except:
                pass
            
            # Delete user pool
            cognito_client.delete_user_pool(UserPoolId=pool_id)
            print(f"✅ Cognito user pool deleted: {pool_id}")
            
        except secrets_client.exceptions.ResourceNotFoundException:
            print("ℹ️ No Cognito resources found")
            
    except Exception as e:
        print(f"⚠️ Error cleaning up Cognito: {e}")

def cleanup_dynamodb_tables():
    """Delete DynamoDB tables"""
    try:
        print("🔄 Cleaning up DynamoDB tables...")
        
        dynamodb = boto3.resource('dynamodb')
        tables_to_delete = [config.applicants_table, config.claims_table]
        
        for table_name in tables_to_delete:
            try:
                table = dynamodb.Table(table_name)
                table.delete()
                table.wait_until_not_exists()
                print(f"✅ DynamoDB table deleted: {table_name}")
            except:
                print(f"ℹ️ Table not found: {table_name}")
                
    except Exception as e:
        print(f"⚠️ Error cleaning up DynamoDB: {e}")

def cleanup_s3_bucket():
    """Delete S3 bucket and all objects"""
    try:
        print("🔄 Cleaning up S3 bucket...")
        
        s3 = boto3.client('s3')
        bucket_name = config.s3_bucket_name
        
        try:
            # Delete all objects first
            response = s3.list_objects_v2(Bucket=bucket_name)
            if 'Contents' in response:
                objects = [{'Key': obj['Key']} for obj in response['Contents']]
                s3.delete_objects(
                    Bucket=bucket_name,
                    Delete={'Objects': objects}
                )
                print(f"✅ Deleted {len(objects)} objects from S3 bucket: {bucket_name}")
            
            # Delete bucket
            s3.delete_bucket(Bucket=bucket_name)
            print(f"✅ S3 bucket deleted: {bucket_name}")
            
        except s3.exceptions.NoSuchBucket:
            print(f"ℹ️ S3 bucket not found: {bucket_name}")
            
    except Exception as e:
        print(f"⚠️ Error cleaning up S3: {e}")

def cleanup_iam_role(server_details):
    """Remove policies and delete the MCP server's AgentCore Runtime Role"""
    try:
        if not server_details:
            print("ℹ️ No AgentCore Runtime Role to cleanup")
            return
            
        print(f"🔄 Cleaning up IAM role: {server_details['execution_role_name']}")
        
        iam = boto3.client('iam')
        role_name = server_details['execution_role_name']
        
        # Remove all inline policies
        try:
            inline_policies = iam.list_role_policies(RoleName=role_name)
            for policy_name in inline_policies.get('PolicyNames', []):
                iam.delete_role_policy(
                    RoleName=role_name,
                    PolicyName=policy_name
                )
                print(f"✅ Removed inline policy {policy_name}")
        except Exception as e:
            print(f"⚠️ Error removing inline policies: {e}")
        
        # Detach all managed policies
        try:
            attached_policies = iam.list_attached_role_policies(RoleName=role_name)
            for policy in attached_policies.get('AttachedPolicies', []):
                iam.detach_role_policy(
                    RoleName=role_name,
                    PolicyArn=policy['PolicyArn']
                )
                print(f"✅ Detached managed policy {policy['PolicyName']}")
        except Exception as e:
            print(f"⚠️ Error detaching managed policies: {e}")
        
        # Delete the role
        try:
            iam.delete_role(RoleName=role_name)
            print(f"✅ IAM role deleted: {role_name}")
        except iam.exceptions.NoSuchEntityException:
            print(f"ℹ️ Role already deleted: {role_name}")
        except Exception as e:
            print(f"⚠️ Error deleting role: {e}")
            
    except Exception as e:
        print(f"⚠️ Error cleaning up IAM role: {e}")



def cleanup_secrets_manager():
    """Delete Secrets Manager secrets"""
    try:
        print("🔄 Cleaning up Secrets Manager...")
        
        secrets_client = boto3.client('secretsmanager')
        secret_name = f'{config.mcp_server_name}/cognito/credentials'
        
        try:
            secrets_client.delete_secret(
                SecretId=secret_name,
                ForceDeleteWithoutRecovery=True
            )
            print(f"✅ Secret deleted successfully")
        except secrets_client.exceptions.ResourceNotFoundException:
            print(f"ℹ️ Secret not found or already deleted")
            
    except Exception as e:
        print(f"⚠️ Error cleaning up Secrets Manager: {e}")

def main():
    """Main cleanup function"""
    print("🗑️ NOVA LITE 2.0 ENTERPRISE CLEANUP")
    print("="*50)
    print(f"🎯 Target MCP Server: {config.mcp_server_name}")
    print(f"🌍 Region: {config.region}")
    print("")
    
    # Get server details first
    print("🔍 Finding MCP server details...")
    server_details = get_mcp_server_details()
    
    if server_details:
        print(f"✅ Found server: {server_details['agent_runtime_id']}")
        print(f"   AgentCore Runtime Role: {server_details['execution_role_name']}")
    else:
        print("⚠️ MCP server not found - will cleanup data resources only")
    
    print("")
    print("📋 The following resources will be deleted:")
    print(f"   • AgentCore runtime: {config.mcp_server_name}")
    print(f"   • Cognito user pool: {config.cognito_user_pool_name}")
    print(f"   • DynamoDB tables: {config.applicants_table}, {config.claims_table}")
    print(f"   • S3 bucket: {config.s3_bucket_name}")
    if server_details:
        print(f"   • IAM role: {server_details['execution_role_name']}")
    print(f"   • Secrets Manager secret: {config.mcp_server_name}/cognito/credentials")
    print("")
    
    # Ask for confirmation
    response = input("⚠️  Do you want to proceed with cleanup? (y/n): ").strip().lower()
    
    if response not in ['y', 'yes']:
        print("\n❌ Cleanup cancelled")
        return
    
    print("\n🔄 Starting cleanup...\n")
    
    # Cleanup in proper order: runtime first, then role after runtime is deleted
    runtime_deleted = cleanup_agentcore_runtime(server_details)
    
    if runtime_deleted:
        cleanup_iam_role(server_details)
    elif server_details:
        print("⚠️ Skipping IAM role cleanup - runtime deletion not confirmed")
        print(f"\n   📝 Manual cleanup required:")
        print(f"   1. Verify AgentCore runtime is deleted: {server_details['agent_runtime_id']}")
        print(f"   2. Then delete IAM role: {server_details['execution_role_arn']}")
        print(f"\n   AWS CLI command:")
        print(f"   aws iam delete-role --role-name {server_details['execution_role_name']}\n")
    
    cleanup_cognito_resources()
    cleanup_dynamodb_tables()
    cleanup_s3_bucket()
    cleanup_secrets_manager()
    
    print("")
    if runtime_deleted or not server_details:
        print("🎉 Cleanup completed successfully!")
    else:
        print("⚠️ Cleanup completed with manual steps required (see above)")

if __name__ == "__main__":
    main()