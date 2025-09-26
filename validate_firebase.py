#!/usr/bin/env python3
"""
Firebase JSON validation script - tests if the service account JSON can be parsed correctly
"""
import json
import os
from dotenv import load_dotenv

def validate_firebase_json():
    """Test parsing of Firebase service account JSON"""
    print("=== FIREBASE JSON VALIDATION ===")
    
    # Load environment variables
    load_dotenv()
    
    # Get the service account key
    service_account_key = os.getenv('FIREBASE_SERVICE_ACCOUNT_KEY')
    
    if not service_account_key:
        print("❌ FIREBASE_SERVICE_ACCOUNT_KEY not found in environment")
        return False
    
    print(f"✓ Environment variable found (length: {len(service_account_key)})")
    
    # Test JSON parsing
    try:
        service_account_info = json.loads(service_account_key)
        print("✓ JSON parsing successful")
        
        # Check required fields
        required_fields = ['type', 'project_id', 'private_key_id', 'client_email', 'private_key', 'client_id', 'auth_uri', 'token_uri']
        missing_fields = [field for field in required_fields if field not in service_account_info]
        
        if missing_fields:
            print(f"❌ Missing required fields: {missing_fields}")
            return False
        
        print("✓ All required fields present")
        
        # Show key information (without sensitive data)
        print(f"Project ID: {service_account_info.get('project_id')}")
        print(f"Client Email: {service_account_info.get('client_email')}")
        print(f"Type: {service_account_info.get('type')}")
        
        return True
        
    except json.JSONDecodeError as e:
        print(f"❌ JSON parsing failed: {e}")
        print("This likely means the JSON has extra quotes or formatting issues")
        
        # Show first and last 100 characters to help debug
        print(f"First 100 chars: {service_account_key[:100]}")
        print(f"Last 100 chars: {service_account_key[-100:]}")
        
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

if __name__ == "__main__":
    validate_firebase_json()