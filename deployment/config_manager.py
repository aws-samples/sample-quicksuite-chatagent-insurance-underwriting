#!/usr/bin/env python3
"""
Configuration manager Insurance Underwriting System
"""

import yaml
import os
import sys
import boto3
from pathlib import Path

class EnterpriseConfig:
    def __init__(self, config_path=None):
        if config_path is None:
            # Check multiple locations for config file
            possible_paths = [
                './enterprise_config.yaml',  # Current directory (Docker deployment)
                Path(__file__).parent.parent / "config" / "enterprise_config.yaml"  # Local development
            ]
            
            config_path = None
            for path in possible_paths:
                if os.path.exists(path):
                    config_path = path
                    break
            
            if config_path is None:
                # Use the local development path as default
                project_root = Path(__file__).parent.parent
                config_path = project_root / "config" / "enterprise_config.yaml"
        
        self.config_path = config_path
        self.config = self._load_config()
        self.region = os.getenv('AWS_REGION', self.config.get('aws', {}).get('region', 'us-east-1'))
    
    def _load_config(self):
        """Load configuration from YAML file"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)
                if not config_data:
                    raise ValueError("Configuration file is empty")
                return config_data
        except FileNotFoundError:
            print(f"❌ Error: Config file not found: {self.config_path}")
            print("Ensure config/enterprise_config.yaml exists and is properly configured")
            sys.exit(1)
        except yaml.YAMLError as e:
            print(f"❌ Error: Invalid YAML format in config file: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Error loading configuration: {e}")
            sys.exit(1)

    
    @property
    def applicants_table(self):
        return self.config['dynamodb']['applicants_table']
    
    @property
    def claims_table(self):
        return self.config['dynamodb']['claims_table']
    
    @property
    def s3_bucket_name(self):
        try:
            bucket_base = self.config['s3']['bucket_name']
            
            # Replace {account_id} placeholder with actual AWS account ID
            if '{account_id}' in bucket_base:
                sts = boto3.client('sts', region_name=self.region)
                account_id = sts.get_caller_identity()['Account']
                bucket_base = bucket_base.replace('{account_id}', account_id)
            
            return f"{bucket_base}-{self.region}"
        except Exception as e:
            raise ValueError(f"Failed to construct S3 bucket name: {e}")
    
    @property
    def medical_records_prefix(self):
        return self.config['s3']['medical_records_prefix']
    
    @property
    def mcp_server_name(self):
        return self.config['agentcore']['mcp_server_name']
    
    @property
    def runtime_role_name(self):
        return self.config['agentcore']['runtime_role_name']
    
    @property
    def cognito_user_pool_name(self):
        return self.config['agentcore']['cognito_user_pool_name']
    
    @property
    def oauth_api_identifier(self):
        return self.config['agentcore']['oauth_api_identifier']
    
    @property
    def model_id(self):
        return self.config['nova']['model_id']
    
    @property
    def inference_config(self):
        return self.config['nova']['inference_config']
    
    def check_and_create_resources(self):
        """Check if resources exist, create if they don't"""
        print("🔍 Checking enterprise resource configuration...")
        
        # Check DynamoDB tables
        self._check_dynamodb_tables()
        
        # Check S3 bucket
        self._check_s3_bucket()
        
        print("✅ Enterprise resource configuration validated")
    
    def _check_dynamodb_tables(self):
        """Check and create DynamoDB tables if needed"""
        dynamodb = boto3.resource('dynamodb', region_name=self.region)
        
        tables_to_check = [
            (self.applicants_table, 'applicant_id'),
            (self.claims_table, 'claim_id')
        ]
        
        for table_name, key_name in tables_to_check:
            try:
                table = dynamodb.Table(table_name)
                table.load()
                print(f"✅ DynamoDB table exists: {table_name}")
            except dynamodb.meta.client.exceptions.ResourceNotFoundException:
                try:
                    table = dynamodb.create_table(
                        TableName=table_name,
                        KeySchema=[{'AttributeName': key_name, 'KeyType': 'HASH'}],
                        AttributeDefinitions=[{'AttributeName': key_name, 'AttributeType': 'S'}],
                        BillingMode='PAY_PER_REQUEST'
                    )
                    table.wait_until_exists()
                    print(f"✅ Created DynamoDB table: {table_name}")
                except Exception as e:
                    raise RuntimeError(f"Failed to create DynamoDB table: {type(e).__name__}")
            except Exception as e:
                raise RuntimeError(f"Failed to check DynamoDB table {table_name}: {e}")
    
    def _check_s3_bucket(self):
        """Check and create S3 bucket if needed"""
        s3 = boto3.client('s3', region_name=self.region)
        
        try:
            s3.head_bucket(Bucket=self.s3_bucket_name)
            print(f"✅ S3 bucket exists: {self.s3_bucket_name}")
        except s3.exceptions.ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code in ['404', 'NoSuchBucket']:
                # Bucket doesn't exist, create it
                try:
                    if self.region == 'us-east-1':
                        s3.create_bucket(Bucket=self.s3_bucket_name)
                    else:
                        s3.create_bucket(
                            Bucket=self.s3_bucket_name,
                            CreateBucketConfiguration={'LocationConstraint': self.region}
                        )
                    print(f"✅ Created S3 bucket: {self.s3_bucket_name}")
                except Exception as create_error:
                    raise RuntimeError(f"Failed to create S3 bucket: {type(create_error).__name__}")
            elif error_code == 'AccessDenied':
                raise RuntimeError("Access denied to S3 bucket - check IAM permissions")
            else:
                raise RuntimeError(f"S3 bucket check failed: {error_code}")
        except Exception as e:
            if not isinstance(e, RuntimeError):
                raise RuntimeError("Unexpected error checking S3 bucket configuration")
            raise

# Global config instance
config = EnterpriseConfig()