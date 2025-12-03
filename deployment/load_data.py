#!/usr/bin/env python3
"""
Generate synthetic data for Insurance Underwriting MCP Server
"""

import json
import random
import boto3
from datetime import datetime, timedelta
from faker import Faker
from decimal import Decimal
import sys
import os
# config_manager is in the same directory
from config_manager import config

fake = Faker()

def generate_applicants(count=1000):
    """Generate synthetic applicant data for insurance underwriting simulation"""
    applicants = []
    # Define realistic occupation categories for risk assessment
    occupations = ['Software Engineer', 'Teacher', 'Doctor', 'Lawyer', 'Nurse', 'Manager', 'Sales Rep', 'Accountant', 'Engineer', 'Consultant']
    # Health status categories used in underwriting decisions
    health_statuses = ['Excellent', 'Good', 'Fair', 'Poor']
    
    # Generate specified number of synthetic applicant profiles
    for i in range(count):
        # Generate realistic age range for insurance applicants
        age = random.randint(18, 75)
        
        # Create comprehensive applicant profile with all underwriting factors
        applicant = {
            # Unique identifier following enterprise naming convention
            'applicant_id': f'APP-{i+1:04d}',
            # Personal information using Faker for realistic data
            'name': fake.name(),
            'age': age,
            'gender': random.choice(['Male', 'Female']),
            # Occupation affects risk assessment (some jobs are higher risk)
            'occupation': random.choice(occupations),
            # Income range typical for insurance applicants ($30K-$200K)
            'income': random.randint(30000, 200000),
            # Overall health status for initial risk screening
            'health_status': random.choice(health_statuses),
            # Smoking status - major risk factor in insurance underwriting
            'smoker': random.choice([True, False]),
            # BMI range from underweight to obese (18.5-35.0)
            'bmi': Decimal(str(round(random.uniform(18.5, 35.0), 1))),
            # Exercise frequency affects health risk assessment
            'exercise_frequency': random.choice(['Never', 'Rarely', 'Weekly', 'Daily']),
            # Family medical history - genetic risk factors
            'family_history': {
                'heart_disease': random.choice([True, False]),
                'diabetes': random.choice([True, False]),
                'cancer': random.choice([True, False])
            },
            # Claims history affects future risk assessment
            'previous_claims': random.randint(0, 5),
            # Credit score correlates with claim frequency in actuarial data
            'credit_score': random.randint(300, 850),
            # Marital status affects risk profiles
            'marital_status': random.choice(['Single', 'Married', 'Divorced', 'Widowed']),
            # Number of dependents affects coverage needs
            'dependents': random.randint(0, 4),
            # Application date within last 2 years
            'created_date': fake.date_between(start_date='-2y', end_date='today').isoformat()
        }
        applicants.append(applicant)
    
    return applicants

def generate_medical_records(applicants):
    """Generate comprehensive medical records for underwriting analysis"""
    records = []
    # Common chronic conditions that affect insurance risk assessment
    conditions = ['Hypertension', 'Diabetes', 'Asthma', 'Arthritis', 'Depression', 'Anxiety']
    # Medications corresponding to common conditions (affects risk evaluation)
    medications = ['Lisinopril', 'Metformin', 'Albuterol', 'Ibuprofen', 'Sertraline', 'Atorvastatin']
    
    # Generate medical record for each applicant
    for applicant in applicants:
        record = {
            # Link medical record to applicant profile
            'applicant_id': applicant['applicant_id'],
            # Recent medical checkup date (within last year)
            'last_checkup': fake.date_between(start_date='-1y', end_date='today').isoformat(),
            # Vital signs - blood pressure in systolic/diastolic format
            'blood_pressure': f"{random.randint(90, 180)}/{random.randint(60, 120)}",
            # Cholesterol levels (mg/dL) - affects cardiovascular risk
            'cholesterol': Decimal(str(random.randint(150, 300))),
            # Physical measurements for BMI calculation and health assessment
            'weight': Decimal(str(random.randint(120, 300))),  # pounds
            'height': Decimal(str(random.randint(60, 78))),    # inches
            # Chronic conditions (0-3 conditions per person) - major risk factors
            'chronic_conditions': random.sample(conditions, random.randint(0, 3)),
            # Current medications (0-2 medications) - indicates health management
            'medications': random.sample(medications, random.randint(0, 2)),
            # Known allergies - important for medical underwriting
            'allergies': random.sample(['Peanuts', 'Shellfish', 'Penicillin', 'None'], random.randint(0, 2)),
            # Surgical history - indicates past health issues
            'surgeries': random.sample(['Appendectomy', 'Knee Surgery', 'Heart Surgery', 'None'], random.randint(0, 1)),
            # Number of hospitalizations - indicates health complexity
            'hospitalizations': Decimal(str(random.randint(0, 3)))
        }
        records.append(record)
    
    return records

def generate_claims(count=500):
    """Generate synthetic insurance claims data with fraud detection patterns"""
    claims = []
    # Different types of insurance claims for comprehensive fraud analysis
    claim_types = ['Auto Accident', 'Medical', 'Property Damage', 'Life', 'Disability']
    
    # Generate specified number of claims
    for i in range(count):
        # Policy must start before claim can be filed (1-3 years ago)
        policy_start = fake.date_between(start_date='-3y', end_date='-1y')
        # Claim date must be after policy start date
        claim_date = fake.date_between(start_date=policy_start, end_date='today')
        
        claim = {
            # Unique claim identifier following enterprise naming convention
            'claim_id': f'CLM-{i+1:04d}',
            # Link claim to existing applicant (random assignment for simulation)
            'applicant_id': f'APP-{random.randint(1, 1000):04d}',
            # Type of insurance claim affects fraud patterns
            'claim_type': random.choice(claim_types),
            # Claim amount range $1K-$100K (realistic for most claims)
            'claim_amount': Decimal(str(random.randint(1000, 100000))),
            # Policy coverage limit (affects fraud detection when claim approaches limit)
            'policy_limit': Decimal(str(random.randint(50000, 1000000))),
            # Policy inception date
            'policy_start_date': policy_start.isoformat(),
            # Date when claim was filed
            'claim_date': claim_date.isoformat(),
            # Time between policy start and claim (fraud indicator if too short)
            'days_since_policy_start': (claim_date - policy_start).days,
            # Current claim processing status
            'status': random.choice(['Pending', 'Approved', 'Denied', 'Under Investigation']),
            # Claim description for analysis
            'description': fake.text(max_nb_chars=200),
            # Fraud indicator score (0-5, higher = more suspicious)
            'fraud_indicators': random.randint(0, 5)
        }
        claims.append(claim)
    
    return claims

def create_dynamodb_tables():
    """Create DynamoDB tables using configuration"""
    config.check_and_create_resources()

def load_data_to_dynamodb(applicants, claims):
    """Load data to DynamoDB using configuration"""
    try:
        dynamodb = boto3.resource('dynamodb', region_name=config.region)
        
        # Load applicants with error handling
        print(f"📎 Loading {len(applicants)} applicant profiles to {config.applicants_table}...")
        print(f"⏱️  Processing enterprise applicant data (30-45 seconds)")
        applicants_table = dynamodb.Table(config.applicants_table)
        
        # Check if table exists first
        try:
            applicants_table.load()
        except dynamodb.meta.client.exceptions.ResourceNotFoundException:
            raise RuntimeError("Applicants table does not exist")
        except Exception as e:
            raise RuntimeError(f"Database table not accessible: {type(e).__name__}")
            
        with applicants_table.batch_writer() as batch:
            for applicant in applicants:
                batch.put_item(Item=applicant)
        print(f"✅ Successfully loaded {len(applicants)} applicant profiles")
        
        # Load claims with error handling
        print(f"📎 Loading {len(claims)} insurance claims to {config.claims_table}...")
        print(f"⏱️  Processing claims data with fraud indicators (15-20 seconds)")
        claims_table = dynamodb.Table(config.claims_table)
        
        # Check if table exists first
        try:
            claims_table.load()
        except dynamodb.meta.client.exceptions.ResourceNotFoundException:
            raise RuntimeError("Claims table does not exist")
        except Exception as e:
            raise RuntimeError(f"Claims table not accessible: {type(e).__name__}")
            
        with claims_table.batch_writer() as batch:
            for claim in claims:
                batch.put_item(Item=claim)
        print(f"✅ Successfully loaded {len(claims)} insurance claims")
        
    except Exception as e:
        print(f"❌ Error loading data to DynamoDB: {e}")
        raise

def decimal_to_float(obj):
    """Convert Decimal objects to float for JSON serialization"""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: decimal_to_float(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [decimal_to_float(v) for v in obj]
    return obj

def create_s3_bucket_and_upload(medical_records):
    """Create S3 bucket and upload medical records using configuration"""
    s3 = boto3.client('s3', region_name=config.region)
    bucket_name = config.s3_bucket_name
    
    # Create bucket
    try:
        if config.region == 'us-east-1':
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={'LocationConstraint': config.region}
            )
        print(f"✅ Created S3 bucket: {bucket_name}")
    except s3.exceptions.BucketAlreadyExists:
        print(f"ℹ️ S3 bucket {bucket_name} already exists")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f"ℹ️ S3 bucket {bucket_name} already owned by you")
    except Exception as e:
        print(f"❌ Error creating S3 bucket: {type(e).__name__}")
        raise
    
    # Upload medical records with progress tracking
    total_records = len(medical_records)
    print(f"📎 Uploading {total_records} medical records to S3...")
    print(f"⏱️  Estimated time: 1-2 minutes for enterprise data upload")
    print(f"📊 Progress: ", end="", flush=True)
    
    for i, record in enumerate(medical_records, 1):
        key = f"{config.medical_records_prefix}/{record['applicant_id']}/summary.json"
        # Convert Decimal to float for JSON serialization
        json_record = decimal_to_float(record)
        s3.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=json.dumps(json_record),
            ContentType='application/json'
        )
        
        # Progress indicator every 100 records
        if i % 100 == 0 or i == total_records:
            progress = int((i / total_records) * 100)
            print(f"\r📊 Progress: {progress}% ({i}/{total_records} records)", end="", flush=True)
    
    print(f"\n✅ Successfully uploaded {total_records} medical records to S3")
    print(f"📊 Data organized in: s3://{bucket_name}/medical-records/")

def main():
    """Generate and load synthetic data"""
    print("🏢 Generate and load synthetic data for Insurance Underwriting MCP Server")
    print("="*60)
    print("🚀 Initializing synthetic enterprise data for production simulation...")
    
    # Generate data
    print("\n🔄 Data Generation")
    print("📊 Generating 1,000 applicant profiles with demographics and risk factors...")
    applicants = generate_applicants(1000)
    
    print("🏥 Generating comprehensive medical records for underwriting analysis...")
    medical_records = generate_medical_records(applicants)
    
    print("📋 Generating 500 insurance claims with fraud detection patterns...")
    claims = generate_claims(500)
    
    # Create AWS resources
    print("\n🔄 AWS Infrastructure Setup")
    print("🗺️ Provisioning DynamoDB tables")
    create_dynamodb_tables()
    
    print("\n🔄 Data Population (Applicants' profiles and Claims) in DynamoDB")
    load_data_to_dynamodb(applicants, claims)
    
    print("\n🔄 Data Population (Medical records) in S3")
    create_s3_bucket_and_upload(medical_records)
    
    # Save sample data locally (convert Decimal for JSON)
    print("\n🔄 Sample Data Export")
    print("💾 Exporting sample data for development and testing...")
    with open('sample_applicants.json', 'w') as f:
        json.dump(decimal_to_float(applicants[:10]), f, indent=2)
    
    with open('sample_medical_records.json', 'w') as f:
        json.dump(decimal_to_float(medical_records[:10]), f, indent=2)
    print("✅ Sample data files created for local development")
    
    print("\n🎉 Completed synthetic data generation.")
    print("\n📊 DEPLOYMENT SUMMARY:")
    print(f"   • 1,000 synthetic applicant profiles with comprehensive demographics")
    print(f"   • 1,000 medical records with health data and risk indicators")
    print(f"   • 500 insurance claims with fraud detection patterns")
    print("\n🗺️ DATA INFRASTRUCTURE:")
    print(f"   • DynamoDB: {config.applicants_table} (1,000 records)")
    print(f"   • DynamoDB: {config.claims_table} (500 records)")
    print(f"   • Amazon S3: {config.s3_bucket_name}/{config.medical_records_prefix}/")
    print("\n📁 SAMPLE DATA:")
    print("   • sample_applicants.json (10 sample profiles)")
    print("   • sample_medical_records.json (10 sample records)")


if __name__ == "__main__":
    main()