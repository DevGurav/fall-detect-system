#!/usr/bin/env python3
"""
Simple backend testing script to check Fall Detection API status
"""
import requests
import json
import time

def test_backend():
    """Test the Fall Detection API backend"""
    base_url = "https://fall-detection-api-ohqg.onrender.com"
    
    print("=== FALL DETECTION API TEST ===")
    print(f"Testing backend at: {base_url}")
    
    # Test endpoints
    endpoints = [
        "/",
        "/health"
    ]
    
    for endpoint in endpoints:
        print(f"\n--- Testing {endpoint} ---")
        try:
            url = f"{base_url}{endpoint}"
            print(f"Requesting: {url}")
            
            response = requests.get(url, timeout=30)
            print(f"Status Code: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    print("Response (JSON):")
                    print(json.dumps(data, indent=2))
                except:
                    print("Response (Text):")
                    print(response.text[:500])
            else:
                print(f"Error Response: {response.text}")
                
        except requests.exceptions.Timeout:
            print("❌ Request timeout (30s)")
        except requests.exceptions.ConnectionError:
            print("❌ Connection error - server might be down")
        except Exception as e:
            print(f"❌ Error: {e}")
    
    print("\n=== TEST COMPLETE ===")

if __name__ == "__main__":
    test_backend()